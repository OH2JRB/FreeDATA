"""The message queue tab: the queue in sending order, deleting a queued message,
and moving one behind all others (send_last)."""

import time
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.api import freedata
from freedata_server.context import AppContext
from freedata_server.message_p2p import MessageP2P
from freedata_server.message_system_db_messages import DatabaseManagerMessages


class TestMessageQueue(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.db = DatabaseManagerMessages(cls.ctx)
        app = FastAPI()
        app.include_router(freedata.router, prefix="/freedata")
        app.state.ctx = cls.ctx
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def setUp(self):
        # the database is shared by the whole test run: start from an empty queue
        self.db.abort_unsent_messages()

    def add(self, status="queued", body="hello"):
        time.sleep(0.002)  # the id, and so the timestamp, has microseconds
        message = MessageP2P.from_api_params("AA1AAA-1", {"destination": "QU1AAA-1", "body": body})
        self.db.add_message(message.to_dict(), statistics={}, direction="transmit", status=status)
        return message.id

    def queue_ids(self):
        return [m["id"] for m in self.db.get_queue()]

    def test_queue_in_sending_order(self):
        a, b = self.add(), self.add()
        sending = self.add(status="transmitting")
        queue = self.db.get_queue()
        self.assertEqual([m["id"] for m in queue], [sending, a, b])
        self.assertEqual([m["position"] for m in queue], [1, 2, 3])
        self.assertEqual(queue[0]["status"], "transmitting")
        self.assertEqual(self.db.get_first_queued_message()["id"], a)

    def test_send_last(self):
        a, b, c = self.add(), self.add(), self.add()
        self.assertEqual(self.db.send_last(a)["status"], "success")
        self.assertEqual(self.queue_ids(), [b, c, a])
        self.assertEqual(self.db.get_first_queued_message()["id"], b)
        # it stays behind messages queued later as well
        d = self.add()
        self.assertEqual(self.queue_ids(), [b, c, d, a])
        # moving the last one again keeps it last
        self.db.send_last(a)
        self.assertEqual(self.queue_ids(), [b, c, d, a])

    def test_send_last_only_for_queued(self):
        sending = self.add(status="transmitting")
        self.assertEqual(self.db.send_last(sending)["status"], "failure")
        self.assertEqual(self.db.send_last("no-such-message")["message"], "Message not found")

    def test_retransmit_puts_it_back_in_place(self):
        a, b = self.add(), self.add()
        self.db.send_last(a)
        response = self.client.patch(f"/freedata/messages/{a}", json={"action": "retransmit"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.queue_ids(), [a, b])

    def test_api_get_queue(self):
        a = self.add()
        response = self.client.get("/freedata/queue")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["total_messages"], 1)
        message = response.json()["messages"][0]
        self.assertEqual(message["id"], a)
        self.assertEqual(message["destination"], "QU1AAA-1")
        self.assertEqual(message["attachments"], [])

    def test_api_delete_queued(self):
        a = self.add()
        self.assertEqual(self.client.delete(f"/freedata/queue/{a}").status_code, 200)
        self.assertIsNone(self.db.get_message_by_id(a))
        self.assertEqual(self.client.delete(f"/freedata/queue/{a}").status_code, 404)

    def test_api_delete_refused_while_sending(self):
        sending = self.add(status="transmitting")
        self.assertEqual(self.client.delete(f"/freedata/queue/{sending}").status_code, 409)
        # claimed by SendMessageCommand, still waiting for a free channel
        a = self.add()
        self.assertTrue(self.ctx.state_manager.claim_message_transmit(a))
        try:
            self.assertEqual(self.client.delete(f"/freedata/queue/{a}").status_code, 409)
        finally:
            self.ctx.state_manager.release_message_transmit(a)
        self.assertIsNotNone(self.db.get_message_by_id(a))

    def test_chat_delete_still_refuses_queued(self):
        a = self.add()
        self.assertEqual(self.client.delete(f"/freedata/messages/{a}").status_code, 409)

    def test_api_send_last(self):
        a, b = self.add(), self.add()
        self.assertEqual(self.client.patch(f"/freedata/queue/{a}", json={"action": "send_last"}).status_code, 200)
        self.assertEqual(self.queue_ids(), [b, a])
        self.assertEqual(self.client.patch(f"/freedata/queue/{a}", json={"action": "other"}).status_code, 400)
        self.assertEqual(
            self.client.patch("/freedata/queue/no-such-message", json={"action": "send_last"}).status_code, 404
        )


if __name__ == "__main__":
    unittest.main()
