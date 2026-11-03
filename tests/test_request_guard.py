"""RequestGuard: other web sites cannot use the API, the GUI and scripts can."""

import socket
import unittest

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from freedata_server import request_guard
from freedata_server.request_guard import RequestGuard


def make_client(allowed_hosts=(), base_url="http://127.0.0.1:5000"):
    config = {"NETWORK": {"allowed_hosts": list(allowed_hosts)}}
    app = FastAPI()

    @app.get("/state")
    def state():
        return {"ok": True}

    @app.post("/modem/start")
    def start():
        return {"started": True}

    @app.websocket("/events")
    async def events(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    # as in server.py
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
    app.add_middleware(RequestGuard, get_config=lambda: config)
    return TestClient(app, base_url=base_url), config


class TestOrigin(unittest.TestCase):
    def test_gui_from_the_server_itself(self):
        client, _ = make_client()
        response = client.post("/modem/start", headers={"Origin": "http://127.0.0.1:5000"})
        self.assertEqual(response.status_code, 200)

    def test_gui_dev_server_on_another_port(self):
        client, _ = make_client(base_url="http://localhost:5000")
        response = client.post("/modem/start", headers={"Origin": "http://localhost:8080"})
        self.assertEqual(response.status_code, 200)

    def test_scripts_without_origin(self):
        client, _ = make_client()
        self.assertEqual(client.post("/modem/start").status_code, 200)

    def test_other_site_is_rejected(self):
        client, _ = make_client()
        response = client.post("/modem/start", headers={"Origin": "https://evil.example"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("evil.example", response.json()["error"])

    def test_other_site_preflight_is_rejected(self):
        client, _ = make_client()
        response = client.options(
            "/modem/start",
            headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
        )
        self.assertEqual(response.status_code, 403)

    def test_null_origin_is_rejected(self):
        client, _ = make_client()
        self.assertEqual(client.post("/modem/start", headers={"Origin": "null"}).status_code, 403)

    def test_other_site_in_allowed_hosts(self):
        client, _ = make_client(allowed_hosts=["radio.example.org"])
        response = client.get("/state", headers={"Origin": "https://radio.example.org"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["access-control-allow-origin"], "*")

    def test_allowed_hosts_is_read_on_every_request(self):
        client, config = make_client()
        headers = {"Origin": "https://radio.example.org"}
        self.assertEqual(client.get("/state", headers=headers).status_code, 403)
        config["NETWORK"]["allowed_hosts"] = ["radio.example.org"]
        self.assertEqual(client.get("/state", headers=headers).status_code, 200)


class TestHost(unittest.TestCase):
    def test_ip_addresses_and_local_names(self):
        for base_url in (
            "http://127.0.0.1:5000",
            "http://[::1]:5000",
            "http://192.168.1.20:5000",
            "http://localhost:5000",
            "http://shack.local:5000",
        ):
            client, _ = make_client(base_url=base_url)
            self.assertEqual(client.get("/state").status_code, 200, base_url)

    def test_this_computers_name(self):
        name = socket.gethostname().split(".")[0].lower()
        client, _ = make_client(base_url=f"http://{name}:5000")
        self.assertEqual(client.get("/state").status_code, 200)

    def test_dns_rebinding_is_rejected(self):
        # evil.example resolved to 127.0.0.1: the page's requests are same-origin
        client, _ = make_client(base_url="http://evil.example:5000")
        response = client.post("/modem/start", headers={"Origin": "http://evil.example:5000"})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(client.get("/state").status_code, 403)

    def test_name_in_allowed_hosts(self):
        client, _ = make_client(allowed_hosts=["Radio.Example.org"], base_url="http://radio.example.org:5000")
        self.assertEqual(client.get("/state").status_code, 200)


class TestWebsocket(unittest.TestCase):
    def test_same_origin(self):
        client, _ = make_client()
        with client.websocket_connect("ws://127.0.0.1:5000/events", headers={"Origin": "http://127.0.0.1:5000"}) as ws:
            self.assertEqual(ws.receive_text(), "hello")

    def test_other_site_is_rejected(self):
        client, _ = make_client()
        with self.assertRaises(WebSocketDisconnect):
            with client.websocket_connect("ws://127.0.0.1:5000/events", headers={"Origin": "https://evil.example"}):
                pass


class TestSplitHost(unittest.TestCase):
    def test_split_host(self):
        self.assertEqual(request_guard.split_host("LocalHost:5000"), "localhost")
        self.assertEqual(request_guard.split_host("[::1]:5000"), "::1")
        self.assertEqual(request_guard.split_host("example.org."), "example.org")
        self.assertEqual(request_guard.split_host("10.0.0.1"), "10.0.0.1")


if __name__ == "__main__":
    unittest.main()
