"""rigctld connects in a background thread: creating the radio, and polling it
while it is not connected, return at once, and disconnect() ends the
connection attempts (freedata_server/docs/modem-audio-radio.md, rigctld)."""

import socket
import threading
import time
import types
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import rigctld


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def make_ctx(port):
    return types.SimpleNamespace(
        config_manager=types.SimpleNamespace(
            config={
                "RIGCTLD": {"ip": "127.0.0.1", "port": port},
                "RADIO": {"control": "rigctld", "ptt_mode": "TX"},
            }
        ),
        state_manager=mock.Mock(),
    )


class FakeRigctld:
    """Accepts one client and answers every command with "0"."""

    def __init__(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        try:
            conn, _ = self.server.accept()
            with conn:
                while data := conn.recv(1024):
                    conn.sendall(b"0\n" * data.count(b"\n"))
        except OSError:
            pass

    def close(self):
        self.server.close()


class TestRigctldConnect(unittest.TestCase):
    def make_radio(self, port):
        radio = rigctld.radio(make_ctx(port))
        self.addCleanup(radio.disconnect)
        return radio

    def test_creating_the_radio_does_not_wait_for_rigctld(self):
        started = time.monotonic()
        radio = self.make_radio(free_port())  # nothing listens there
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertTrue(radio.connect_thread.is_alive())
        self.assertFalse(radio.connected)

    def test_polling_while_not_connected_returns_at_once(self):
        radio = self.make_radio(free_port())
        thread = radio.connect_thread
        started = time.monotonic()
        parameters = radio.get_parameters()
        self.assertLess(time.monotonic() - started, 0.5)
        self.assertEqual(parameters["frequency"], "---")
        self.assertIs(radio.connect_thread, thread)  # no second connect thread

    def test_disconnect_ends_the_attempts(self):
        radio = self.make_radio(free_port())
        thread = radio.connect_thread
        time.sleep(0.2)
        radio.disconnect()
        thread.join(2)
        self.assertFalse(thread.is_alive())
        radio.get_parameters()
        self.assertIs(radio.connect_thread, thread)  # not again after disconnect

    def test_connects_in_the_background(self):
        server = FakeRigctld()
        self.addCleanup(server.close)
        radio = self.make_radio(server.port)
        radio.connect_thread.join(10)  # 2 s warm-up after connecting
        self.assertTrue(radio.connected)
        radio.ctx.state_manager.set_radio.assert_any_call("radio_status", True)

    def test_one_connect_at_a_time(self):
        radio = self.make_radio(free_port())
        deadline = time.monotonic() + 1
        while not radio.connect_lock.locked() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(radio.connect_lock.locked())  # held by the connect thread
        started = time.monotonic()
        radio.connect()  # a second connect returns at once
        self.assertLess(time.monotonic() - started, 0.5)


if __name__ == "__main__":
    unittest.main()
