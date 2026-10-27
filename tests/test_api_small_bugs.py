"""Small API fixes: the GUI URL for a server on all interfaces, transmit
commands that report a failure by returning False, error examples that show
the body the server sends, no 503 documented where none is raised, and the
per-domain broadcast GET with and without the trailing slash."""

import ast
import asyncio
import inspect
import types
import unittest
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

import testenv  # noqa: F401  isolates config + message DB, must precede freedata_server imports
from freedata_server import command_norm, helpers
from freedata_server.api import config, devices, freedata, general, modem, radio
from freedata_server.api.command_helpers import enqueue_tx_command

ROUTER_MODULES = (general, config, devices, radio, modem, freedata)


class TestBrowsableHost(unittest.TestCase):
    def test_wildcard_addresses(self):
        self.assertEqual(helpers.browsable_host("0.0.0.0"), "127.0.0.1")
        self.assertEqual(helpers.browsable_host(""), "127.0.0.1")
        self.assertEqual(helpers.browsable_host("::"), "[::1]")

    def test_other_addresses(self):
        self.assertEqual(helpers.browsable_host("192.168.1.5"), "192.168.1.5")
        self.assertEqual(helpers.browsable_host("localhost"), "localhost")
        self.assertEqual(helpers.browsable_host("fe80::1"), "[fe80::1]")
        self.assertEqual(helpers.browsable_host("[fe80::1]"), "[fe80::1]")


def command_class(run):
    class Command:
        def __init__(self, ctx, params):
            pass

        def get_name(self):
            return "Command"

    Command.run = lambda self: run()
    return Command


class TestEnqueueTxCommand(unittest.TestCase):
    def enqueue(self, run):
        return asyncio.run(enqueue_tx_command(mock.Mock(), command_class(run)))

    def test_success(self):
        self.assertTrue(self.enqueue(lambda: None))
        self.assertTrue(self.enqueue(lambda: object()))

    def test_run_returns_false(self):
        self.assertFalse(self.enqueue(lambda: False))

    def test_run_raises(self):
        def fail():
            raise RuntimeError("no modem")

        self.assertFalse(self.enqueue(fail))


class TestNormRun(unittest.TestCase):
    def make_norm(self, groupchat):
        norm = command_norm.Norm.__new__(command_norm.Norm)
        norm.ctx = types.SimpleNamespace(
            config_manager=types.SimpleNamespace(config={"EXP": {"enable_groupchat": groupchat}})
        )
        norm.origin, norm.domain, norm.gridsquare = "AA1AAA-1", "TEST-0", "JN48"
        norm.data, norm.priority, norm.msgtype = b"hello", 1, "message"
        norm.emit_event = mock.Mock()
        norm.logger = mock.Mock()
        norm.log = mock.Mock()
        return norm

    def test_started_broadcast_is_a_success(self):
        with mock.patch.object(command_norm, "NormTransmissionISS"):
            self.assertTrue(self.make_norm(groupchat=True).run())

    def test_group_chat_disabled(self):
        self.assertFalse(self.make_norm(groupchat=False).run())

    def test_error_starting(self):
        with mock.patch.object(command_norm, "NormTransmissionISS", side_effect=RuntimeError):
            self.assertFalse(self.make_norm(groupchat=True).run())


def make_app():
    app = FastAPI()
    for module in ROUTER_MODULES:
        app.include_router(module.router)
    return app


class TestOpenApiErrors(unittest.TestCase):
    def test_error_examples_show_the_detail_body(self):
        # api_abort raises HTTPException(detail={"error": ...})
        checked = 0
        for path, methods in make_app().openapi()["paths"].items():
            for method, operation in methods.items():
                for status, response in operation.get("responses", {}).items():
                    content = response.get("content", {}).get("application/json", {})
                    examples = [content.get("example")]
                    examples += [e.get("value") for e in content.get("examples", {}).values()]
                    for example in examples:
                        if int(status) >= 400 and isinstance(example, dict):
                            checked += 1
                            self.assertIn("detail", example, f"{method.upper()} {path} {status}")
        self.assertGreater(checked, 50)

    def test_503_only_where_raised(self):
        for module in ROUTER_MODULES:
            source = inspect.getsource(module)
            for node in ast.parse(source).body:
                if not isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef):
                    continue
                for decorator in node.decorator_list:
                    for keyword in getattr(decorator, "keywords", []):
                        if keyword.arg != "responses" or not isinstance(keyword.value, ast.Dict):
                            continue
                        codes = [k.value for k in keyword.value.keys if isinstance(k, ast.Constant)]
                        if 503 in codes:
                            self.assertIn("503", ast.get_source_segment(source, node), node.name)


class TestBroadcastDomainRoute(unittest.TestCase):
    def setUp(self):
        app = FastAPI()
        app.include_router(freedata.router, prefix="/freedata")
        app.state.ctx = mock.Mock()
        self.manager = mock.Mock()
        self.manager.get_broadcasts_per_domain_json.side_effect = lambda domain: {"domain": domain}
        self.manager.get_broadcast_domains_json.return_value = {"domains": []}
        patcher = mock.patch.object(freedata, "_mgr_broadcasts", return_value=self.manager)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(app)

    def test_with_and_without_slash(self):
        for path in ("/freedata/broadcasts/TEST-0/", "/freedata/broadcasts/TEST-0"):
            response = self.client.get(path, follow_redirects=False)
            self.assertEqual(response.status_code, 200, path)
            self.assertEqual(response.json(), {"domain": "TEST-0"})

    def test_domains_keeps_its_route(self):
        response = self.client.get("/freedata/broadcasts/domains")
        self.assertEqual(response.json(), {"domains": []})


if __name__ == "__main__":
    unittest.main()
