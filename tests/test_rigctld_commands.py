"""rigctld commands from two threads: the radio manager polls while the modem
keys and unkeys. On the air (2026-11-08, IC-7300 at 19200 baud) a poll held
the socket longer than the 1 s an unkey waited, so the unkey was never sent and
the radio stayed keyed; and replies were read for the wrong command ("PKTUSB
3000" as the ALC, the RF power as the tuner state), since two commands could be
on the socket at once and a reply split in two left its rest for the next one.
send_command now holds a lock from sending to the end of the reply, reads
rigctld's extended replies up to their RPRT line, and lets PTT changes go first;
an unkey that cannot be sent is sent again."""

import socket
import threading
import time
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import rigctld

# rigctld's extended replies to the plain commands, as an IC-7300 gives them
REPLIES = {
    "f": [b"get_freq:\n", b"Frequency: 7053000\n", b"RPRT 0\n"],
    "m": [b"get_mode:\nMode: PKTUSB\n", b"Passband: 3000\nRPRT 0\n"],
    "l ALC": [b"get_level: ALC\n0\nRPRT 0\n"],
    "l RFPOWER": [b"get_level: RFPOWER\n0.0470588\nRPRT 0\n"],
    "u TUNER": [b"get_func: TUNER\n1\nRPRT 0\n"],
    "l STRENGTH": [b"get_level: STRENGTH\n-20\nRPRT 0\n"],
    "l SWR": [b"get_level: SWR\n1.000000\nRPRT 0\n"],
    "v": [b"get_vfo:\nRPRT -11\n"],
    "T 0": [b"set_ptt: 0\nRPRT 0\n"],
    "T 1": [b"set_ptt: 1\nRPRT 0\n"],
    "\\chk_vfo": [b"ChkVFO: 0\n"],
}


class FakeRigctld:
    """Answers one client with REPLIES, each part in a send of its own after
    delay seconds (slow_commands: slow seconds before the reply)."""

    def __init__(self, delay=0.01, slow_commands=None, slow=0.0):
        self.delay = delay
        self.slow_commands = slow_commands or set()
        self.slow = slow
        self.received = []
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        try:
            conn, _ = self.server.accept()
            with conn:
                buffer = b""
                while data := conn.recv(1024):
                    buffer += data
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        command = line.decode()[1:]  # without the "+"
                        self.received.append(command)
                        if command in self.slow_commands:
                            time.sleep(self.slow)
                        for part in REPLIES.get(command, [b"x:\nRPRT -1\n"]):
                            time.sleep(self.delay)
                            conn.sendall(part)
        except OSError:
            pass

    def close(self):
        self.server.close()


def make_radio(server):
    """A rigctld radio connected to server, without its connect thread."""
    radio = rigctld.radio.__new__(rigctld.radio)
    radio.ctx = types.SimpleNamespace(state_manager=mock.Mock())
    radio.log = mock.Mock()
    radio.timeout = 3
    radio.ptt_mode = "TX"
    radio.command_lock = threading.Lock()
    radio.priority_waiting = 0
    radio.ptt_release_pending = False
    radio.ptt_lock = threading.RLock()
    radio.poll_errors = {}
    radio.parameters = {"chk_vfo": False, "vfo": False, "ptt": False}
    radio.connection = socket.create_connection(("127.0.0.1", server.port), timeout=3)
    radio.connected = True
    return radio


class TestReplies(unittest.TestCase):
    def setUp(self):
        self.server = FakeRigctld()
        self.radio = make_radio(self.server)
        self.addCleanup(self.server.close)
        self.addCleanup(self.radio.drop_connection)

    def test_values_as_in_plain_mode(self):
        radio = self.radio
        self.assertEqual(radio.send_command("f"), "7053000")
        # a reply in two parts is read whole
        self.assertEqual(radio.send_command("m"), "PKTUSB\n3000")
        self.assertEqual(radio.send_command("l RFPOWER"), "0.0470588")
        self.assertEqual(radio.send_command("\\chk_vfo"), "0")
        self.assertIsNone(radio.send_command("v"))
        self.assertEqual(radio.send_command("v", raw=True), "RPRT -11")
        self.assertEqual(radio.send_command("T 0", raw=True), "RPRT 0")

    def test_poll_reads_each_value_from_its_own_reply(self):
        radio = self.radio
        radio.start_connecting = mock.Mock()
        parameters = radio.get_parameters()
        self.assertEqual(parameters["frequency"], 7053000)
        self.assertEqual((parameters["mode"], parameters["bandwidth"]), ("PKTUSB", 3000))
        self.assertEqual(parameters["alc"], 0.0)
        self.assertEqual(parameters["rf"], 4)
        self.assertTrue(parameters["tuner"])
        self.assertEqual(parameters["strength"], -20)
        radio.log.warning.assert_not_called()


class TestTwoThreads(unittest.TestCase):
    def test_commands_from_two_threads_get_their_own_replies(self):
        server = FakeRigctld(delay=0.005)
        radio = make_radio(server)
        self.addCleanup(server.close)
        self.addCleanup(radio.drop_connection)
        results = {"f": [], "m": []}

        def run(command):
            for _ in range(20):
                try:
                    results[command].append(radio.send_command(command))
                except TimeoutError:
                    pass

        threads = [threading.Thread(target=run, args=(c,)) for c in ("f", "m")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        self.assertTrue(results["f"] and results["m"])
        self.assertEqual(set(results["f"]), {"7053000"})
        self.assertEqual(set(results["m"]), {"PKTUSB\n3000"})


class TestPttDuringSlowPoll(unittest.TestCase):
    def test_unkey_waits_for_a_slow_poll_and_is_sent(self):
        # the radio takes 2 s for the ALC reading: longer than the 1 s an
        # unkey used to wait before giving up without sending it
        server = FakeRigctld(slow_commands={"l ALC"}, slow=2.0)
        radio = make_radio(server)
        self.addCleanup(server.close)
        self.addCleanup(radio.drop_connection)
        poll = threading.Thread(target=radio.send_command, args=("l ALC",))
        poll.start()
        time.sleep(0.2)
        started = time.monotonic()
        self.assertTrue(radio.set_ptt(False))
        self.assertGreater(time.monotonic() - started, 1.5)
        poll.join(5)
        self.assertEqual(server.received, ["l ALC", "T 0"])
        self.assertTrue(radio.connected)
        self.assertFalse(radio.ptt_release_pending)

    def test_polls_give_way_to_a_waiting_ptt_change(self):
        server = FakeRigctld()
        radio = make_radio(server)
        self.addCleanup(server.close)
        self.addCleanup(radio.drop_connection)
        radio.priority_waiting = 1
        with self.assertRaises(TimeoutError):
            radio.send_command("f")
        self.assertEqual(server.received, [])


class TestTimeout(unittest.TestCase):
    def test_a_reply_that_does_not_end_closes_the_connection(self):
        server = FakeRigctld()
        radio = make_radio(server)
        self.addCleanup(server.close)
        radio.timeout = 0.5
        # its reply never reaches the RPRT line: no late part of it may be
        # read for a later command, so the connection goes
        REPLIES["hang"] = [b"get_hang:\n"]
        self.addCleanup(REPLIES.pop, "hang")
        self.assertIsNone(radio.send_command("hang"))
        self.assertFalse(radio.connected)
        self.assertIsNone(radio.connection)
        radio.log.warning.assert_called_once()


class TestPendingRelease(unittest.TestCase):
    def make_radio(self):
        radio = rigctld.radio.__new__(rigctld.radio)
        radio.log = mock.Mock()
        radio.ptt_mode = "TX"
        radio.parameters = {"chk_vfo": False, "vfo": False, "ptt": True}
        radio.ptt_lock = threading.RLock()
        radio.ptt_release_pending = False
        radio.connected = False
        radio.send_command = mock.Mock(return_value="RPRT 0")
        return radio

    def test_unkey_while_disconnected_is_sent_once_connected(self):
        radio = self.make_radio()
        self.assertFalse(radio.set_ptt(False))
        self.assertTrue(radio.ptt_release_pending)
        radio.connected = True
        radio.send_pending_ptt_release()
        radio.send_command.assert_called_once_with("T 0", raw=True, priority=True)
        self.assertFalse(radio.ptt_release_pending)
        radio.send_pending_ptt_release()  # nothing left to send
        radio.send_command.assert_called_once()

    def test_unkey_that_timed_out_stays_pending_and_connected(self):
        radio = self.make_radio()
        radio.connected = True
        radio.send_command.side_effect = TimeoutError("busy")
        self.assertFalse(radio.set_ptt(False))
        self.assertTrue(radio.ptt_release_pending)
        self.assertTrue(radio.connected)

    def test_keying_again_clears_a_pending_release(self):
        radio = self.make_radio()
        radio.set_ptt(False)  # not connected: pending
        radio.connected = True
        self.assertTrue(radio.set_ptt(True))
        self.assertFalse(radio.ptt_release_pending)
        radio.send_pending_ptt_release()
        radio.send_command.assert_called_once_with("T 1", raw=True, priority=True)

    def test_poll_sends_a_pending_release_first(self):
        radio = self.make_radio()
        radio.connected = True
        radio.ptt_release_pending = True
        radio.priority_waiting = 0
        radio.start_connecting = mock.Mock()
        for name in (
            "get_vfo",
            "get_frequency",
            "get_mode_bandwidth",
            "get_alc",
            "get_strength",
            "get_rf",
            "get_tuner",
            "get_swr",
        ):
            setattr(radio, name, mock.Mock())
        radio.get_parameters()
        self.assertEqual(radio.send_command.call_args_list[0], mock.call("T 0", raw=True, priority=True))
        self.assertFalse(radio.ptt_release_pending)


if __name__ == "__main__":
    unittest.main()
