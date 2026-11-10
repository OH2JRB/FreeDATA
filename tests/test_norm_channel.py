"""NORM traffic and the channel (found in the on-air NORM tests, 2026-11-10):

- broadcasts, resends, NACKs and repairs keyed up even while an ARQ session or
  P2P connection ran, and collided with it;
- a broadcast waited only 0.5 s for codec2 traffic to end, a NACK not at all
  and without a random delay, so receivers missing the same burst answered at once;
- a repair logged origin=None and "burst 1/1" instead of the burst it repaired;
- received bursts and the scheduler printed debug dumps with print();
- a repair was answered up to 30 attempts while the database stops at 20;
- sending with the modem stopped called None.transmit;
- broadcasts held back by a session went out from both of its stations at
  once when it ended (random delays of 0 and 1 s; the codec2 busy check had
  not yet synced on the other station's burst);
- after that fix the second station still gave up waiting after 5 s and keyed
  up during the first one's last burst (a 3-burst broadcast takes about 17 s).
"""

import base64
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import frame_handler_norm, schedule_manager
from freedata_server.codec2 import FREEDV_MODE
from freedata_server.norm import norm_transmission, norm_transmission_irs
from freedata_server.norm.norm_transmission import NormTransmission
from freedata_server.norm.norm_transmission_irs import NormTransmissionIRS
from freedata_server.norm.norm_transmission_iss import NORM_ISS_State, NormTransmissionISS


class Channel:
    """The state_manager parts NORM sending reads: sessions, Stop, the channel busy event."""

    def __init__(self, sessions=(), free=(), busy_for=0.0, freedata_for=0.0):
        self.sessions = list(sessions)  # is_in_session() answers, in turn; then False
        self.stop_count = 0
        self.free = list(free)  # channel_busy_event.is_set() answers after listening, in turn; then True
        # the channel is busy for busy_for s on a fake clock, with another
        # FreeDATA station heard for the first freedata_for s of it
        self.clock = 1000.0
        self.start = self.clock
        self.busy_for = busy_for
        self.freedata_for = freedata_for
        self.channel_busy_event = mock.Mock()
        self.channel_busy_event.wait.side_effect = self.busy_wait
        self.channel_busy_event.is_set.side_effect = lambda: self.free.pop(0) if self.free else True

    def busy_wait(self, timeout):
        if self.clock - self.start >= self.busy_for:
            return True
        self.clock += timeout
        return False

    def is_receiving_codec2_signal(self):
        return self.clock - self.start < self.freedata_for

    def clocked(self):
        """Runs NORM's time.time() on this channel's fake clock."""
        return mock.patch.object(norm_transmission.time, "time", lambda: self.clock)

    def is_in_session(self):
        return self.sessions.pop(0) if self.sessions else False


def make_iss(channel):
    iss = NormTransmissionISS.__new__(NormTransmissionISS)
    iss.ctx = types.SimpleNamespace(state_manager=channel, rf_modem=mock.Mock())
    iss.logger = mock.Mock()
    iss.log = mock.Mock()
    iss.state = NORM_ISS_State.NEW
    return iss


def logged(iss):
    return [c.args[0] for c in iss.log.call_args_list]


class NoDelays(unittest.TestCase):
    def setUp(self):
        for patch in (
            mock.patch.object(norm_transmission.np.random, "uniform", return_value=0.0),
            mock.patch.object(NormTransmission, "SESSION_POLL", 0.01),
            mock.patch.object(NormTransmission, "LISTEN_TIME", 0.0),
        ):
            patch.start()
            self.addCleanup(patch.stop)


class TestBroadcastAndSessions(NoDelays):
    def test_waits_for_the_session_to_end(self):
        channel = Channel(sessions=[True, True, True])
        iss = make_iss(channel)
        self.assertTrue(iss.transmit_bursts([b"1", b"2"]))
        self.assertEqual(iss.ctx.rf_modem.transmit.call_count, 2)
        self.assertEqual(channel.sessions, [])  # asked until the session was over

    def test_gives_up_when_the_session_lasts(self):
        channel = Channel()
        channel.is_in_session = lambda: True
        iss = make_iss(channel)
        with mock.patch.object(NormTransmission, "SESSION_WAIT", 0.05):
            self.assertFalse(iss.transmit_bursts([b"1", b"2"]))
        iss.ctx.rf_modem.transmit.assert_not_called()
        self.assertIn("Not sent", logged(iss)[-1])

    def test_stops_when_a_session_starts(self):
        channel = Channel()
        iss = make_iss(channel)
        iss.set_burst_labels("burst", [1, 2, 3], 3)
        iss.ctx.rf_modem.transmit.side_effect = lambda *a: channel.sessions.extend([True] * 5)
        self.assertFalse(iss.transmit_bursts([b"1", b"2", b"3"]))
        self.assertEqual(iss.ctx.rf_modem.transmit.call_count, 1)
        self.assertIn("A session started: stopped before burst 2/3", logged(iss))

    def test_waits_for_a_free_channel(self):
        channel = Channel()
        iss = make_iss(channel)
        iss.transmit_bursts([b"1"])
        # channel_busy_event: neither codec2 nor the FFT traffic check reports traffic
        channel.channel_busy_event.wait.assert_called_once_with(NormTransmission.BUSY_POLL)

    def test_random_delay_up_to_five_seconds(self):
        iss = make_iss(Channel())
        iss.transmit_bursts([b"1"])
        norm_transmission.np.random.uniform.assert_called_once_with(0, 5)

    def test_wider_random_delay_after_a_session(self):
        iss = make_iss(Channel(sessions=[True, True]))
        self.assertTrue(iss.transmit_bursts([b"1"]))
        norm_transmission.np.random.uniform.assert_called_once_with(0, NormTransmission.RANDOM_DELAY_AFTER_SESSION)
        self.assertEqual(NormTransmission.RANDOM_DELAY_AFTER_SESSION, 15)

    def test_another_station_starts_while_listening(self):
        # free, but busy again after listening; then free and still free
        channel = Channel(free=[False, True])
        iss = make_iss(channel)
        self.assertTrue(iss.transmit_bursts([b"1"]))
        self.assertEqual(channel.channel_busy_event.wait.call_count, 2)
        iss.logger.info.assert_any_call("[NORM] Another station started: waiting for the channel")
        iss.ctx.rf_modem.transmit.assert_called_once()

    def test_busy_channel_sends_after_the_wait(self):
        # a signal that is not FreeDATA (no codec2 traffic): BUSY_WAIT s, as CQ answers
        channel = Channel(busy_for=60)
        iss = make_iss(channel)
        with channel.clocked():
            self.assertTrue(iss.transmit_bursts([b"1"]))
        self.assertAlmostEqual(channel.clock - channel.start, NormTransmission.BUSY_WAIT, delta=0.3)
        iss.logger.info.assert_any_call("[NORM] Channel still busy after 5s: sending anyway")

    def test_waits_for_another_broadcast_to_finish(self):
        # on the air: another station's 3-burst broadcast, about 17 s
        channel = Channel(busy_for=17, freedata_for=17)
        iss = make_iss(channel)
        with channel.clocked():
            self.assertTrue(iss.transmit_bursts([b"1"]))
        self.assertAlmostEqual(channel.clock - channel.start, 17, delta=0.3)
        iss.logger.info.assert_any_call("[NORM] Another FreeDATA station is sending: waiting for it to finish")
        self.assertNotIn(
            mock.call("[NORM] Channel still busy after 17s: sending anyway"), iss.logger.info.call_args_list
        )

    def test_busy_after_the_broadcast_ends(self):
        # the FFT check stays busy a while after FreeDATA traffic ends: up to
        # FREEDATA_GAP s more, then sent anyway
        channel = Channel(busy_for=60, freedata_for=17)
        iss = make_iss(channel)
        with channel.clocked():
            iss.transmit_bursts([b"1"])
        self.assertAlmostEqual(channel.clock - channel.start, 17 + NormTransmission.FREEDATA_GAP, delta=0.3)

    def test_freedata_wait_has_a_limit(self):
        channel = Channel(busy_for=300, freedata_for=300)
        iss = make_iss(channel)
        with channel.clocked():
            iss.transmit_bursts([b"1"])
        self.assertAlmostEqual(channel.clock - channel.start, NormTransmission.BUSY_WAIT_FREEDATA, delta=0.3)
        iss.logger.info.assert_any_call("[NORM] Channel still busy after 90s: sending anyway")


class TestNack(NoDelays):
    def make(self, channel):
        norm = NormTransmission.__new__(NormTransmission)
        norm.ctx = types.SimpleNamespace(state_manager=channel, rf_modem=mock.Mock())
        norm.logger = mock.Mock()
        norm.frame_factory = mock.Mock()
        norm.frame_factory.build_norm_nack.return_value = b"nack"
        return norm

    def test_not_sent_during_a_session(self):
        channel = Channel()
        channel.is_in_session = lambda: True
        norm = self.make(channel)
        self.assertFalse(norm.create_and_transmit_nack_burst("AA1AAA-0", "id1", [2]))
        norm.ctx.rf_modem.transmit.assert_not_called()

    def test_random_delay_and_busy_wait_first(self):
        channel = Channel()
        norm = self.make(channel)
        self.assertTrue(norm.create_and_transmit_nack_burst("AA1AAA-0", "id1", [2]))
        norm_transmission.np.random.uniform.assert_called_once_with(0, 5)
        channel.channel_busy_event.wait.assert_called_once()
        norm.ctx.rf_modem.transmit.assert_called_once_with(FREEDV_MODE.datac4, 1, 200, b"nack")


class TestScheduler(unittest.TestCase):
    def make(self, in_session):
        sm = schedule_manager.ScheduleManager.__new__(schedule_manager.ScheduleManager)
        sm.log = mock.Mock()
        sm.ctx = types.SimpleNamespace(
            config_manager=types.SimpleNamespace(
                config={"EXP": {"enable_groupchat": True}, "STATION": {"mycall": "AA1AAA", "myssid": 0}}
            ),
            state_manager=types.SimpleNamespace(is_modem_running=True, is_in_session=lambda: in_session),
        )
        return sm

    def test_no_resend_or_nack_during_a_session(self):
        sm = self.make(in_session=True)
        with mock.patch.object(schedule_manager, "DatabaseManagerBroadcasts") as db:
            sm.check_queued_messages()
            sm.check_missing_broadcast_bursts()
        db.assert_not_called()

    def test_no_missing_bursts_print(self):
        sm = self.make(in_session=False)
        with (
            mock.patch.object(schedule_manager, "DatabaseManagerBroadcasts") as db,
            mock.patch("builtins.print") as printed,
        ):
            db.return_value.check_missing_bursts.return_value = None
            sm.check_missing_broadcast_bursts()
        printed.assert_not_called()


class TestRepair(NoDelays):
    BROADCAST = {
        "id": "b794dc6c3e",
        "origin": "AA1AAA-12",
        "domain": "OHTEST-0",
        "gridsquare": "KP20jd",
        "timestamp": 1794288390.0,
        "payload_data": {"final": base64.b64encode(b"x" * 120).decode()},
        "total_bursts": 5,
        "priority": 1,
        "msg_type": "MESSAGE",
        "checksum": "373dca",
        "direction": "transmit",
        "attempts": 0,
    }

    def test_logs_the_repaired_burst(self):
        iss = make_iss(Channel())
        iss.frame_factory = mock.Mock()
        iss.frame_factory.build_norm_repair.return_value = bytes(54)
        bursts = iss.create_repair(self.BROADCAST, [2])
        self.assertEqual(iss.origin, "AA1AAA-12")
        self.assertEqual(iss.domain, "OHTEST-0")
        iss.transmit_bursts(bursts)
        self.assertIn("Transmitted repair burst 2/5 (size", " ".join(logged(iss)))

    def answer(self, attempts):
        handler = frame_handler_norm.NORMFrameHandler.__new__(frame_handler_norm.NORMFrameHandler)
        handler.ctx = mock.Mock()
        handler.logger = mock.Mock()
        frame = {"id": b"b794dc6c3e", "origin": "BB1BBB-13", "burst_numbers": [2]}
        with (
            mock.patch.object(frame_handler_norm, "DatabaseManagerBroadcasts") as db,
            mock.patch.object(frame_handler_norm, "NormTransmissionISS") as iss_class,
            mock.patch.object(frame_handler_norm.threading, "Thread") as thread,
        ):
            db.return_value.MAX_ATTEMPTS = 20
            db.return_value.get_broadcast_per_id.return_value = dict(self.BROADCAST, attempts=attempts)
            handler.answer_nack(frame)
        return db.return_value, iss_class.return_value, thread

    def test_repairs_stop_at_the_database_limit(self):
        db, iss, thread = self.answer(attempts=20)
        db.increment_attempts.assert_not_called()
        iss.create_repair.assert_not_called()
        thread.assert_not_called()

        db, iss, thread = self.answer(attempts=19)
        db.increment_attempts.assert_called_once_with("b794dc6c3e")
        thread.return_value.start.assert_called_once()

    def test_nack_answered_in_a_thread(self):
        db, iss, thread = self.answer(attempts=0)
        iss.create_repair.assert_called_once_with(dict(self.BROADCAST, attempts=0), [2])
        thread.assert_called_once()
        self.assertIs(thread.call_args.kwargs["target"], iss.transmit_bursts)
        thread.return_value.start.assert_called_once()
        iss.transmit_bursts.assert_not_called()  # not on the receive worker


class TestWithoutModem(NoDelays):
    def test_broadcast_not_sent(self):
        iss = make_iss(Channel())
        iss.ctx.rf_modem = None
        self.assertFalse(iss.transmit_bursts([b"1", b"2"]))  # no AttributeError
        self.assertIn("the modem is not running", logged(iss)[-1])

    def test_modem_stops_between_bursts(self):
        iss = make_iss(Channel())
        modem = iss.ctx.rf_modem
        modem.transmit.side_effect = lambda *a: setattr(iss.ctx, "rf_modem", None)
        self.assertFalse(iss.transmit_bursts([b"1", b"2", b"3"]))
        self.assertEqual(modem.transmit.call_count, 1)
        self.assertIn("The modem stopped: stopped before burst 2/3", logged(iss))

    def test_nack_not_sent(self):
        norm = TestNack.make(None, Channel())
        norm.ctx.rf_modem = None
        self.assertFalse(norm.create_and_transmit_nack_burst("AA1AAA-0", "id1", [2]))


class TestReceivedBurstNoPrint(unittest.TestCase):
    def test_no_print(self):
        ctx = types.SimpleNamespace(config_manager=types.SimpleNamespace(config={"EXP": {"enable_groupchat": True}}))
        frame = {
            "flag": 0x89,  # last burst, MESSAGE, priority 1
            "burst_info": 0x11,  # burst 1 of 1
            "payload_size": 5,
            "payload_data": b"hello" + bytes(21),
            "origin": "AA1AAA-12",
            "domain": "OHTEST-0",
            "gridsquare": "KP20jd",
            "checksum": "373dca",
            "timestamp": 1794288390,
        }
        with (
            mock.patch.object(norm_transmission, "DataFrameFactory"),
            mock.patch.object(norm_transmission.structlog, "get_logger"),  # structlog itself prints
            mock.patch.object(norm_transmission_irs, "DatabaseManagerBroadcasts") as db,
            mock.patch("builtins.print") as printed,
        ):
            NormTransmissionIRS(ctx, frame)
        printed.assert_not_called()
        stored = db.return_value.process_broadcast_message.call_args.kwargs
        self.assertEqual(stored["burst_index"], 1)
        self.assertEqual(stored["origin"], "AA1AAA-12")


if __name__ == "__main__":
    unittest.main()
