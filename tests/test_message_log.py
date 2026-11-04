"""The message log: POST /freedata/messages/log writes all messages to a CSV
file next to the message database and returns its name and full path."""

import base64
import csv
import os
import time
import unittest
import uuid
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.api import freedata
from freedata_server.context import AppContext
from freedata_server.message_p2p import MessageP2P
from freedata_server.message_system_db_messages import DatabaseManagerMessages


class TestMessageLog(unittest.TestCase):
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

    def add(
        self,
        body,
        attachments=(),
        origin="AA1AAA-1",
        destination="QU1AAA-1",
        direction="transmit",
        status="transmitted",
    ):
        time.sleep(0.02)  # the id holds the time; Windows clock steps are ~16 ms
        params = {
            "destination": destination,
            "body": body,
            "attachments": [
                {"name": name, "type": "text/plain", "data": base64.b64encode(name.encode()).decode()}
                for name in attachments
            ],
        }
        message = MessageP2P.from_api_params(origin, params)
        self.db.add_message(message.to_dict(), statistics={}, direction=direction, status=status)
        return message

    def write_log(self):
        response = self.client.post("/freedata/messages/log")
        self.assertEqual(response.status_code, 200)
        result = response.json()
        self.addCleanup(os.remove, result["file_path"])
        return result

    def read_rows(self, path):
        with open(path, encoding="utf-8-sig", newline="") as f:
            return list(csv.reader(f, delimiter=";"))

    def test_log_file_next_to_database(self):
        result = self.write_log()
        self.assertEqual(result["status"], "success")
        path = result["file_path"]
        self.assertTrue(os.path.isabs(path))
        self.assertTrue(os.path.isfile(path))
        self.assertEqual(os.path.basename(path), result["file_name"])
        self.assertRegex(result["file_name"], r"^freedata-message-log_\d{8}-\d{6}(_\d+)?\.csv$")
        database_dir = os.path.dirname(os.path.abspath(os.environ["FREEDATA_DATABASE"]))
        self.assertEqual(os.path.dirname(path), database_dir)
        rows = self.read_rows(path)
        self.assertEqual(rows[0], ["Date/time (UTC)", "Sender", "Receiver", "Message", "Attachments"])
        self.assertEqual(result["total_messages"], len(rows) - 1)
        completed = [
            m
            for m in self.db.get_all_messages()
            if (m["direction"], m["status"]) in (("transmit", "transmitted"), ("receive", "received"))
        ]
        self.assertEqual(len(completed), len(rows) - 1)

    def test_only_completed_transmissions(self):
        tag = uuid.uuid4().hex
        sent = self.add(f"{tag} sent")
        received = self.add(
            f"{tag} received", origin="BB2BBB-2", destination="AA1AAA-1", direction="receive", status="received"
        )
        for status in ("queued", "transmitting", "failed", "aborted"):
            self.add(f"{tag} {status}", status=status)

        bodies = [row[3] for row in self.read_rows(self.write_log()["file_path"]) if tag in row[3]]
        self.assertEqual(bodies, [f"{tag} sent", f"{tag} received"])

        # sent again: left out until the new transmission has finished
        self.db.update_message(sent.id, {"status": "queued"})
        bodies = [row[3] for row in self.read_rows(self.write_log()["file_path"]) if tag in row[3]]
        self.assertEqual(bodies, [f"{tag} received"])
        self.assertTrue(received.id)

    def test_rows_quoting_and_attachments(self):
        tag = uuid.uuid4().hex
        tricky = f'{tag} Hello, "world";\nsecond line äöå ✓'
        plain = self.add(f"{tag} plain")
        files = self.add(tricky, attachments=("a, b.txt", "kuva.jpg"))
        formula = self.add(f"=1+1 {tag}", origin="BB2BBB-2", destination="AA1AAA-1")

        result = self.write_log()
        with open(result["file_path"], "rb") as f:
            raw = f.read()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "UTF-8 byte order mark for spreadsheets")
        # semicolon separated, for Excel with a comma as decimal mark
        self.assertTrue(raw[3:].startswith(b"Date/time (UTC);Sender;Receiver;Message;Attachments\r\n"))
        # a comma alone needs no quoting
        self.assertIn(f";{tag} plain;".encode(), raw)
        # RFC 4180: the cell is quoted, quotes doubled, the line break kept inside
        self.assertIn(f'"{tag} Hello, ""world"";\nsecond line äöå ✓"'.encode(), raw)

        rows = [row for row in self.read_rows(result["file_path"]) if tag in row[3]]
        self.assertEqual(len(rows), 3)
        for row in rows:
            self.assertEqual(len(row), 5)
            self.assertRegex(row[0], r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")

        # oldest first; timestamps come from the message id (UTC)
        self.assertEqual([row[3] for row in rows], [f"{tag} plain", tricky, f"'=1+1 {tag}"])
        self.assertEqual(rows[0][0], plain.id.rsplit("_", 1)[1][:19].replace("T", " "))
        self.assertEqual(rows[0][1:3], ["AA1AAA-1", "QU1AAA-1"])
        self.assertEqual(rows[0][4], "")
        self.assertEqual(rows[1][4], "a, b.txt; kuva.jpg")
        self.assertEqual(rows[2][1:3], ["BB2BBB-2", "AA1AAA-1"])
        self.assertTrue(files.id and formula.id)

    def test_new_file_each_time(self):
        first = self.write_log()
        second = self.write_log()
        self.assertNotEqual(first["file_path"], second["file_path"])

    def test_write_error_is_500(self):
        with mock.patch.object(DatabaseManagerMessages, "write_message_log_csv", side_effect=PermissionError("denied")):
            response = self.client.post("/freedata/messages/log")
        self.assertEqual(response.status_code, 500)
        self.assertIn("denied", response.json()["detail"]["error"])


if __name__ == "__main__":
    unittest.main()
