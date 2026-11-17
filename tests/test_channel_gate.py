"""Listen before talk (channel_gate): new transmissions wait for a clear channel.

Users reported that FreeDATA easily keyed up over another station's FreeDATA
session: ping, CQ, beacon, test frame and the start of a P2P connection did not
check the channel at all, and ARQ starts and CQ replies sent anyway after 5 s."""

import threading
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import channel_gate
from freedata_server.command import TxCommand
from freedata_server.command_beacon import BeaconCommand
from freedata_server.command_transmit_sine import TransmitSine
from freedata_server.config import CONFIG
from freedata_server.state_manager import StateManager

D = channel_gate.DEFAULTS


class Clock:
    """Fake time: _sleep advances it and runs the scenario's events."""

    def __init__(self):
        self.now = 1000.0
        self.events = []  # (time, callable), in time order

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += max(seconds, 0.001)
        while self.events and self.events[0][0] <= self.now:
            self.events.pop(0)[1]()

    def at(self, offset, action):
        self.events.append((self.now + offset, action))
        self.events.sort(key=lambda e: e[0])


class FakeState:
    def __init__(self, clock):
        self.clock = clock
        self.channel_busy_event = threading.Event()
        self.channel_busy_event.set()  # set == free
        self.codec2 = False
        self.last_codec2_traffic = None
        self.stop_count = 0

    def is_receiving_codec2_signal(self):
        return self.codec2

    def freedata(self, on):
        self.codec2 = on
        if on:
            self.last_codec2_traffic = self.clock.now
            self.channel_busy_event.clear()
        else:
            self.last_codec2_traffic = self.clock.now
            self.channel_busy_event.set()

    def other(self, on):
        if on:
            self.channel_busy_event.clear()
        else:
            self.channel_busy_event.set()


class GateTestCase(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.state = FakeState(self.clock)
        self.ctx = types.SimpleNamespace(state_manager=self.state, TESTMODE=False)
        for p in (
            mock.patch.object(channel_gate, "_now", self.clock),
            mock.patch.object(channel_gate, "_sleep", self.clock.sleep),
            mock.patch.object(channel_gate.random, "uniform", lambda a, b: b),
        ):
            p.start()
            self.addCleanup(p.stop)
        self.start = self.clock.now

    def gate(self, **kwargs):
        return channel_gate.wait_for_clear_channel(self.ctx, "test", **kwargs)

    def elapsed(self):
        return self.clock.now - self.start


class TestWaitForClearChannel(GateTestCase):
    def test_clear_channel_listen_then_random_delay(self):
        self.assertTrue(self.gate())
        expected = D["listen_time"] + D["random_delay"]
        self.assertAlmostEqual(self.elapsed(), expected, delta=0.2)

    def test_waits_while_freedata_heard_and_for_the_holdoff(self):
        self.state.freedata(True)
        self.clock.at(20, lambda: self.state.freedata(False))
        self.assertTrue(self.gate())
        expected = 20 + D["freedata_holdoff"] + D["listen_time"] + D["random_delay"]
        self.assertAlmostEqual(self.elapsed(), expected, delta=0.3)

    def test_holdoff_after_freedata_heard_just_before(self):
        # the other session's ISS went quiet a second ago; its IRS may answer now
        self.state.last_codec2_traffic = self.clock.now - 1
        self.assertTrue(self.gate())
        self.assertGreaterEqual(self.elapsed(), D["freedata_holdoff"] - 1)

    def test_bursts_with_gaps_shorter_than_holdoff_keep_it_waiting(self):
        # a session: 3 s bursts with 4 s gaps (the far end we cannot hear)
        self.state.freedata(True)
        for k in range(5):
            self.clock.at(k * 7 + 3, lambda: self.state.freedata(False))
            self.clock.at(k * 7 + 7, lambda: self.state.freedata(True))
        self.clock.at(5 * 7 + 3, lambda: self.state.freedata(False))
        self.assertTrue(self.gate())
        self.assertGreater(self.elapsed(), 5 * 7 + 3 + D["freedata_holdoff"])

    def test_freedata_that_does_not_end_is_never_sent_over(self):
        self.state.freedata(True)
        self.assertFalse(self.gate())
        self.assertAlmostEqual(self.elapsed(), D["freedata_max_wait"], delta=0.2)

    def test_settings_from_config(self):
        self.ctx.config_manager = types.SimpleNamespace(
            config={"CHANNEL": {"freedata_max_wait": 10, "freedata_holdoff": 3, "listen_time": 1, "random_delay": 0}}
        )
        self.state.freedata(True)
        self.assertFalse(self.gate())
        self.assertAlmostEqual(self.elapsed(), 10, delta=0.2)

        self.start = self.clock.now
        self.state.freedata(False)
        self.assertTrue(self.gate())
        self.assertAlmostEqual(self.elapsed(), 3 + 1, delta=0.3)

    def test_other_setting_names(self):
        self.ctx.config_manager = types.SimpleNamespace(config={"CHANNEL": {"cq_reply_max_wait": 7}})
        self.state.freedata(True)
        self.assertFalse(self.gate(max_wait_setting="cq_reply_max_wait"))
        self.assertAlmostEqual(self.elapsed(), 7, delta=0.2)

    def test_switched_off_sends_at_once(self):
        self.ctx.config_manager = types.SimpleNamespace(config={"CHANNEL": {"enable_listen_before_talk": False}})
        self.state.freedata(True)
        self.assertTrue(self.gate())
        self.assertEqual(self.elapsed(), 0)

    def test_other_signals_held_then_sent_anyway(self):
        self.state.other(True)
        self.assertTrue(self.gate())
        self.assertAlmostEqual(self.elapsed(), D["other_busy_wait"], delta=0.2)

    def test_other_signals_counted_from_the_end_of_freedata_traffic(self):
        # on the air (G2): a 25 s session, then the channel stayed busy from
        # other signals; the ping went out 17 s after the session, as the
        # 30 s had counted from the start of the wait
        self.state.freedata(True)

        def session_ends():
            self.state.freedata(False)
            self.state.other(True)

        self.clock.at(25, session_ends)
        self.assertTrue(self.gate())
        self.assertAlmostEqual(self.elapsed(), 25 + D["freedata_holdoff"] + D["other_busy_wait"], delta=0.3)

    def test_other_signal_that_ends_waits_for_listen_time(self):
        self.state.other(True)
        self.clock.at(5, lambda: self.state.other(False))
        self.assertTrue(self.gate())
        expected = 5 + D["listen_time"] + D["random_delay"]
        self.assertAlmostEqual(self.elapsed(), expected, delta=0.3)

    def test_station_keying_up_during_random_delay_restarts_listening(self):
        delay_starts = D["listen_time"]
        self.clock.at(delay_starts + 1, lambda: self.state.freedata(True))
        self.clock.at(delay_starts + 4, lambda: self.state.freedata(False))
        self.assertTrue(self.gate())
        self.assertGreater(self.elapsed(), delay_starts + 4 + D["freedata_holdoff"])

    def test_stop_ends_the_wait(self):
        self.state.freedata(True)

        def stop():
            self.state.stop_count += 1

        self.clock.at(5, stop)
        self.assertFalse(self.gate())
        self.assertLess(self.elapsed(), 6)

    def test_stop_during_random_delay(self):
        def stop():
            self.state.stop_count += 1

        self.clock.at(D["listen_time"] + 1, stop)
        self.assertFalse(self.gate())

    def test_testmode_does_not_wait(self):
        self.ctx.TESTMODE = True
        self.state.freedata(True)
        self.assertTrue(self.gate())
        self.assertEqual(self.elapsed(), 0)


class Command(TxCommand):
    def build_frame(self):
        return b"frame"


class TestCommandsUseTheGate(GateTestCase):
    def make(self, cls):
        cmd = cls.__new__(cls)
        cmd.ctx = self.ctx
        cmd.logger = mock.Mock()
        self.ctx.rf_modem = mock.Mock()
        self.ctx.config_manager = types.SimpleNamespace(config={"EXP": {}})
        return cmd

    def test_command_sent_on_clear_channel(self):
        cmd = self.make(Command)
        cmd.run()
        self.ctx.rf_modem.transmit.assert_called_once()

    def test_command_not_sent_over_freedata(self):
        self.state.freedata(True)
        cmd = self.make(Command)
        self.assertFalse(cmd.run())
        self.ctx.rf_modem.transmit.assert_not_called()

    def test_beacon_gives_up_sooner(self):
        self.state.freedata(True)
        cmd = self.make(BeaconCommand)
        self.ctx.state_manager.is_away_from_key = False
        self.assertFalse(cmd.run())
        self.assertAlmostEqual(self.elapsed(), D["beacon_max_wait"], delta=0.2)

    def test_tune_tone_does_not_wait(self):
        self.state.freedata(True)
        cmd = self.make(TransmitSine)
        cmd.run()
        self.ctx.rf_modem.transmit_sine.assert_called_once()
        self.assertEqual(self.elapsed(), 0)


class TestChannelConfig(unittest.TestCase):
    def test_defaults_agree(self):
        # the gate's fallbacks, the values added to an older config and the example config
        for name, value in D.items():
            self.assertEqual(CONFIG.config_types["CHANNEL"][name], type(value), name)
            self.assertEqual(CONFIG.setting_defaults[("CHANNEL", name)], str(value), name)
        import configparser

        example = configparser.ConfigParser()
        example.read(testenv.CONFIG_TEMPLATE)
        self.assertEqual(set(example["CHANNEL"]), set(D))
        for name, value in D.items():
            self.assertEqual(example["CHANNEL"][name], str(value), name)

    def test_older_config_gets_the_section(self):
        import configparser
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.ini")
            parser = configparser.ConfigParser()
            parser.read(testenv.CONFIG_TEMPLATE)
            parser.remove_section("CHANNEL")
            with open(path, "w") as fh:
                parser.write(fh)
            config = CONFIG(mock.Mock(), path).read()
        self.assertEqual(config["CHANNEL"], D)


class TestStateManagerRecordsFreedataTraffic(unittest.TestCase):
    def test_last_codec2_traffic(self):
        sm = StateManager(mock.Mock())
        self.assertIsNone(sm.last_codec2_traffic)
        with mock.patch("freedata_server.state_manager.time.monotonic", return_value=42.0):
            sm.set_channel_busy_condition_codec2(True)
        self.assertEqual(sm.last_codec2_traffic, 42.0)
        sm.set_channel_busy_condition_codec2(False)
        self.assertEqual(sm.last_codec2_traffic, 42.0)


if __name__ == "__main__":
    unittest.main()
