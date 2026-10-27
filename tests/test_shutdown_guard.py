"""The exit guard names threads that would keep the server process alive
(freedata_server/docs/architecture.md, section 1.2)."""

import signal
import threading
import unittest
from unittest import mock

from freedata_server import shutdown_guard


class TestShutdownGuard(unittest.TestCase):
    def setUp(self):
        self.release = threading.Event()

    def start_thread(self, name, daemon):
        thread = threading.Thread(target=self.release.wait, name=name, daemon=daemon)
        thread.start()
        # cleanups run last-in first-out: release the thread, then join it
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.release.set)
        return thread

    def test_stuck_thread_is_named_and_exits_hard(self):
        self.start_thread("stuck worker", daemon=False)
        with (
            mock.patch.object(shutdown_guard.os, "_exit") as exit_mock,
            mock.patch.object(shutdown_guard.logging, "shutdown"),
            mock.patch.object(shutdown_guard, "log") as log_mock,
        ):
            shutdown_guard.exit_if_threads_hang(timeout=0.2)
        exit_mock.assert_called_once_with(1)
        self.assertIn("stuck worker", log_mock.warning.call_args.kwargs["threads"])

    def test_finishing_thread_returns_normally(self):
        thread = self.start_thread("finishing worker", daemon=False)
        threading.Timer(0.1, self.release.set).start()
        with mock.patch.object(shutdown_guard.os, "_exit") as exit_mock:
            shutdown_guard.exit_if_threads_hang(timeout=2.0)
        exit_mock.assert_not_called()
        self.assertFalse(thread.is_alive())

    def test_daemon_threads_are_ignored(self):
        self.start_thread("daemon worker", daemon=True)
        with mock.patch.object(shutdown_guard.os, "_exit") as exit_mock:
            shutdown_guard.exit_if_threads_hang(timeout=0.2)
        exit_mock.assert_not_called()

    def test_sigterm_raises_keyboard_interrupt(self):
        previous = signal.getsignal(signal.SIGTERM)
        self.addCleanup(signal.signal, signal.SIGTERM, previous)
        shutdown_guard.stop_signals_raise_keyboard_interrupt()
        with self.assertRaises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGTERM)


if __name__ == "__main__":
    unittest.main()
