"""The speed levels of higher code rates (data_ofdm_250_hr, _500_hr, _1700_hr):
the modes themselves, which levels the IRS chooses, how both stations agree to
use them (HIGH_RATE_FLAG in ARQ_SESSION_OPEN), and the fallback to the same
waveform at the lower code rate (docs/arq-sessions.md, "Higher code rates")."""

import configparser
import ctypes
import os
import shutil
import tempfile
import types
import unittest
from unittest import mock

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import codec2
from freedata_server.arq_session import ARQSession
from freedata_server.arq_session_irs import ARQSessionIRS
from freedata_server.arq_session_iss import ARQSessionISS, ISS_State
from freedata_server.config import CONFIG, high_rate_modes_enabled
from freedata_server.data_frame_factory import DataFrameFactory
from freedata_server.modem_frametypes import FRAME_TYPE
from freedata_server.modulator import Modulator

FREEDV_MODE = codec2.FREEDV_MODE
HIGH_RATE = {
    # high-rate mode: (sibling mode, bytes per frame, at least this much more payload)
    FREEDV_MODE.data_ofdm_250_hr: (FREEDV_MODE.data_ofdm_250, 120, 2.0),
    FREEDV_MODE.data_ofdm_500_hr: (FREEDV_MODE.data_ofdm_500, 185, 1.5),
    FREEDV_MODE.data_ofdm_1700_hr: (FREEDV_MODE.data_ofdm_1700, 1211, 2.3),
}


def session_ctx(maximum_bandwidth=0, enable_high_rate=True, busy=None):
    modem = {"maximum_bandwidth": maximum_bandwidth, "enable_fsk": True, "enable_high_rate": enable_high_rate}
    return types.SimpleNamespace(
        config_manager=types.SimpleNamespace(config={"MODEM": modem}),
        state_manager=types.SimpleNamespace(channel_busy_slot=busy or [False] * 5),
    )


def loopback(mode, payload):
    """Modulates one frame of mode and demodulates it without noise; the payload or None."""
    config = {"MODEM": {"tx_delay": 0}, "EXP": {"enable_vhf": False}}
    modulator = Modulator(types.SimpleNamespace(config_manager=types.SimpleNamespace(config=config)))
    modulator.log = mock.Mock()
    audio = np.frombuffer(modulator.create_burst(mode, 1, 500, bytearray(payload)), dtype=np.int16)
    audio = np.concatenate([np.zeros(4000, np.int16), audio, np.zeros(16000, np.int16)])
    rx = codec2.open_instance(mode.value)
    codec2.api.freedv_set_frames_per_burst(rx, 1)
    size = codec2.api.freedv_get_bits_per_modem_frame(rx) // 8
    out = ctypes.create_string_buffer(size)
    pos, nin = 0, codec2.api.freedv_nin(rx)
    while pos + nin <= len(audio):
        chunk = np.ascontiguousarray(audio[pos : pos + nin])
        nbytes = codec2.api.freedv_rawdatarx(rx, out, chunk.ctypes)
        pos += nin
        nin = codec2.api.freedv_nin(rx)
        if nbytes == size:
            return out.raw[: size - 2]
    return None


class TestModes(unittest.TestCase):
    def test_payload_and_frame_length(self):
        for mode, (sibling, size, gain) in HIGH_RATE.items():
            with self.subTest(mode=mode.name):
                self.assertEqual(codec2.get_bytes_per_frame(mode.value), size)
                self.assertGreaterEqual(size / codec2.get_bytes_per_frame(sibling.value), gain)

    def test_same_waveform_other_code_and_unique_word(self):
        for mode, (sibling, _, _) in HIGH_RATE.items():
            with self.subTest(mode=mode.name):
                hr = codec2.ofdm_configurations[mode.value].config.contents
                base = codec2.ofdm_configurations[sibling.value].config.contents
                self.assertEqual((hr.nc, hr.ns, hr.bps, hr.tcp, hr.ts), (base.nc, base.ns, base.bps, base.tcp, base.ts))
                self.assertNotEqual(hr.codename, base.codename)
                hr_uw = list(hr.tx_uw)[: hr.nuwbits]
                self.assertNotEqual(hr_uw, list(base.tx_uw)[: hr.nuwbits])
                self.assertEqual(hr_uw, codec2.high_rate_uw(hr.nuwbits))

    def test_loopback(self):
        rng = np.random.default_rng(1)
        for mode in HIGH_RATE:
            with self.subTest(mode=mode.name):
                size = codec2.get_bytes_per_frame(mode.value) - 2
                payload = rng.integers(0, 256, size, dtype=np.uint8).tobytes()
                self.assertEqual(loopback(mode, payload), payload)

    def test_burst_frame_fills_the_mode(self):
        factory = DataFrameFactory.__new__(DataFrameFactory)
        factory.template_list = {}
        factory._load_arq_templates()
        for mode in HIGH_RATE:
            with self.subTest(mode=mode.name):
                size = factory.get_available_data_payload_for_mode(FRAME_TYPE.ARQ_BURST_FRAME, mode)
                frame = factory.build_arq_burst_frame(mode, 7, 0, bytes(size), 5)
                self.assertEqual(len(frame), codec2.get_bytes_per_frame(mode.value) - 2)


class TestSpeedLevels(unittest.TestCase):
    def setUp(self):
        self.session = ARQSession.__new__(ARQSession)
        self.session.ctx = session_ctx()

    def level(self, snr, bandwidth, high_rate=True, busy=None):
        self.session.ctx = session_ctx(busy=busy)
        return self.session.get_appropriate_speed_level(snr, bandwidth, high_rate=high_rate)

    def test_order_by_payload_rate(self):
        levels = ARQSession.SPEED_LEVEL_DICT
        self.assertEqual(sorted(ARQSession.SPEED_LEVEL_ORDER), sorted(levels))
        for slower, faster in zip(ARQSession.SPEED_LEVEL_ORDER[1:], ARQSession.SPEED_LEVEL_ORDER[2:]):
            rate = {
                level: codec2.get_bytes_per_frame(levels[level]["mode"].value) / levels[level]["duration_per_frame"]
                for level in (slower, faster)
            }
            self.assertLess(rate[slower], rate[faster], (slower, faster))

    def test_fallbacks_are_the_siblings(self):
        for level, details in ARQSession.SPEED_LEVEL_DICT.items():
            if "fallback" in details:
                fallback = ARQSession.SPEED_LEVEL_DICT[details["fallback"]]
                self.assertEqual(details["bandwidth"], fallback["bandwidth"])
                self.assertGreater(details["min_snr"], fallback["min_snr"])

    def test_legacy_levels_keep_their_numbers(self):
        # stations without the high-rate levels send and read these
        modes = {level: details["mode"] for level, details in ARQSession.SPEED_LEVEL_DICT.items() if level <= 4}
        self.assertEqual(
            modes,
            {
                0: FREEDV_MODE.data_fsk4_50,
                1: FREEDV_MODE.datac4,
                2: FREEDV_MODE.data_ofdm_500,
                3: FREEDV_MODE.datac1,
                4: FREEDV_MODE.data_ofdm_2438,
            },
        )

    def test_without_high_rate_as_before(self):
        for snr in (-10, 0, 4, 7, 20):
            for bandwidth in (250, 500, 1700, 2438):
                self.assertLessEqual(self.level(snr, bandwidth, high_rate=False), 4)

    def test_choices(self):
        cases = [
            # snr, bandwidth, level
            (20, 2438, 4),  # data_ofdm_2438 stays the fastest
            (7, 2438, 7),  # 1700_hr between datac1 and 2438
            (4, 2438, 3),  # datac1
            (20, 1700, 7),
            (20, 500, 6),
            (3, 500, 2),
            (20, 250, 5),
            (0, 250, 1),
        ]
        for snr, bandwidth, level in cases:
            with self.subTest(snr=snr, bandwidth=bandwidth):
                self.assertEqual(self.level(snr, bandwidth), level)

    def test_250_hr_only_when_limited_to_250(self):
        for snr in range(-10, 30):
            self.assertNotEqual(self.level(snr, 500), 5)

    def test_busy_slots(self):
        # data_ofdm_2438 needs the outer slots: 1700_hr instead
        self.assertEqual(self.level(20, 2438, busy=[True, False, False, False, False]), 7)


class TestNegotiation(unittest.TestCase):
    def open_frame(self, iss_enabled, bandwidth=2438):
        iss = ARQSessionISS.__new__(ARQSessionISS)
        iss.ctx = session_ctx(bandwidth, iss_enabled)
        iss.ctx.event_manager = mock.Mock()
        iss.id, iss.dxcall, iss.total_length, iss.protocol_version = 9, "AA1AAA-1", 100, 2
        iss.frame_factory = mock.Mock()
        iss.set_state = mock.Mock(return_value=True)
        iss.launch_twr = mock.Mock()
        iss.state = ISS_State.NEW
        iss.start()
        return iss.frame_factory.build_arq_session_open.call_args.args[2]

    def irs_after_open(self, field, irs_enabled=True, irs_bandwidth=2438):
        irs = ARQSessionIRS.__new__(ARQSessionIRS)
        irs.ctx = session_ctx(irs_bandwidth, irs_enabled)
        irs.ctx.event_manager = mock.Mock()
        irs.id, irs.dxcall, irs.version, irs.protocol_version, irs.snr = 9, "AA1AAA-1", 2, 2, 10
        irs.abort = False
        irs.state = mock.Mock()
        irs.fsk = False
        irs.log = mock.Mock()
        irs.frame_factory = mock.Mock()
        irs.launch_transmit_and_wait = mock.Mock()
        irs.set_state = irs.set_state_unless_aborted = mock.Mock()
        irs.send_open_ack({"maximum_bandwidth": field, "protocol_version": 2})
        return irs

    def test_iss_sets_the_flag(self):
        # the FSK bits (FSK on in session_ctx) ride along (test_fsk_negotiation)
        fsk_bits = ARQSession.CAPS_FLAG | ARQSession.FSK_FLAG
        self.assertEqual(self.open_frame(True), 2438 | ARQSession.HIGH_RATE_FLAG | fsk_bits)
        self.assertEqual(self.open_frame(False), 2438 | fsk_bits)

    def test_flag_fits_the_field(self):
        factory = DataFrameFactory.__new__(DataFrameFactory)
        factory.template_list = {}
        factory._load_arq_templates()
        factory.myfullcall = "AA1AAA-1"
        frame = factory.build_arq_session_open("BB1BBB-1", 9, 2438 | ARQSession.HIGH_RATE_FLAG, 2)
        self.assertEqual(len(frame), DataFrameFactory.LENGTH_SIG0_FRAME)

    def test_both_on(self):
        irs = self.irs_after_open(2438 | ARQSession.HIGH_RATE_FLAG)
        self.assertTrue(irs.high_rate)
        self.assertEqual(irs.maximum_bandwidth, 2438)

    def test_bandwidth_without_the_flag(self):
        irs = self.irs_after_open(500 | ARQSession.HIGH_RATE_FLAG)
        self.assertEqual(irs.maximum_bandwidth, 500)

    def test_iss_without_them(self):
        # an older ISS, or one with the switch off
        self.assertFalse(self.irs_after_open(2438).high_rate)

    def test_irs_switch_off(self):
        self.assertFalse(self.irs_after_open(2438 | ARQSession.HIGH_RATE_FLAG, irs_enabled=False).high_rate)

    def test_older_irs_reads_its_own_bandwidth(self):
        # an IRS without them compares the whole field with its own bandwidth
        # and takes its own: the flag makes the field larger than any
        for own in (250, 500, 1700, 2438):
            self.assertGreater(2438 | ARQSession.HIGH_RATE_FLAG, own)
            self.assertGreater(0 | ARQSession.HIGH_RATE_FLAG, own)


class TestIssFallback(unittest.TestCase):
    def test_lost_bursts_fall_back_to_the_sibling_then_datac4(self):
        iss = ARQSessionISS.__new__(ARQSessionISS)
        iss.speed_level = 7
        iss.fsk = False
        iss.probing = False
        iss.state = ISS_State.INFO_SENT
        iss.confirmed_bytes = 0
        iss.ctx = session_ctx()
        iss.log = mock.Mock()
        iss.transmit_frame = mock.Mock()
        iss.send_data = mock.Mock()

        def run():
            event = mock.Mock()
            event.wait.side_effect = [False, False, True]
            with testenv.patch_threading_event("freedata_server.arq_session_iss", event):
                iss.transmit_wait_and_retry(b"frame", 1.0, ARQSessionISS.RETRIES_DATA, "auto", isARQBurst=True)

        run()
        self.assertEqual(iss.speed_level, 3)  # datac1, the same waveform at rate 1/2
        iss.send_data.assert_called_once()
        run()
        self.assertEqual(iss.speed_level, 1)  # then datac4, as before


class TestIrsFallback(unittest.TestCase):
    def make_irs(self, speed_level, failures=(), bandwidth=0):
        failures = list(failures)
        irs = ARQSessionIRS.__new__(ARQSessionIRS)
        irs.fsk = False
        irs.high_rate = True
        irs.high_rate_hold = 0
        irs.last_burst_level = speed_level
        irs.speed_level = irs.previous_speed_level = speed_level
        irs.snr = 7  # between 1700_hr (6) and data_ofdm_2438 (8.5)
        irs.maximum_bandwidth = bandwidth
        irs.step_down_hold = 0
        irs.log = mock.Mock()
        demodulator = mock.Mock()
        demodulator.take_decode_failures.side_effect = lambda modes: failures.pop(0) if failures else 0
        irs.ctx = session_ctx(bandwidth)
        irs.ctx.rf_modem = types.SimpleNamespace(demodulator=demodulator)
        return irs, demodulator

    def test_steady(self):
        irs, _ = self.make_irs(7)
        self.assertEqual(irs.calibrate_speed_settings(), 7)

    def test_decode_failures_step_down_to_the_sibling(self):
        irs, _ = self.make_irs(7, [2])
        self.assertEqual(irs.calibrate_speed_settings(), 3)
        self.assertFalse(irs.fsk)
        for _ in range(ARQSessionIRS.HIGH_RATE_HOLD - 1):
            irs.last_burst_level = 3
            self.assertEqual(irs.calibrate_speed_settings(), 3)
        irs.last_burst_level = 3
        self.assertEqual(irs.calibrate_speed_settings(), 7)

    def test_the_iss_fell_back(self):
        irs, _ = self.make_irs(6, bandwidth=500)
        irs.last_burst_level = 2  # asked for 6, the burst came at 2
        self.assertEqual(irs.calibrate_speed_settings(), 2)
        self.assertEqual(irs.high_rate_hold, ARQSessionIRS.HIGH_RATE_HOLD - 1)

    def test_decodes_the_fallback_mode_too(self):
        irs, demodulator = self.make_irs(7)
        irs.calibrate_speed_settings()
        modes = demodulator.set_decode_mode.call_args.args[0]
        self.assertTrue(modes[FREEDV_MODE.data_ofdm_1700_hr.value])
        self.assertTrue(modes[FREEDV_MODE.datac1.value])

    def test_sibling_failures_do_not_count(self):
        irs, demodulator = self.make_irs(7)
        irs.previous_speed_level = 3
        irs.take_decode_failures()
        self.assertEqual(demodulator.take_decode_failures.call_args.args[0], {FREEDV_MODE.data_ofdm_1700_hr.value})
        irs.last_burst_level = 3
        irs.take_decode_failures()
        self.assertEqual(demodulator.take_decode_failures.call_args.args[0], {FREEDV_MODE.datac1.value})

    def test_other_failures_still_count(self):
        irs, demodulator = self.make_irs(4)
        irs.previous_speed_level = 3
        irs.take_decode_failures()
        self.assertEqual(
            demodulator.take_decode_failures.call_args.args[0],
            {FREEDV_MODE.data_ofdm_2438.value, FREEDV_MODE.datac1.value},
        )

    def test_failures_at_datac4_still_fall_back_to_fsk(self):
        irs, _ = self.make_irs(1, [2], bandwidth=250)
        self.assertEqual(irs.calibrate_speed_settings(), ARQSessionIRS.FSK_LEVEL)
        self.assertTrue(irs.fsk)


class TestConfig(unittest.TestCase):
    def test_on_by_default(self):
        self.assertTrue(high_rate_modes_enabled(types.SimpleNamespace(config_manager=None)))
        self.assertTrue(high_rate_modes_enabled(session_ctx()))
        self.assertFalse(high_rate_modes_enabled(session_ctx(enable_high_rate=False)))

    def test_added_on_to_an_older_config(self):
        tmp = tempfile.mkdtemp()
        try:
            path = os.path.join(tmp, "config.ini")
            shutil.copyfile(testenv.CONFIG_TEMPLATE, path)
            parser = configparser.ConfigParser()
            parser.read(path)
            parser.remove_option("MODEM", "enable_high_rate")
            with open(path, "w") as f:
                parser.write(f)
            config = CONFIG(None, path).read()
            self.assertIs(config["MODEM"]["enable_high_rate"], True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


class TestBusySlots(unittest.TestCase):
    """Busy FFT slots steer the level to narrower modes; a busy centre slot,
    which every mode uses, no longer drops the session to datac4 at good SNR
    (on the air 2026-11-08 at the 500 Hz limit: 6, 1, 6, 1, 6 at 13 dB)."""

    def level(self, snr, bandwidth, busy, high_rate=True):
        session = ARQSession.__new__(ARQSession)
        session.ctx = session_ctx(busy=busy)
        return session.get_appropriate_speed_level(snr, bandwidth, high_rate=high_rate)

    def test_busy_centre_slot_keeps_the_snr_choice(self):
        centre = [False, False, True, False, False]
        self.assertEqual(self.level(13, 500, centre), 6)
        self.assertEqual(self.level(13, 500, centre, high_rate=False), 2)
        self.assertEqual(self.level(20, 2438, centre), 4)

    def test_busy_outer_slots_still_choose_a_narrower_mode(self):
        # 2438 needs the outer slots, datac1 and 1700_hr the inner three
        self.assertEqual(self.level(20, 2438, [True, False, False, False, False]), 7)
        self.assertEqual(self.level(20, 2438, [False, True, False, False, False]), 6)

    def test_low_snr_still_the_lowest_level(self):
        self.assertEqual(self.level(-10, 2438, [False, False, True, False, False]), 1)
