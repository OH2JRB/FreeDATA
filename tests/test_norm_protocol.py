"""NORM broadcasts between two stations in test mode: the bursts of a
broadcast reach the other station, which assembles them, checks the CRC and
stores the broadcast as received; a lost burst is sent again as a NORM_REPAIR
when the receiver's scheduled job asks for it with a NORM_NACK (see
freedata_server/docs/message-system.md, broadcasts).

The two stations run in one process, so each gets its own message database
here: otherwise the receiver would only add its bursts to the sender's own
record."""

import base64
import os
import queue
import shutil
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import command_norm, message_system_db_broadcasts
from freedata_server.context import AppContext
from freedata_server.message_system_db_broadcasts import DatabaseManagerBroadcasts
from freedata_server.message_system_db_manager import DatabaseManager
from freedata_server.modem_frametypes import FRAME_TYPE

ORIGIN = "AA1AAA-1"
DOMAIN = "BB1BBB-1"
# with 3 more bytes, 120 bytes: 5 bursts of up to 26 bytes
DATA = b"hello world! " * 9


class TwoMinutesLater(datetime):
    """datetime two minutes ahead, for the broadcasts' "received a minute ago"."""

    @classmethod
    def now(cls, tz=None):
        return datetime.now(tz) + timedelta(minutes=2)


def wait_for(condition, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = condition()
        if result:
            return result
        time.sleep(0.1)
    return None


class TestNormBroadcast(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # a message database per station: DatabaseManager.get_database()
        # normally takes the one FREEDATA_DATABASE of the process
        cls.db_dir = tempfile.mkdtemp(prefix="freedata-norm-")
        cls.databases = {}
        original = DatabaseManager.get_database

        def get_database(manager):
            return cls.databases.get(id(manager.ctx)) or original(manager)

        cls.db_patch = mock.patch.object(DatabaseManager, "get_database", get_database)
        cls.db_patch.start()

        cls.ctx_ISS = cls.start_station("iss")
        cls.ctx_IRS = cls.start_station("irs")

        cls.drop = None
        cls.channels_running = True
        cls.channels = [
            threading.Thread(target=cls.channel_worker, args=[cls.ctx_ISS, cls.ctx_IRS], daemon=True),
            threading.Thread(target=cls.channel_worker, args=[cls.ctx_IRS, cls.ctx_ISS], daemon=True),
        ]
        for channel in cls.channels:
            channel.start()

    @classmethod
    def start_station(cls, name):
        ctx = AppContext(testenv.config_path())
        cls.databases[id(ctx)] = "sqlite:///" + os.path.join(cls.db_dir, f"{name}.db")
        ctx.TESTMODE = True
        testenv.start(ctx)
        # the service manager reads the config again when it starts the modem
        if not wait_for(lambda: ctx.rf_modem is not None, 10):
            raise RuntimeError("modem did not start")
        time.sleep(0.5)
        # group chat (NORM) is off by default
        ctx.config_manager.config["EXP"]["enable_groupchat"] = True
        return ctx

    @classmethod
    def tearDownClass(cls):
        cls.channels_running = False
        for channel in cls.channels:
            channel.join()
        cls.ctx_IRS.shutdown()
        cls.ctx_ISS.shutdown()
        cls.db_patch.stop()
        # shutdown() disposed the database engines
        shutil.rmtree(cls.db_dir, ignore_errors=True)

    @classmethod
    def channel_worker(cls, ctx_a, ctx_b):
        """Delivers every frame station A transmits to station B, unless drop(frame) says it is lost."""
        while cls.channels_running:
            try:
                mode, frames = ctx_a.TESTMODE_TRANSMIT_QUEUE.get(timeout=0.5)
            except queue.Empty:
                continue
            for frame in frames if isinstance(frames, list) else [frames]:
                frame = bytes(frame) + bytes(2)  # the CRC16 the modulator appends
                if cls.drop and cls.drop(frame):
                    continue
                ctx_b.service_manager.frame_dispatcher.process_data(frame, None, len(frame), 15, 0, mode_name=mode)

    def setUp(self):
        type(self).drop = None
        self.addCleanup(setattr, type(self), "drop", None)

    def broadcast(self, data):
        params = {
            "origin": ORIGIN,
            "domain": DOMAIN,
            "gridsquare": "JN48ea",
            "type": "MESSAGE",
            "priority": "1",
            "data": base64.b64encode(data).decode("ascii"),
        }
        self.assertTrue(command_norm.Norm(self.ctx_ISS, params).run())
        # the sender stores its broadcast before transmitting it
        sent = wait_for(lambda: self.find(self.ctx_ISS, data), 10)
        self.assertIsNotNone(sent, "the sender did not store the broadcast")
        return sent

    def find(self, ctx, data):
        """The broadcast with this payload in the station's database, by its bursts."""
        domains = DatabaseManagerBroadcasts(ctx).get_broadcasts_per_domain_json(DOMAIN)
        for broadcast in domains.get(DOMAIN, []):
            bursts = broadcast["payload_data"].get("bursts", {})
            joined = b"".join(base64.b64decode(bursts[k]) for k in sorted(bursts, key=int))
            if data.startswith(joined[: len(data)]) and joined and len(joined) <= len(data):
                return broadcast
        return None

    def received(self, ctx, broadcast_id):
        broadcast = DatabaseManagerBroadcasts(ctx).get_broadcast_per_id(broadcast_id)
        return broadcast if broadcast and broadcast["status"] == "received" else None

    def test_broadcast_received(self):
        data = DATA + b"one"
        sent = self.broadcast(data)
        self.assertEqual(sent["direction"], "transmit")

        received = wait_for(lambda: self.received(self.ctx_IRS, sent["id"]), 30)
        self.assertIsNotNone(received, "the receiver never assembled the broadcast")
        self.assertEqual(base64.b64decode(received["payload_data"]["final"]), data)
        self.assertEqual(received["direction"], "receive")
        self.assertEqual(received["origin"], ORIGIN)
        self.assertEqual(received["domain"], DOMAIN)
        self.assertEqual(received["total_bursts"], 5)
        self.assertFalse(received["is_read"])

    def test_lost_burst_repaired(self):
        data = DATA + b"two"
        norm_data = []

        def lose_second_data_burst(frame):
            if frame[0] != FRAME_TYPE.NORM_DATA.value:
                return False
            norm_data.append(frame)
            return len(norm_data) == 2

        type(self).drop = lose_second_data_burst
        sent = self.broadcast(data)

        # four of five bursts arrive: the broadcast waits for the missing one
        def bursts_held():
            broadcast = DatabaseManagerBroadcasts(self.ctx_IRS).get_broadcast_per_id(sent["id"])
            return broadcast and sorted(broadcast["payload_data"]["bursts"], key=int)

        self.assertEqual(wait_for(lambda: bursts_held() == ["1", "3", "4", "5"], 30), True, bursts_held())
        self.assertIsNone(self.received(self.ctx_IRS, sent["id"]))

        # the receiver's scheduled job asks for burst 2 once the broadcast is a
        # minute old (check_missing_bursts); the sender repairs it
        with (
            mock.patch.object(message_system_db_broadcasts, "datetime", TwoMinutesLater),
            mock.patch.object(self.ctx_IRS.state_manager, "is_modem_running", True),
        ):
            self.ctx_IRS.schedule_manager.check_missing_broadcast_bursts()
        received = wait_for(lambda: self.received(self.ctx_IRS, sent["id"]), 30)
        self.assertIsNotNone(received, "the repair did not complete the broadcast")
        self.assertEqual(base64.b64decode(received["payload_data"]["final"]), data)


if __name__ == "__main__":
    unittest.main()
