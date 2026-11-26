"""GET /freedata/messages?attachment_data=false leaves out the attachments'
base64 data.

The GUI reloads the whole message list on every message-db event; with the
data of every attachment in it, the reload grew with the database. The list
now comes without it, and the GUI fetches an image's data by its hash.
Without the parameter the answer is unchanged, for other API users.
"""

import asyncio
import base64
import json
import unittest

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.api.freedata import get_freedata_messages
from freedata_server.context import AppContext
from freedata_server.message_p2p import MessageP2P
from freedata_server.message_system_db_messages import DatabaseManagerMessages

PAYLOAD = b"\x89PNG fake image bytes" * 10


class TestMessageListWithoutAttachmentData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.messages = DatabaseManagerMessages(cls.ctx)
        attachment = {"name": "light.png", "type": "image/png", "data": base64.b64encode(PAYLOAD).decode()}
        message = MessageP2P.from_api_params(
            "AA1AAA-0", {"destination": "BB1BBB-0", "body": "light list", "attachments": [attachment]}
        )
        cls.message_id = message.to_dict()["id"]
        cls.messages.add_message(message.to_dict(), statistics={}, direction="transmit", status="queued")

    @classmethod
    def tearDownClass(cls):
        cls.messages.delete_message(cls.message_id, allow_queued=True)
        cls.ctx.shutdown()

    def get(self, **params):
        response = asyncio.run(get_freedata_messages(ctx=self.ctx, **params))
        messages = json.loads(response.body)["messages"]
        return next(m for m in messages if m["id"] == self.message_id)["attachments"][0]

    def test_default_keeps_the_data(self):
        attachment = self.get()
        self.assertEqual(base64.b64decode(attachment["data"]), PAYLOAD)
        self.assertNotIn("size", attachment)

    def test_without_data(self):
        attachment = self.get(attachment_data=False)
        self.assertNotIn("data", attachment)
        self.assertEqual(attachment["size"], len(PAYLOAD))
        # what the GUI needs to show and fetch it
        for key in ("name", "type", "hash_sha512"):
            self.assertTrue(attachment[key], key)
        self.assertIn("checksum_crc32", attachment)

    def test_data_by_hash_still_available(self):
        from freedata_server.message_system_db_attachments import DatabaseManagerAttachments

        attachment = self.get(attachment_data=False)
        full = DatabaseManagerAttachments(self.ctx).get_attachment_by_sha512(attachment["hash_sha512"])
        self.assertEqual(base64.b64decode(full["data"]), PAYLOAD)


if __name__ == "__main__":
    unittest.main()
