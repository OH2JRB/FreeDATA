"""The API's answer when the channel gate does not let a transmission out, and
messages sent in the background.

- channel_gate.gate_result tells why the gate refused (DROPPED, STOPPED).
- TxCommand keeps that reason (not_sent_reason).
- enqueue_tx_command answers 503 for a channel busy with FreeDATA traffic and
  409 for a Stop: nothing was sent on purpose, it is not a server error (500).
- start_tx_command creates the command (a message is stored then) and runs it
  in the background, so POST /freedata/messages does not wait for the gate.
"""

import asyncio
import threading
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from fastapi import HTTPException

from freedata_server import channel_gate, command
from freedata_server.api import command_helpers


class TestGateResult(unittest.TestCase):
    def test_testmode_and_disabled_are_clear(self):
        ctx = mock.Mock(TESTMODE=True)
        self.assertEqual(channel_gate.gate_result(ctx, "test"), channel_gate.CLEAR)
        self.assertTrue(channel_gate.wait_for_clear_channel(ctx, "test"))

    def test_reason_passed_on(self):
        ctx = mock.Mock(TESTMODE=False)
        for result, sent in (
            (channel_gate.CLEAR, True),
            (channel_gate.SEND_ANYWAY, True),
            (channel_gate.DROPPED, False),
            (channel_gate.STOPPED, False),
        ):
            with (
                self.subTest(result),
                mock.patch.object(channel_gate, "setting", return_value=True),
                mock.patch.object(channel_gate, "_wait", return_value=result),
            ):
                self.assertEqual(channel_gate.gate_result(ctx, "test"), result)
                self.assertIs(channel_gate.wait_for_clear_channel(ctx, "test"), sent)


class TestCommandReason(unittest.TestCase):
    def cmd(self):
        cmd = command.TxCommand.__new__(command.TxCommand)
        cmd.ctx = mock.Mock()
        return cmd

    def test_reason_kept(self):
        for result, expected, reason in (
            (channel_gate.CLEAR, True, None),
            (channel_gate.SEND_ANYWAY, True, None),
            (channel_gate.DROPPED, False, channel_gate.DROPPED),
            (channel_gate.STOPPED, False, channel_gate.STOPPED),
        ):
            with self.subTest(result), mock.patch.object(command.channel_gate, "gate_result", return_value=result):
                cmd = self.cmd()
                self.assertIs(cmd.wait_for_clear_channel(), expected)
                self.assertEqual(cmd.not_sent_reason, reason)


def command_class(run, reason=None, init=None):
    class Command:
        def __init__(self, ctx, params):
            if init:
                init()
            self.not_sent_reason = None

        def get_name(self):
            return "Command"

        def run(self):
            self.not_sent_reason = reason
            return run()

    return Command


class TestEnqueueAnswers(unittest.TestCase):
    def enqueue(self, cls):
        return asyncio.run(command_helpers.enqueue_tx_command(mock.Mock(), cls))

    def status(self, cls):
        with self.assertRaises(HTTPException) as caught:
            self.enqueue(cls)
        return caught.exception.status_code, caught.exception.detail["error"]

    def test_channel_busy_is_503(self):
        code, error = self.status(command_class(lambda: False, channel_gate.DROPPED))
        self.assertEqual(code, 503)
        self.assertIn("busy", error)

    def test_stop_is_409(self):
        code, error = self.status(command_class(lambda: False, channel_gate.STOPPED))
        self.assertEqual(code, 409)
        self.assertIn("stopped", error)

    def test_other_failures_stay_false(self):
        # the handler answers 500 for those, as before
        self.assertFalse(self.enqueue(command_class(lambda: False)))

    def test_success(self):
        self.assertTrue(self.enqueue(command_class(lambda: None)))


class TestStartInBackground(unittest.TestCase):
    def start(self, cls):
        return asyncio.run(command_helpers.start_tx_command(mock.Mock(), cls))

    def test_answers_before_run_ends(self):
        release = threading.Event()
        finished = threading.Event()

        def run():
            release.wait(5)
            finished.set()

        self.assertTrue(self.start(command_class(run)))
        self.assertFalse(finished.is_set())  # still waiting, e.g. for the channel gate
        release.set()
        self.assertTrue(finished.wait(5))

    def test_creation_error_is_false(self):
        def init():
            raise ValueError("bad params")

        self.assertFalse(self.start(command_class(lambda: None, init=init)))

    def test_run_error_is_logged_not_raised(self):
        done = threading.Event()

        def run():
            done.set()
            raise RuntimeError("no modem")

        with mock.patch.object(command_helpers.logger, "error") as error:
            self.assertTrue(self.start(command_class(run)))
            self.assertTrue(done.wait(5))
            for _ in range(50):
                if error.called:
                    break
                threading.Event().wait(0.02)
        error.assert_called_once()

    def test_message_route_uses_it(self):
        import inspect

        from freedata_server.api import freedata

        source = inspect.getsource(freedata.post_freedata_message)
        self.assertIn("start_tx_command", source)
        self.assertNotIn("enqueue_tx_command", source)


if __name__ == "__main__":
    unittest.main()
