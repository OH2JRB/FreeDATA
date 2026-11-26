"""Broadcast origin and domain in the form the receivers decode from the frame.

A domain typed as "test" (or "TEST") was stored like that on the sending
station, while the receivers decode it from the frame as "TEST-0". The
broadcast id is computed from the domain, so the receivers' repair requests
(NORM_NACK, which carry their id) found nothing on the sender, and the GUI,
which requests domains in upper case, showed a lower-case domain as empty.
"""

import asyncio
import base64
import time
import types
import unittest

from fastapi import HTTPException

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import api_validations, command_norm, helpers
from freedata_server.api.freedata import post_freedata_broadcast
from freedata_server.context import AppContext
from freedata_server.message_system_db_broadcasts import DatabaseManagerBroadcasts
from freedata_server.norm.norm_transmission import NormTransmission


def on_the_air(value):
    """What a receiving station decodes from the frame."""
    return helpers.bytes_to_callsign(helpers.callsign_to_bytes(value)).decode()


class TestBroadcastAddress(unittest.TestCase):
    def test_normal_forms(self):
        for given, expected in (
            ("test", "TEST-0"),
            ("TEST", "TEST-0"),
            (" OHTest-0 ", "OHTEST-0"),
            ("oh2abc-15", "OH2ABC-15"),
            ("WX-63", "WX-63"),
            ("ABCDEFG-7", "ABCDEFG-7"),
        ):
            self.assertEqual(api_validations.broadcast_address(given), expected, given)

    def test_same_as_the_receivers_decode(self):
        for given in ("test", "OHTEST-0", "oh2abc-15", "WX-63", "ABCDEFG-7", "A1"):
            normal = api_validations.broadcast_address(given)
            self.assertEqual(normal, on_the_air(normal), given)

    def test_what_cannot_be_sent(self):
        # longer than 7, other characters, an SSID the frame cannot carry
        for given in ("", None, "TOOLONGNAME", "my net", "a.b", "WX-64", "WX-255", "-1", 7):
            self.assertIsNone(api_validations.broadcast_address(given), given)

    def test_sender_and_receiver_compute_the_same_id(self):
        sender = api_validations.broadcast_address("test")
        receiver = on_the_air(sender)
        ts = int(time.time())
        make_id = NormTransmission.create_broadcast_id
        self.assertEqual(make_id(None, ts, sender, "ab12"), make_id(None, ts, receiver, "ab12"))


class TestNormCommand(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def make(self, **extra):
        params = {
            "origin": "aa1aaa",
            "domain": "test",
            "gridsquare": "JN48ea",
            "type": "MESSAGE",
            "data": base64.b64encode(b"hi").decode(),
        }
        params.update(extra)
        return command_norm.Norm(self.ctx, params)

    def test_origin_and_domain_normalized(self):
        cmd = self.make()
        self.assertEqual((cmd.origin, cmd.domain), ("AA1AAA-0", "TEST-0"))

    def test_invalid_domain_refused(self):
        with self.assertRaises(ValueError):
            self.make(domain="my net")


class TestPostBroadcast(unittest.TestCase):
    def post(self, **payload):
        ctx = types.SimpleNamespace(config_manager=types.SimpleNamespace(config={"EXP": {"enable_groupchat": True}}))
        return asyncio.run(post_freedata_broadcast(payload, ctx))

    def test_invalid_domain_is_400(self):
        with self.assertRaises(HTTPException) as caught:
            self.post(origin="AA1AAA-0", domain="TOOLONGNAME", data="aGk=", type="MESSAGE", gridsquare="JN48ea")
        self.assertEqual(caught.exception.status_code, 400)
        self.assertIn("domain", str(caught.exception.detail))

    def test_invalid_origin_is_400(self):
        with self.assertRaises(HTTPException) as caught:
            self.post(origin="", domain="TEST-0", data="aGk=", type="MESSAGE", gridsquare="JN48ea")
        self.assertEqual(caught.exception.status_code, 400)


class TestDomainLookups(unittest.TestCase):
    """Domains stored in lower case before this fix are still found."""

    @classmethod
    def setUpClass(cls):
        cls.ctx = AppContext(testenv.config_path())
        cls.ctx.config_manager.read()
        cls.db = DatabaseManagerBroadcasts(cls.ctx)

    @classmethod
    def tearDownClass(cls):
        cls.ctx.shutdown()

    def add(self, msg_id, domain):
        self.assertTrue(
            self.db.process_broadcast_message(
                id=msg_id,
                origin="AA1AAA-0",
                timestamp=time.time(),
                burst_index=1,
                burst_data=base64.b64encode(b"hello").decode(),
                total_bursts=2,
                checksum=None,
                domain=domain,
                msg_type="MESSAGE",
                is_read=False,
                direction="receive",
                status="received",
            )
        )

    def test_get_read_and_delete_ignore_case(self):
        self.add("bc-case-1", "lowcase")
        self.assertIn("lowcase", self.db.get_broadcasts_per_domain_json("LOWCASE"))
        self.assertEqual(self.db.mark_domain_as_read("LOWCASE"), 1)
        self.assertEqual(self.db.delete_broadcast_message_or_domain("LOWCASE")["status"], "success")
        self.assertEqual(self.db.get_broadcasts_per_domain_json("lowcase"), {})

    def test_other_domains_untouched(self):
        self.add("bc-case-2", "KEEP1-0")
        self.add("bc-case-3", "KEEP12-0")
        self.assertEqual(list(self.db.get_broadcasts_per_domain_json("keep1-0")), ["KEEP1-0"])
        self.db.delete_broadcast_message_or_domain("KEEP1-0")
        self.db.delete_broadcast_message_or_domain("KEEP12-0")


if __name__ == "__main__":
    unittest.main()
