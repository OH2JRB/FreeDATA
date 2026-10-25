"""Tests for the common encryption key: one shared key for all stations, used
when the [MESSAGES] enable_common_key setting is on. A station's own key takes
precedence over it."""

import asyncio
import copy
import json
import queue
import unittest
from unittest import mock

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import encryption
from freedata_server.context import AppContext
from freedata_server.arq_data_type_handler import ARQDataTypeHandler, ARQ_SESSION_TYPES
from freedata_server.message_p2p import MessageP2P
from freedata_server.message_system_db_encryption import DatabaseManagerEncryption
from freedata_server.message_system_db_messages import DatabaseManagerMessages
from freedata_server import command_message_send
from freedata_server.api.config import post_config
from freedata_server.api.freedata import (
    get_station_encryption,
    put_station_encryption_key,
    get_common_encryption_key,
    put_common_encryption_key,
    delete_common_encryption_key,
)

COMMON_HEX_KEY = "aa" * 32
STATION_HEX_KEY = "bb" * 32


def message_payload(msg_id, origin, destination, body="common secret"):
    message = MessageP2P(msg_id, origin, destination, body, [])
    return bytearray(message.to_payload().encode("utf-8"))


def body(response):
    return json.loads(response.body)


class TestCommonKeyStore(unittest.TestCase):
    def test_station_key_takes_precedence(self):
        enabled = [False]
        store = encryption.StationKeyStore(common_key_enabled=lambda: enabled[0])
        common = encryption.parse_key(COMMON_HEX_KEY)
        station = encryption.parse_key(STATION_HEX_KEY)
        store.set_common(common)

        # setting off: the common key is not used
        self.assertIsNone(store.key_for("CK1AAA-0"))
        self.assertEqual(store.keys_for("CK1AAA-0"), [])

        enabled[0] = True
        self.assertEqual(store.key_for("CK1AAA-0"), common)
        store.set("CK1AAA-0", station)
        self.assertEqual(store.key_for("CK1AAA-0"), station)
        self.assertEqual(store.keys_for("CK1AAA-0"), [station, common])

        self.assertEqual(store.common_fingerprint(), encryption.key_fingerprint(common))
        self.assertTrue(store.remove_common())
        self.assertFalse(store.remove_common())
        self.assertIsNone(store.common_fingerprint())
        with self.assertRaises(ValueError):
            store.set_common(b"short")

    def test_decrypt_any(self):
        common = encryption.parse_key(COMMON_HEX_KEY)
        station = encryption.parse_key(STATION_HEX_KEY)
        payload = encryption.encrypt(common, b"hello")
        self.assertEqual(encryption.decrypt_any([station, common], payload), b"hello")
        with self.assertRaises(encryption.DecryptionError):
            encryption.decrypt_any([station], payload)
        with self.assertRaises(encryption.DecryptionError):
            encryption.decrypt_any([], payload)


class TestCommonKeyMessages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.ctx.TESTMODE = True
        cls.ctx.message_system_db_manager.initialize_default_values()
        cls.handler = ARQDataTypeHandler(cls.ctx)
        cls.msg_db = DatabaseManagerMessages(cls.ctx)
        cls.enc_db = DatabaseManagerEncryption(cls.ctx)
        config = cls.ctx.config_manager.config["STATION"]
        cls.mycall = f"{config['mycall']}-{config['myssid']}"

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def setUp(self):
        self.set_common_enabled(True)
        self.addCleanup(self.set_common_enabled, False)
        self.addCleanup(self.ctx.station_keys.remove_common)

    def set_common_enabled(self, enabled):
        self.ctx.config_manager.config["MESSAGES"]["enable_common_key"] = enabled

    def encrypt_with(self, hex_key, payload):
        """Encrypts a payload as a remote station would."""
        key = encryption.parse_key(hex_key)
        return encryption.encrypt(key, self.handler.prepare_p2pmsg_zlib(payload))

    def test_send_with_common_key(self):
        remote = "CK2AAA-0"
        # no common key yet: the message waits for a key, although encryption is off for the station
        command = command_message_send.SendMessageCommand(self.ctx, {"destination": remote, "body": "to all"})
        msg_id = command.message.id
        self.assertEqual(self.msg_db.get_message_by_id(msg_id)["status"], "key_required")

        result = body(asyncio.run(put_common_encryption_key({"key": COMMON_HEX_KEY}, self.ctx)))
        self.assertTrue(result["key_loaded"])
        self.assertGreaterEqual(result["requeued"], 1)
        self.assertEqual(self.msg_db.get_message_by_id(msg_id)["status"], "queued")

        status = body(asyncio.run(get_station_encryption(remote, self.ctx)))
        self.assertFalse(status["enabled"])
        self.assertTrue(status["encrypted"])
        self.assertEqual(status["key_source"], "common")

        payload = message_payload(f"{self.mycall}_{remote}_2026-10-25T10:00:00+00:00", self.mycall, remote)
        data, _ = self.handler.prepare(payload, ARQ_SESSION_TYPES.p2pmsg_zlib_aesgcm, remote)
        encryption.decrypt(encryption.parse_key(COMMON_HEX_KEY), data)

        # the station's own key wins
        self.ctx.station_keys.set(remote, encryption.parse_key(STATION_HEX_KEY))
        try:
            data, _ = self.handler.prepare(payload, ARQ_SESSION_TYPES.p2pmsg_zlib_aesgcm, remote)
            encryption.decrypt(encryption.parse_key(STATION_HEX_KEY), data)
            self.assertEqual(body(asyncio.run(get_station_encryption(remote, self.ctx)))["key_source"], "station")
        finally:
            self.ctx.station_keys.remove(remote)
        self.msg_db.update_message(msg_id, {"status": "transmitted"})

    def test_receive_with_common_and_station_key(self):
        remote = "CK3AAA-0"
        asyncio.run(put_common_encryption_key({"key": COMMON_HEX_KEY}, self.ctx))

        msg_id = f"{remote}_{self.mycall}_2026-10-25T11:00:00+00:00"
        data = self.encrypt_with(COMMON_HEX_KEY, message_payload(msg_id, remote, self.mycall))
        result = self.handler.dispatch(ARQ_SESSION_TYPES.p2pmsg_zlib_aesgcm.value, data, statistics={}, dxcall=remote)
        self.assertEqual(json.loads(result)["body"], "common secret")
        self.assertTrue(self.msg_db.get_message_by_id(msg_id)["encrypted"])

        # with a station key loaded, a message under the common key still decrypts
        self.ctx.station_keys.set(remote, encryption.parse_key(STATION_HEX_KEY))
        try:
            msg_id = f"{remote}_{self.mycall}_2026-10-25T11:01:00+00:00"
            data = self.encrypt_with(COMMON_HEX_KEY, message_payload(msg_id, remote, self.mycall))
            self.assertIsNotNone(
                self.handler.dispatch(ARQ_SESSION_TYPES.p2pmsg_zlib_aesgcm.value, data, statistics={}, dxcall=remote)
            )
        finally:
            self.ctx.station_keys.remove(remote)

        # setting off: the common key is not used
        self.set_common_enabled(False)
        msg_id = f"{remote}_{self.mycall}_2026-10-25T11:02:00+00:00"
        data = self.encrypt_with(COMMON_HEX_KEY, message_payload(msg_id, remote, self.mycall))
        self.assertIsNone(
            self.handler.dispatch(ARQ_SESSION_TYPES.p2pmsg_zlib_aesgcm.value, data, statistics={}, dxcall=remote)
        )
        self.assertEqual(self.enc_db.count_pending(remote), 1)

        # back on: entering the common key decrypts it
        self.set_common_enabled(True)
        result = body(asyncio.run(put_common_encryption_key({"key": COMMON_HEX_KEY}, self.ctx)))
        self.assertGreaterEqual(result["decrypted"], 1)
        self.assertEqual(self.enc_db.count_pending(remote), 0)
        self.assertEqual(self.msg_db.get_message_by_id(msg_id)["body"], "common secret")

    def test_common_key_decrypts_messages_of_all_stations(self):
        remotes = ["CK4AAA-0", "CK5AAA-0"]
        for i, remote in enumerate(remotes):
            msg_id = f"{remote}_{self.mycall}_2026-10-25T12:0{i}:00+00:00"
            data = self.encrypt_with(COMMON_HEX_KEY, message_payload(msg_id, remote, self.mycall))
            self.handler.dispatch(ARQ_SESSION_TYPES.p2pmsg_zlib_aesgcm.value, data, statistics={}, dxcall=remote)
        self.assertEqual([self.enc_db.count_pending(r) for r in remotes], [1, 1])

        result = body(asyncio.run(put_common_encryption_key({"key": COMMON_HEX_KEY}, self.ctx)))
        self.assertGreaterEqual(result["decrypted"], 2)
        self.assertEqual([self.enc_db.count_pending(r) for r in remotes], [0, 0])

        # a station key that fits nothing is still rejected
        remote = "CK6AAA-0"
        data = self.encrypt_with(STATION_HEX_KEY, message_payload(f"{remote}_x", remote, self.mycall))
        self.handler.dispatch(ARQ_SESSION_TYPES.p2pmsg_zlib_aesgcm.value, data, statistics={}, dxcall=remote)
        self.assertEqual(self.enc_db.count_pending(remote), 1)
        result = body(asyncio.run(put_station_encryption_key(remote, {"key": STATION_HEX_KEY}, self.ctx)))
        self.assertEqual(result["decrypted"], 1)
        self.ctx.station_keys.remove(remote)

    def test_common_key_api(self):
        status = body(asyncio.run(get_common_encryption_key(self.ctx)))
        self.assertEqual(status, {"enabled": True, "key_loaded": False, "fingerprint": None})
        result = body(asyncio.run(put_common_encryption_key({"key": COMMON_HEX_KEY}, self.ctx)))
        self.assertEqual(result["fingerprint"], encryption.key_fingerprint(bytes.fromhex(COMMON_HEX_KEY)))
        result = body(asyncio.run(delete_common_encryption_key(self.ctx)))
        self.assertFalse(result["key_loaded"])
        with self.assertRaises(Exception):
            asyncio.run(put_common_encryption_key({"key": "short"}, self.ctx))

    def test_switching_setting_on_requeues(self):
        remote = "CK7AAA-0"
        self.ctx.config_manager.write(self.config_with_common_key(False))
        self.ctx.station_keys.set_common(encryption.parse_key(COMMON_HEX_KEY))

        # setting off: no encryption, message is queued and sent in plain text
        command = command_message_send.SendMessageCommand(self.ctx, {"destination": remote, "body": "plain"})
        self.assertEqual(self.msg_db.get_message_by_id(command.message.id)["status"], "queued")
        self.msg_db.update_message(command.message.id, {"status": "transmitted"})

        # a message waiting for a key is queued again once the setting is switched on
        self.ctx.station_keys.remove_common()
        self.set_common_enabled(True)
        command = command_message_send.SendMessageCommand(self.ctx, {"destination": remote, "body": "wait"})
        self.assertEqual(self.msg_db.get_message_by_id(command.message.id)["status"], "key_required")
        self.ctx.station_keys.set_common(encryption.parse_key(COMMON_HEX_KEY))
        self.ctx.config_manager.write(self.config_with_common_key(False))

        # keep the config change from restarting the modem (and its scheduler) in this test
        with mock.patch.object(self.ctx, "modem_service", queue.Queue()):
            asyncio.run(post_config(self.config_with_common_key(True), self.ctx))
        self.assertTrue(self.ctx.station_keys.common_enabled())
        self.assertEqual(self.msg_db.get_message_by_id(command.message.id)["status"], "queued")
        self.msg_db.update_message(command.message.id, {"status": "transmitted"})
        self.ctx.config_manager.write(self.config_with_common_key(False))

    def config_with_common_key(self, enabled):
        config = copy.deepcopy(self.ctx.config_manager.read())
        config["MESSAGES"]["enable_common_key"] = enabled
        return config


if __name__ == "__main__":
    unittest.main()
