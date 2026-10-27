"""A stop that comes while a session answers the other station is not lost:
the answer must not set its state over the abort (the ISS's ABORTING, the
IRS's ABORTED)."""

import queue
import threading
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.arq_session_irs import ARQSessionIRS, IRS_State
from freedata_server.arq_session_iss import ARQSessionISS, ISS_State


def make_iss():
    """An ISS in BURST_SENT with only what send_data and abort_transmission need."""
    iss = ARQSessionISS.__new__(ARQSessionISS)
    iss.state_lock = threading.RLock()
    iss.state = ISS_State.BURST_SENT
    iss.id = 1
    iss.dxcall = "AA1AAA-1"
    iss.data = bytes(1000)
    iss.total_length = 1000
    iss.confirmed_bytes = 0
    iss.expected_byte_offset = 100
    iss.speed_level = 0
    iss.frames_per_burst = 1
    iss.fsk = False
    iss.irs_holds = set()
    iss.burst_id = 0
    iss.running_p2p_connection = None
    iss.event_frame_received = threading.Event()
    iss.log = mock.Mock()
    iss.update_histograms = mock.Mock()
    iss.update_speed_level = mock.Mock()
    iss.get_data_payload_size = mock.Mock(return_value=100)
    iss.calculate_session_statistics = mock.Mock(return_value={})
    iss.frame_factory = mock.Mock()
    iss.launch_twr = mock.Mock()
    iss.send_stop = mock.Mock()
    iss.ctx = types.SimpleNamespace(
        event_manager=mock.Mock(),
        state_manager=mock.Mock(**{"isTransmitting.return_value": False}),
        rf_modem=types.SimpleNamespace(audio_out_queue=queue.Queue()),
    )
    return iss


BURST_ACK = {"offset": 100, "flag": {"ABORT": False, "FINAL": False}}


class TestIssAbortDuringAnswer(unittest.TestCase):
    def test_stop_while_a_burst_ack_is_answered(self):
        # the user stops the session after send_data() checked for an abort:
        # what went wrong before, the next burst set BURST_SENT over ABORTING
        iss = make_iss()
        iss.update_histograms.side_effect = lambda *args: iss.abort_transmission(send_stop=True)
        iss.send_data(BURST_ACK)
        self.assertIs(iss.state, ISS_State.ABORTING)
        iss.send_stop.assert_called_once()
        iss.launch_twr.assert_not_called()

    def test_burst_ack_before_the_stop(self):
        iss = make_iss()
        iss.send_data(BURST_ACK)
        iss.launch_twr.assert_called_once()
        self.assertTrue(iss.abort_transmission(send_stop=True))
        self.assertIs(iss.state, ISS_State.ABORTING)

    def test_abort_is_final(self):
        iss = make_iss()
        iss.set_state(ISS_State.ABORTING)
        for state in (ISS_State.OPEN_SENT, ISS_State.INFO_SENT, ISS_State.BURST_SENT, ISS_State.FAILED):
            self.assertFalse(iss.set_state(state))
            self.assertIs(iss.state, ISS_State.ABORTING)
        self.assertTrue(iss.set_state(ISS_State.ABORTED))
        self.assertFalse(iss.set_state(ISS_State.ENDED))
        self.assertIs(iss.state, ISS_State.ABORTED)

    def test_all_data_confirmed_while_aborting(self):
        # the IRS has the data: the session ends as transferred
        iss = make_iss()
        iss.set_state(ISS_State.ABORTING)
        self.assertTrue(iss.set_state(ISS_State.ENDED))

    def test_failure_does_not_replace_the_abort(self):
        iss = make_iss()
        iss.arq_data_type_handler = mock.Mock()
        iss.set_state(ISS_State.ABORTING)
        iss.transmission_failed()
        self.assertIs(iss.state, ISS_State.ABORTING)
        iss.arq_data_type_handler.failed.assert_not_called()


def make_irs():
    """An IRS in BURST_REPLY_SENT with only what send_burst_ack and transmission_aborted need."""
    irs = ARQSessionIRS.__new__(ARQSessionIRS)
    irs.burst_ack_lock = threading.RLock()
    irs.burst_ack_timer = None
    irs.state = IRS_State.BURST_REPLY_SENT
    irs.id = 1
    irs.dxcall = "AA1AAA-1"
    irs.abort = False
    irs.received_data = bytearray(1000)
    irs.received_bytes = 100
    irs.total_length = 1000
    irs.speed_level = 0
    irs.event_frame_received = threading.Event()
    irs.log = mock.Mock()
    irs.all_data_received = mock.Mock(return_value=False)
    irs.calibrate_speed_settings = mock.Mock()
    irs.build_burst_ack = mock.Mock()
    irs.answer_frames = mock.Mock()
    irs.get_signalling_mode = mock.Mock()
    irs.calculate_session_statistics = mock.Mock(return_value={})
    irs.launch_transmit_and_wait = mock.Mock()
    irs.ctx = types.SimpleNamespace(event_manager=mock.Mock(), state_manager=mock.Mock())
    return irs


class TestIrsAbortDuringAnswer(unittest.TestCase):
    def test_stop_while_a_burst_ack_is_sent(self):
        # the API thread aborts after send_burst_ack() checked the state: what
        # went wrong before, BURST_REPLY_SENT replaced ABORTED and the session
        # waited 90 s for the ISS, then failed
        irs = make_irs()
        api = threading.Thread(target=irs.transmission_aborted)

        def abort_meanwhile():
            api.start()
            api.join(0.3)  # blocked on the lock until send_burst_ack() is done

        irs.calibrate_speed_settings.side_effect = abort_meanwhile
        irs.send_burst_ack()
        api.join(5)
        self.assertIs(irs.state, IRS_State.ABORTED)
        irs.ctx.state_manager.setARQ.assert_called_once_with(False)

    def test_no_burst_ack_after_the_stop(self):
        irs = make_irs()
        irs.transmission_aborted()
        irs.send_burst_ack()
        self.assertIs(irs.state, IRS_State.ABORTED)
        irs.launch_transmit_and_wait.assert_not_called()

    def test_no_fsk_answer_after_the_stop(self):
        irs = make_irs()
        irs.transmit_frame = mock.Mock()
        irs.transmission_aborted()
        irs.answer_undecodable_burst()
        self.assertIs(irs.state, IRS_State.ABORTED)
        irs.transmit_frame.assert_not_called()


if __name__ == "__main__":
    unittest.main()
