"""The message database: one shared engine, missing indexes added at startup,
broadcast lookups that return plain snapshots, and one update per ARQ result
(freedata_server/docs/message-system.md)."""

import os
import sqlite3
from contextlib import closing
import time
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server.context import AppContext
from freedata_server.message_p2p import MessageP2P, message_failed, message_transmitted
from freedata_server.message_system_db_broadcasts import DatabaseManagerBroadcasts
from freedata_server.message_system_db_manager import DatabaseManager
from freedata_server.message_system_db_messages import DatabaseManagerMessages


class TestMessageDatabaseFixes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def test_managers_share_one_engine(self):
        self.assertIs(DatabaseManagerMessages(self.ctx).engine, DatabaseManagerBroadcasts(self.ctx).engine)

    def test_missing_index_added(self):
        db_path = os.path.join(os.path.dirname(os.environ["FREEDATA_DATABASE"]), "missing-index.db")
        previous = os.environ["FREEDATA_DATABASE"]
        os.environ["FREEDATA_DATABASE"] = db_path
        try:
            manager = DatabaseManager(self.ctx)  # creates the tables and their indexes
            # closing(): a sqlite3 connection's own "with" only commits, and an
            # open file cannot be deleted on Windows (testenv's cleanup)
            with closing(sqlite3.connect(db_path)) as conn, conn:
                conn.execute("DROP INDEX idx_beacon_callsign")
            manager.check_database_version()
            with closing(sqlite3.connect(db_path)) as conn:
                names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
            self.assertIn("idx_beacon_callsign", names)
        finally:
            os.environ["FREEDATA_DATABASE"] = previous

    def test_broadcast_lookups_return_snapshots(self):
        broadcasts = DatabaseManagerBroadcasts(self.ctx)
        broadcast_id = f"snapshot-{time.time()}"
        broadcasts.process_broadcast_message(
            id=broadcast_id,
            origin="AA1AAA-0",
            timestamp=time.time(),
            burst_index=1,
            burst_data="aGk=",
            total_bursts=1,
            checksum="00",
            domain="SNAP-0",
            direction="transmit",
            nexttransmission_at=time.time() - 1,
        )
        snapshot = broadcasts.get_broadcast_per_id(broadcast_id, get_object=True)
        # a detached ORM object would fail to load its status here
        self.assertIsInstance(snapshot.status, str)
        self.assertEqual(snapshot.domain, "SNAP-0")
        self.assertEqual(vars(snapshot), broadcasts.get_broadcast_per_id(broadcast_id))

    def test_one_update_per_arq_result(self):
        messages = DatabaseManagerMessages(self.ctx)
        for callback, status in ((message_transmitted, "transmitted"), (message_failed, "failed")):
            message = MessageP2P.from_api_params("AA1AAA-0", {"destination": "BB1BBB-0", "body": status})
            messages.add_message(message.to_dict(), statistics={}, direction="transmit", status="transmitting")
            with mock.patch.object(self.ctx.event_manager, "freedata_message_db_change") as changed:
                callback(self.ctx, message.to_payload().encode("utf-8"), {"duration": 12})
            changed.assert_called_once()
            stored = messages.get_message_by_id(message.id)
            self.assertEqual(stored["status"], status)
            self.assertEqual(stored["statistics"]["duration"], 12)


if __name__ == "__main__":
    unittest.main()
