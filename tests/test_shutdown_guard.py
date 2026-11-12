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


class TestRepeatedCtrlC(unittest.TestCase):
    """One Ctrl-C reaches the server twice (the terminal's process group and
    the run script's trap); uvicorn took the second for "force quit" and skipped
    FreeDATA's shutdown (on-air tests S1 and S2, 2026-11-12)."""

    def server(self):
        import uvicorn

        return shutdown_guard.Server(uvicorn.Config(app=None))

    def test_second_sigint_right_after_the_first_shuts_down_normally(self):
        server = self.server()
        with mock.patch.object(shutdown_guard.time, "monotonic", side_effect=[100.0, 100.3]):
            server.handle_exit(signal.SIGINT, None)
            server.handle_exit(signal.SIGINT, None)
        self.assertTrue(server.should_exit)
        self.assertFalse(server.force_exit, "the app's lifespan shutdown must still run")
        # uvicorn raises each captured signal again after shutting down: only once
        self.assertEqual(server._captured_signals, [signal.SIGINT])

    def test_ctrl_c_pressed_again_later_still_forces_the_exit(self):
        server = self.server()
        later = 100.0 + shutdown_guard.Server.DUPLICATE_SIGINT_SECONDS + 1
        with mock.patch.object(shutdown_guard.time, "monotonic", side_effect=[100.0, later]):
            server.handle_exit(signal.SIGINT, None)
            server.handle_exit(signal.SIGINT, None)
        self.assertTrue(server.force_exit)

    def test_sigterm_then_sigint_is_not_a_force_quit(self):
        server = self.server()
        with mock.patch.object(shutdown_guard.time, "monotonic", side_effect=[100.0, 100.5]):
            server.handle_exit(signal.SIGTERM, None)
            server.handle_exit(signal.SIGINT, None)
        self.assertTrue(server.should_exit)
        self.assertFalse(server.force_exit)

    def test_plain_uvicorn_server_forces_the_exit(self):
        # what happened before: the reason for Server
        import uvicorn

        server = uvicorn.Server(uvicorn.Config(app=None))
        server.handle_exit(signal.SIGINT, None)
        server.handle_exit(signal.SIGINT, None)
        self.assertTrue(server.force_exit)


if __name__ == "__main__":
    unittest.main()
