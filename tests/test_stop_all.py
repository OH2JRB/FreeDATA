"""The Stop button (stop_all): every transmission stops and the transmit queues
are emptied. Queued and transmitting messages and broadcasts are set to
'aborted', which neither the scheduler nor the auto-repeat sends again."""

import base64
import queue
import time
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import stop_all
from freedata_server.arq_session_iss import ARQSessionISS, ISS_State
from freedata_server.context import AppContext
from freedata_server.message_p2p import MessageP2P
from freedata_server.message_system_db_broadcasts import DatabaseManagerBroadcasts
from freedata_server.message_system_db_messages import DatabaseManagerMessages
from freedata_server.norm import norm_transmission_iss
from freedata_server.norm.norm_transmission_iss import NormTransmissionISS


class TestAbortUnsent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.messages = DatabaseManagerMessages(cls.ctx)
        cls.broadcasts = DatabaseManagerBroadcasts(cls.ctx)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def add_message(self, destination, status):
        message = MessageP2P.from_api_params("AA1AAA-1", {"destination": destination, "body": status})
        self.messages.add_message(message.to_dict(), statistics={}, direction="transmit", status=status)
        return message.id

    def status(self, message_id):
        return self.messages.get_message_by_id(message_id)["status"]

    def test_queued_and_transmitting_messages_are_aborted(self):
        queued = self.add_message("ST1AAA-1", "queued")
        transmitting = self.add_message("ST1AAA-1", "transmitting")
        failed = self.add_message("ST1AAA-1", "failed")
        transmitted = self.add_message("ST1AAA-1", "transmitted")

        aborted = self.messages.abort_unsent_messages()

        self.assertIn(queued, aborted)
        self.assertIn(transmitting, aborted)
        self.assertEqual(self.status(queued), "aborted")
        self.assertEqual(self.status(transmitting), "aborted")
        self.assertEqual(self.status(failed), "failed")
        self.assertEqual(self.status(transmitted), "transmitted")
        self.assertIsNone(self.messages.get_first_queued_message())

    def test_auto_repeat_does_not_queue_an_aborted_message(self):
        message_id = self.add_message("ST2AAA-1", "queued")
        self.messages.abort_unsent_messages()
        self.messages.set_message_to_queued_for_callsign("ST2AAA-1")
        self.assertEqual(self.status(message_id), "aborted")

    def add_broadcast(self, msg_id, direction, status):
        now = time.time()
        ok = self.broadcasts.process_broadcast_message(
            id=msg_id,
            origin="AA1AAA-1",
            timestamp=now,
            burst_index=1,
            burst_data=base64.b64encode(b"hello").decode(),
            total_bursts=2,  # incomplete on purpose, no checksum needed
            checksum=None,
            domain="ST3AAA-0",
            msg_type="MESSAGE",
            expires_at=now + 3600,
            nexttransmission_at=now - 1,
            direction=direction,
            status=status,
        )
        self.assertTrue(ok)

    def test_queued_broadcast_is_not_repeated(self):
        self.add_broadcast("stop-bc-transmit", "transmit", "queued")
        self.add_broadcast("stop-bc-receive", "receive", "received")

        aborted = self.broadcasts.abort_queued_broadcasts()

        self.assertIn("stop-bc-transmit", aborted)
        self.assertNotIn("stop-bc-receive", aborted)
        sent = self.broadcasts.get_broadcast_per_id("stop-bc-transmit")
        self.assertEqual(sent["status"], "aborted")
        self.assertEqual(sent["attempts"], self.broadcasts.MAX_ATTEMPTS)
        self.assertEqual(self.broadcasts.get_broadcast_per_id("stop-bc-receive")["status"], "received")
        first = self.broadcasts.get_first_queued_message()
        self.assertTrue(first is None or first.id != "stop-bc-transmit")


def make_ctx():
    state = mock.Mock()
    state.stop_count = 0
    state.p2p_connection_sessions = {}
    rf_modem = types.SimpleNamespace(audio_out_queue=queue.Queue())
    return types.SimpleNamespace(state_manager=state, rf_modem=rf_modem, p2p_data_queue=queue.Queue())


@mock.patch.object(stop_all, "DatabaseManagerBroadcasts")
@mock.patch.object(stop_all, "DatabaseManagerMessages")
class TestStopAll(unittest.TestCase):
    def test_stops_everything(self, messages, broadcasts):
        messages.return_value.abort_unsent_messages.return_value = ["m1", "m2"]
        broadcasts.return_value.abort_queued_broadcasts.return_value = ["b1"]
        ctx = make_ctx()
        ctx.rf_modem.audio_out_queue.put(b"audio")
        ctx.p2p_data_queue.put(b"socket data")
        p2p = mock.Mock(session_id=7, p2p_data_tx_queue=queue.Queue(), p2p_data_rx_queue=queue.Queue())
        p2p.p2p_data_tx_queue.put(b"data")
        ctx.state_manager.p2p_connection_sessions = {7: p2p}

        result = stop_all.stop_all(ctx)

        self.assertEqual(result, {"messages_aborted": 2, "broadcasts_aborted": 1})
        self.assertEqual(ctx.state_manager.stop_count, 1)
        ctx.state_manager.set.assert_any_call("is_beacon_running", False)
        self.assertTrue(ctx.rf_modem.audio_out_queue.empty())
        self.assertTrue(ctx.p2p_data_queue.empty())
        p2p.abort_connection.assert_called_once()
        self.assertTrue(p2p.p2p_data_tx_queue.empty())
        ctx.state_manager.stop_transmission.assert_called_once_with(stop_retries=stop_all.STOP_RETRIES)

    def test_a_failing_p2p_connection_does_not_stop_the_rest(self, messages, broadcasts):
        ctx = make_ctx()
        p2p = mock.Mock(session_id=7, p2p_data_tx_queue=queue.Queue(), p2p_data_rx_queue=queue.Queue())
        p2p.abort_connection.side_effect = RuntimeError("boom")
        ctx.state_manager.p2p_connection_sessions = {7: p2p}

        stop_all.stop_all(ctx)

        ctx.state_manager.stop_transmission.assert_called_once()
        messages.return_value.abort_unsent_messages.assert_called_once()

    def test_without_a_modem(self, messages, broadcasts):
        ctx = make_ctx()
        ctx.rf_modem = None
        stop_all.stop_all(ctx)
        ctx.state_manager.stop_transmission.assert_called_once()


class TestStopRetries(unittest.TestCase):
    def make_iss(self):
        iss = ARQSessionISS.__new__(ARQSessionISS)
        iss.state = ISS_State.ABORTING
        iss.TIMEOUT_STOP_ACK = 0.01
        iss.log = mock.Mock()
        iss.get_signalling_mode = mock.Mock()
        iss.signalling_frames = mock.Mock(side_effect=lambda frame: frame)
        iss.get_response_timeout = mock.Mock(return_value=0.01)
        iss.transmit_frame = mock.Mock()
        iss.transmission_aborted = mock.Mock()
        return iss

    def test_one_stop_frame_when_asked(self):
        iss = self.make_iss()
        iss.transmit_stop_and_wait(b"stop", 1)
        self.assertEqual(iss.transmit_frame.call_count, 1)
        iss.transmission_aborted.assert_called_once()

    def test_retries_stop_by_default(self):
        iss = self.make_iss()
        iss.transmit_stop_and_wait(b"stop")
        self.assertEqual(iss.transmit_frame.call_count, ARQSessionISS.RETRIES_STOP)


class TestNormBurstsStop(unittest.TestCase):
    def test_no_burst_after_the_stop(self):
        norm = NormTransmissionISS.__new__(NormTransmissionISS)
        norm.log = mock.Mock()
        state = types.SimpleNamespace(stop_count=0, channel_busy_condition_codec2=mock.Mock())

        def transmit(*args):
            state.stop_count += 1  # Stop pressed during the first burst

        norm.ctx = types.SimpleNamespace(state_manager=state, rf_modem=mock.Mock())
        norm.ctx.rf_modem.transmit.side_effect = transmit
        with mock.patch.object(norm_transmission_iss.np.random, "randint", return_value=0):
            norm.transmit_bursts([b"1", b"2", b"3"])
        self.assertEqual(norm.ctx.rf_modem.transmit.call_count, 1)


if __name__ == "__main__":
    unittest.main()
