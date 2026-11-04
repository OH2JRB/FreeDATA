"""The message log: GET /freedata/messages/log returns the completed messages
as a CSV download, oldest first; nothing is stored on the server."""

import base64
import csv
import io
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
        timestamp=None,
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
        data = message.to_dict()
        if timestamp:  # the message time is the one in its id
            data["id"] = message.id = f"{origin}_{destination}_{timestamp}"
        self.db.add_message(data, statistics={}, direction=direction, status=status)
        return message

    def download(self):
        response = self.client.get("/freedata/messages/log")
        self.assertEqual(response.status_code, 200)
        return response

    def rows(self, response):
        return list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter=";"))

    def test_download(self):
        files_before = set(os.listdir(os.path.dirname(os.path.abspath(os.environ["FREEDATA_DATABASE"]))))
        response = self.download()
        self.assertEqual(response.headers["content-type"], "text/csv; charset=utf-8")
        self.assertRegex(
            response.headers["content-disposition"],
            r'^attachment; filename="freedata-message-log_\d{8}-\d{6}\.csv"$',
        )
        rows = self.rows(response)
        self.assertEqual(rows[0], ["Date/time (UTC)", "Sender", "Receiver", "Message", "Attachments"])
        self.assertEqual(int(response.headers["x-total-messages"]), len(rows) - 1)
        completed = [
            m
            for m in self.db.get_all_messages()
            if (m["direction"], m["status"]) in (("transmit", "transmitted"), ("receive", "received"))
        ]
        self.assertEqual(len(completed), len(rows) - 1)
        # nothing written next to the database
        files_after = set(os.listdir(os.path.dirname(os.path.abspath(os.environ["FREEDATA_DATABASE"]))))
        self.assertEqual(files_after, files_before)

    def test_only_completed_transmissions(self):
        tag = uuid.uuid4().hex
        sent = self.add(f"{tag} sent")
        received = self.add(
            f"{tag} received", origin="BB2BBB-2", destination="AA1AAA-1", direction="receive", status="received"
        )
        unfinished = [
            self.add(f"{tag} {status}", status=status) for status in ("queued", "transmitting", "failed", "aborted")
        ]
        # the message DB is shared by the whole test run: a queued message left
        # here would be sent by another test's schedule manager
        for message in [sent, *unfinished]:
            self.addCleanup(self.db.delete_message, message.id)
            self.addCleanup(self.db.update_message, message.id, {"status": "failed"})

        bodies = [row[3] for row in self.rows(self.download()) if tag in row[3]]
        self.assertEqual(bodies, [f"{tag} sent", f"{tag} received"])

        # sent again: left out until the new transmission has finished
        self.db.update_message(sent.id, {"status": "queued"})
        bodies = [row[3] for row in self.rows(self.download()) if tag in row[3]]
        self.assertEqual(bodies, [f"{tag} received"])
        self.assertTrue(received.id)

    def test_oldest_first(self):
        # added in another order than their times: a received message can be
        # older than one sent before it reached the database
        tag = uuid.uuid4().hex
        for body, timestamp in (
            ("third", "2001-01-03T08:00:00+00:00"),
            ("first", "2001-01-01T23:59:59.500000+00:00"),
            ("second", "2001-01-02T00:00:00+00:00"),
        ):
            self.add(f"{tag} {body}", timestamp=timestamp)

        rows = [row for row in self.rows(self.download()) if tag in row[3]]
        self.assertEqual([row[3] for row in rows], [f"{tag} first", f"{tag} second", f"{tag} third"])
        self.assertEqual(
            [row[0] for row in rows], ["2001-01-01 23:59:59", "2001-01-02 00:00:00", "2001-01-03 08:00:00"]
        )
        # oldest first over the whole log, not only these
        times = [row[0] for row in self.rows(self.download())[1:]]
        self.assertEqual(times, sorted(times))

    def test_rows_quoting_and_attachments(self):
        tag = uuid.uuid4().hex
        tricky = f'{tag} Hello, "world";\nsecond line äöå ✓'
        plain = self.add(f"{tag} plain")
        files = self.add(tricky, attachments=("a, b.txt", "kuva.jpg"))
        formula = self.add(f"=1+1 {tag}", origin="BB2BBB-2", destination="AA1AAA-1")

        response = self.download()
        raw = response.content
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"), "UTF-8 byte order mark for spreadsheets")
        # semicolon separated, for Excel with a comma as decimal mark
        self.assertTrue(raw[3:].startswith(b"Date/time (UTC);Sender;Receiver;Message;Attachments\r\n"))
        # a comma alone needs no quoting
        self.assertIn(f";{tag} plain;".encode(), raw)
        # RFC 4180: the cell is quoted, quotes doubled, the line break kept inside
        self.assertIn(f'"{tag} Hello, ""world"";\nsecond line äöå ✓"'.encode(), raw)

        rows = [row for row in self.rows(response) if tag in row[3]]
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

    def test_error_is_500(self):
        with mock.patch.object(DatabaseManagerMessages, "message_log_csv", side_effect=RuntimeError("broken")):
            response = self.client.get("/freedata/messages/log")
        self.assertEqual(response.status_code, 500)
        self.assertIn("broken", response.json()["detail"]["error"])


if __name__ == "__main__":
    unittest.main()
