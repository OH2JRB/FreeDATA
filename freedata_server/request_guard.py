"""Keeps other web sites away from the API.

The API has no login, so a page open in the same browser could otherwise
call it: key the transmitter, change the config, delete messages. CORS alone
does not stop that, a browser sends simple requests (a POST without a body,
a form) to any server and only hides the response, and it does not apply to
websockets at all.

Two checks run for every HTTP and websocket request, before anything else:

- Host: the name the browser used to reach the server must be an IP
  address, localhost, this computer's name, a *.local name or one listed in
  NETWORK.allowed_hosts. This stops DNS rebinding, where a site's own name
  is pointed at 127.0.0.1 and its requests look same-origin.
- Origin: a browser request from a page must come from the same host name
  (any port, so the GUI dev server on port 8080 works), or from a host in
  NETWORK.allowed_hosts. Requests without an Origin header (scripts, curl,
  other programs) are not browser cross-site requests and are let through.

Rejected requests get 403 and are logged with the name to add to
allowed_hosts if the access was wanted.
"""

import ipaddress
import json
import socket
from urllib.parse import urlsplit

import structlog

log = structlog.get_logger("request_guard")


def split_host(host):
    """The host name of a Host header value, without the port, lower case."""
    host = host.strip().lower()
    if host.startswith("["):  # [::1]:5000
        return host[1 : host.find("]")] if "]" in host else host[1:]
    if host.count(":") == 1:
        host = host.split(":")[0]
    return host.rstrip(".")


def is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def own_names():
    """This computer's names, as a browser on the network may use them."""
    name = socket.gethostname().lower()
    short = name.split(".")[0]
    return {name, short, f"{short}.local"}


def allowed_hosts(config):
    hosts = config.get("NETWORK", {}).get("allowed_hosts") or []
    return {split_host(str(h)) for h in hosts if str(h).strip()}


def host_allowed(host, extra_hosts):
    if not host:
        return True  # HTTP/1.0 without a Host header: not from a browser
    if is_ip(host) or host == "localhost" or host.endswith((".localhost", ".local")):
        return True
    return host in extra_hosts or host in own_names()


def origin_allowed(origin, host, extra_hosts):
    if origin is None:
        return True
    origin_host = urlsplit(origin).hostname  # None for "null" (file:// pages, sandboxed frames)
    if not origin_host:
        return False
    origin_host = origin_host.rstrip(".")
    return origin_host == host or origin_host in extra_hosts


class RequestGuard:
    """ASGI middleware applying the Host and Origin checks."""

    def __init__(self, app, get_config):
        self.app = app
        self.get_config = get_config

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
        host = split_host(headers.get("host", ""))
        origin = headers.get("origin")
        extra_hosts = allowed_hosts(self.get_config())

        if not host_allowed(host, extra_hosts):
            reason = f"host '{host}' is not allowed, add it to NETWORK.allowed_hosts if it is yours"
        elif not origin_allowed(origin, host, extra_hosts):
            reason = f"origin '{origin}' is not allowed, add its host to NETWORK.allowed_hosts if it is yours"
        else:
            return await self.app(scope, receive, send)

        log.warning("[GUARD] Request rejected", path=scope.get("path"), reason=reason)
        if scope["type"] == "websocket":
            # closing before accept makes the server answer the handshake with 403
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"error": f"Forbidden: {reason}"}).encode()
        await send({
            "type": "http.response.start",
            "status": 403,
            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
        })
        await send({"type": "http.response.body", "body": body})
