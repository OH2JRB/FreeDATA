"""Both stations say whether they have the FSK modes on, and a session falls
back to FSK only if the other one has them too (or is an older station that
does not say). On the air on 2026-11-08 an ISS with FSK on fell back to FSK
with an IRS that had it off, and went on sending frames the IRS could not hear
until stopped by hand; the next queued message did the same."""

import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.arq_session import ARQSession
from freedata_server.arq_session_irs import ARQSessionIRS
from freedata_server.arq_session_iss import ARQSessionISS, ISS_State
from freedata_server.data_frame_factory import DataFrameFactory
from freedata_server.modem_frametypes import FRAME_TYPE


def ctx(fsk=True):
    modem = {"maximum_bandwidth": 2438, "enable_fsk": fsk, "enable_high_rate": True}
    return types.SimpleNamespace(
        config_manager=types.SimpleNamespace(config={"MODEM": modem}),
        state_manager=types.SimpleNamespace(channel_busy_slot=[False] * 5),
        event_manager=mock.Mock(),
    )


def factory():
    f = DataFrameFactory.__new__(DataFrameFactory)
    f.template_list = {}
    f._load_arq_templates()
    f.myfullcall = "AA1AAA-1"
    return f


class TestOpenAckFlags(unittest.TestCase):
    def flags(self, **kwargs):
        frame = factory().build_arq_session_open_ack(9, "BB1BBB-1", 2, 10, **kwargs)
        return factory().deconstruct(frame)["flag"]

    def test_irs_says_fsk_on_or_off(self):
        self.assertTrue(ARQSession.peer_fsk_from_flags(self.flags(fsk=True)))
        self.assertIs(ARQSession.peer_fsk_from_flags(self.flags(fsk=False)), False)

    def test_older_irs_says_nothing(self):
        self.assertIsNone(ARQSession.peer_fsk_from_flags(self.flags()))

    def test_abort_still_read(self):
        self.assertTrue(self.flags(flag_abort=True, fsk=True)["ABORT"])


class TestBandwidthField(unittest.TestCase):
    def test_iss_bits(self):
        caps, fsk = ARQSession.CAPS_FLAG, ARQSession.FSK_FLAG
        self.assertTrue(ARQSession.peer_fsk_from_bandwidth_field(2438 | caps | fsk))
        self.assertIs(ARQSession.peer_fsk_from_bandwidth_field(2438 | caps), False)
        self.assertIsNone(ARQSession.peer_fsk_from_bandwidth_field(2438))

    def test_bits_fit_and_leave_the_bandwidth(self):
        field = 2438 | ARQSession.CAPABILITY_FLAGS
        self.assertLess(field, 1 << 16)
        self.assertEqual(field & ~ARQSession.CAPABILITY_FLAGS, 2438)


class TestIss(unittest.TestCase):
    def make_iss(self, fsk=True):
        iss = ARQSessionISS.__new__(ARQSessionISS)
        iss.ctx = ctx(fsk)
        iss.id, iss.dxcall, iss.total_length, iss.protocol_version = 9, "BB1BBB-1", 100, 2
        iss.data_crc, iss.snr, iss.type_byte = bytes(4), 10, 0
        iss.speed_level = 1
        iss.fsk = False
        iss.log = mock.Mock()
        iss.frame_factory = mock.Mock()
        iss.set_state = mock.Mock(return_value=True)
        iss.launch_twr = mock.Mock()
        iss.state = ISS_State.NEW
        return iss

    def test_open_carries_this_stations_fsk_setting(self):
        for fsk in (True, False):
            iss = self.make_iss(fsk)
            iss.start()
            field = iss.frame_factory.build_arq_session_open.call_args.args[2]
            self.assertEqual(ARQSession.peer_fsk_from_bandwidth_field(field), fsk)

    def test_no_fallback_when_the_irs_has_fsk_off(self):
        iss = self.make_iss()
        iss.send_info({"flag": {"ABORT": False, "CAPS": True, "FSK": False}})
        self.assertIs(iss.peer_fsk, False)
        iss.fall_back_to_fsk()
        self.assertFalse(iss.fsk)
        self.assertEqual(iss.speed_level, 1)

    def test_fallback_with_an_irs_that_has_fsk(self):
        iss = self.make_iss()
        iss.send_info({"flag": {"ABORT": False, "CAPS": True, "FSK": True}})
        iss.fall_back_to_fsk()
        self.assertTrue(iss.fsk)

    def test_fallback_with_an_older_irs(self):
        iss = self.make_iss()
        iss.send_info({"flag": {"ABORT": False}})  # deconstruct gives no CAPS from an older IRS
        self.assertIsNone(iss.peer_fsk)
        iss.fall_back_to_fsk()
        self.assertTrue(iss.fsk)

    def test_lost_bursts_stay_on_ofdm_with_an_irs_without_fsk(self):
        iss = self.make_iss()
        iss.peer_fsk = False
        iss.state = ISS_State.INFO_SENT
        iss.probing = False
        iss.confirmed_bytes = 0
        iss.transmit_frame = mock.Mock()
        iss.send_data = mock.Mock()
        event = mock.Mock()
        event.wait.side_effect = [False, False, True]
        with testenv.patch_threading_event("freedata_server.arq_session_iss", event):
            iss.transmit_wait_and_retry(b"frame", 1.0, ARQSessionISS.RETRIES_DATA, "auto", isARQBurst=True)
        self.assertFalse(iss.fsk)
        self.assertEqual(iss.speed_level, 1)


class TestIrs(unittest.TestCase):
    def make_irs(self, fsk=True):
        irs = ARQSessionIRS.__new__(ARQSessionIRS)
        irs.ctx = ctx(fsk)
        irs.id, irs.dxcall, irs.version, irs.protocol_version, irs.snr = 9, "AA1AAA-1", 2, 2, 10
        irs.abort = False
        irs.fsk = False
        irs.state = mock.Mock()
        irs.log = mock.Mock()
        irs.frame_factory = mock.Mock()
        irs.launch_transmit_and_wait = mock.Mock()
        irs.set_state = irs.set_state_unless_aborted = mock.Mock()
        return irs

    def test_ack_says_whether_fsk_is_on(self):
        for fsk in (True, False):
            irs = self.make_irs(fsk)
            irs.send_open_ack({"maximum_bandwidth": 2438, "protocol_version": 2})
            self.assertEqual(irs.frame_factory.build_arq_session_open_ack.call_args.kwargs["fsk"], fsk)

    def test_reads_the_iss_setting_and_the_bandwidth(self):
        irs = self.make_irs()
        irs.send_open_ack({"maximum_bandwidth": 500 | ARQSession.CAPS_FLAG, "protocol_version": 2})
        self.assertIs(irs.peer_fsk, False)
        self.assertEqual(irs.maximum_bandwidth, 500)
        irs.fall_back_to_fsk()
        self.assertFalse(irs.fsk)

    def test_older_iss(self):
        irs = self.make_irs()
        irs.send_open_ack({"maximum_bandwidth": 2438, "protocol_version": 2})
        self.assertIsNone(irs.peer_fsk)
        irs.fall_back_to_fsk()
        self.assertTrue(irs.fsk)


class TestFrameTypes(unittest.TestCase):
    def test_open_ack_flags_include_the_new_bits(self):
        self.assertEqual(DataFrameFactory.ARQ_FLAGS["FSK"], 3)
        self.assertEqual(DataFrameFactory.ARQ_FLAGS["CAPS"], 4)
        self.assertIn(FRAME_TYPE.ARQ_SESSION_OPEN_ACK.value, factory().template_list)


if __name__ == "__main__":
    unittest.main()
