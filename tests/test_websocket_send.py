"""The WebSocket workers send on the server's event loop, where the websockets
live, instead of a new event loop per message (asyncio.run), and with no
client connected they only take the data off the queue."""

import asyncio
import queue
import threading
import time
import unittest
from unittest import mock

import numpy as np

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import websocket_manager


class FakeClient:
    """A websocket that records what it is sent and on which event loop."""

    def __init__(self, fail=False, hang=False):
        self.fail = fail
        self.hang = hang
        self.sent = []
        self.loops = []
        self.closed = threading.Event()

    async def send_text(self, text):
        self.loops.append(asyncio.get_running_loop())
        if self.fail:
            raise ConnectionError("client gone")
        if self.hang:
            await asyncio.sleep(60)
        self.sent.append(text)

    async def close(self):
        self.closed.set()

    async def send_bytes(self, data):
        self.loops.append(asyncio.get_running_loop())
        self.sent.append(data)


class TestWebsocketSend(unittest.TestCase):
    def setUp(self):
        # the server's event loop, running in its own thread as under uvicorn
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.addCleanup(self.stop_loop)
        self.manager = websocket_manager.wsm(mock.Mock())
        self.manager.loop = self.loop

    def stop_loop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(2)
        self.loop.close()

    def run_worker(self, worker, client_list, data):
        """Puts data on a queue and runs worker until the queue is empty."""
        q = queue.Queue()
        q.put(data)
        thread = threading.Thread(target=worker, args=(client_list, q), daemon=True)
        thread.start()
        deadline = time.time() + 5
        while not q.empty() and time.time() < deadline:
            time.sleep(0.01)
        time.sleep(0.1)
        self.manager.shutdown_flag.set()
        thread.join(3)

    def test_sends_on_the_server_loop(self):
        a, b = FakeClient(), FakeClient()
        self.manager.send_to_clients({a, b}, "send_text", '{"x": 1}')
        for client in (a, b):
            self.assertEqual(client.sent, ['{"x": 1}'])
            self.assertEqual(client.loops, [self.loop])

    def test_failing_client_dropped(self):
        good, bad = FakeClient(), FakeClient(fail=True)
        clients = {good, bad}
        self.manager.send_to_clients(clients, "send_text", "hello")
        self.assertEqual(clients, {good})
        self.assertEqual(good.sent, ["hello"])
        # closed, so the browser reconnects instead of waiting for data forever
        self.assertTrue(bad.closed.wait(2))
        self.assertFalse(good.closed.is_set())

    def test_slow_client_dropped_and_closed(self):
        # a background tab or a sleeping computer holds up the send
        self.manager.SEND_TIMEOUT = 0.1
        good, slow = FakeClient(), FakeClient(hang=True)
        clients = {good, slow}
        self.manager.send_to_clients(clients, "send_text", "hello")
        self.assertEqual(clients, {good})
        self.assertTrue(slow.closed.wait(2))
        self.assertEqual(slow.sent, [])

    def test_data_worker_sends_json(self):
        client = FakeClient()
        self.run_worker(self.manager.transmit_sock_data_worker, {client}, {"fft": [1, 2, 3]})
        self.assertEqual(client.sent, ['{"fft": [1, 2, 3]}'])

    def test_no_client_no_serializing(self):
        with mock.patch.object(websocket_manager.json, "dumps") as dumps:
            self.run_worker(self.manager.transmit_sock_data_worker, set(), {"fft": [1, 2, 3]})
        dumps.assert_not_called()

    def test_audio_worker_sends_bytes(self):
        client = FakeClient()
        samples = np.arange(4, dtype=np.int16)
        self.run_worker(self.manager.transmit_sock_audio_worker, {client}, samples)
        self.assertEqual(client.sent, [samples.tobytes()])
        self.assertEqual(client.loops, [self.loop])


class TestAudioRxEndpoint(unittest.TestCase):
    """End to end: a block on audio_rx_queue reaches a browser connected to
    /audio_rx as one binary frame of 8 kHz int16 samples."""

    def test_block_arrives_as_binary_frame(self):
        from types import SimpleNamespace

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from freedata_server.api import websocket as websocket_api
        from freedata_server.context import get_ctx

        ctx = SimpleNamespace(
            modem_events=queue.Queue(),
            state_queue=queue.Queue(),
            modem_fft=queue.Queue(),
            audio_rx_queue=queue.Queue(maxsize=10),
        )
        ctx.websocket_manager = websocket_manager.wsm(ctx)
        app = FastAPI()
        app.include_router(websocket_api.router)
        app.dependency_overrides[get_ctx] = lambda: ctx
        ctx.websocket_manager.startWorkerThreads(app)
        self.addCleanup(ctx.websocket_manager.shutdown)

        block = (np.arange(800) - 400).astype(np.int16)
        with TestClient(app) as client, client.websocket_connect("/audio_rx") as ws:
            deadline = time.time() + 5
            while not ctx.websocket_manager.audio_rx_client_list and time.time() < deadline:
                time.sleep(0.01)
            ctx.audio_rx_queue.put(block)
            data = ws.receive_bytes()
            self.assertEqual(len(data), 1600)
            np.testing.assert_array_equal(np.frombuffer(data, dtype=np.int16), block)


if __name__ == "__main__":
    unittest.main()
