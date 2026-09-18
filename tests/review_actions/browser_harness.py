"""Drive the shipped pages in a real headless Chromium, against a synthetic loopback stub.

Nothing here touches a live archive, a real document or a model provider. The stub server
serves the real files from ``docflow/web/static`` plus scripted JSON for the API routes the
page calls, and records every request. The browser is started with external name resolution
disabled (``--host-resolver-rules``) and with background networking off, so a page that
tried to reach the internet would fail rather than leak; the only reachable host is the
loopback stub.

Transport is the DevTools protocol over a loopback WebSocket, implemented here with the
standard library only — the suite gains no new dependency. Every subprocess is bounded by a
timeout and terminated (then killed) in a ``finally`` block.
"""
from __future__ import annotations

import base64
import json
import os
import re
import secrets
import shutil
import signal
import socket
import struct
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[2] / "docflow" / "web" / "static"
LOOPBACK = "127.0.0.1"

# Captured at import, before any test denies egress: the harness talks to the loopback
# stub and to the browser's loopback debugging port only. Tests keep the global deny.
_CONNECT = socket.socket.connect

BROWSERS = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
DASHBOARD_READY = "typeof handleFiles === 'function'"
# The public hosts the shipped pages themselves reference. Only a test that needs the
# real stylesheet applied opens these; every other run keeps the total deny below.
ASSET_HOSTS = ("cdn.tailwindcss.com", "fonts.googleapis.com", "fonts.gstatic.com")
# ``bg-canvas`` on <body> resolves through the shipped config.js token table, so this
# colour appears only once the real Tailwind CDN has compiled with the real config.
STYLES_READY = ("return getComputedStyle(document.body).backgroundColor"
                " === 'rgb(244, 241, 234)';")
# The navigation events the harness synchronises on; every other event is dropped.
NAV_EVENTS = ("Page.frameNavigated", "Page.lifecycleEvent")
LAUNCH_TIMEOUT = 30.0
CALL_TIMEOUT = 20.0
PAGES = {"/": "dashboard.html", "/review": "review.html", "/archive": "archive.html",
         "/settings": "settings.html"}


def browser_binary() -> str:
    for name in BROWSERS:
        found = shutil.which(name)
        if found:
            return found
    pytest.skip("no Chromium/Chrome binary available for browser coverage")


# ---------------------------------------------------------------------------
# Synthetic loopback stub
# ---------------------------------------------------------------------------

class StubServer:
    """Serve the real static pages and scripted JSON; record what the page asked for."""

    def __init__(self, routes: list[dict], port: int = 0) -> None:
        # ``port`` is named only by a test that restarts the server under a page that
        # is already open: the browser keeps talking to the origin it loaded from, so
        # the replacement has to come back on the same one.
        self.routes = list(routes)
        self.requests: list[dict] = []
        harness = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:  # keep the test output clean
                pass

            def _send(self, status: int, body: bytes, content_type: str) -> None:
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(body)
                self.close_connection = True

            def _drain(self) -> None:
                """Consume the request body so the connection is never left mid-message."""
                if (self.headers.get("Transfer-Encoding") or "").lower() == "chunked":
                    while True:
                        size = int(self.rfile.readline().split(b";")[0] or b"0", 16)
                        self.rfile.read(size + 2)
                        if size == 0:
                            return
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)

            def _handle(self, method: str) -> None:
                self._drain()
                path = self.path
                harness.requests.append({"method": method, "path": path})
                page = PAGES.get(path.split("?")[0])
                if method == "GET" and page:
                    self._send(200, (STATIC / page).read_bytes(), "text/html; charset=utf-8")
                    return
                if method == "GET" and path.startswith("/static/"):
                    name = path.split("?")[0][len("/static/"):]
                    target = STATIC / name
                    if name and "/" not in name and target.is_file():
                        kind = ("text/javascript" if target.suffix == ".js"
                                else "text/html; charset=utf-8")
                        self._send(200, target.read_bytes(), kind)
                        return
                route = harness.match(method, path)
                if route is None:
                    self._send(404, json.dumps(
                        {"error": {"code": "not_found", "message": "Not Found"}}).encode(),
                        "application/json")
                    return
                self._send(int(route.get("status", 200)),
                           json.dumps(route.get("body", {})).encode(), "application/json")

            def do_GET(self) -> None:
                self._handle("GET")

            def do_POST(self) -> None:
                self._handle("POST")

        self.server = ThreadingHTTPServer((LOOPBACK, port), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def origin(self) -> str:
        return f"http://{LOOPBACK}:{self.port}"

    def match(self, method: str, path: str) -> dict | None:
        for route in self.routes:
            if route["method"] == method and re.search(route["url"], path):
                if route.get("once"):
                    self.routes.remove(route)
                return route
        return None

    def respond(self, route: dict) -> None:
        """Change what the next matching call answers, mid-scenario."""
        self.routes.insert(0, route)

    def calls(self, method: str, path: str) -> list[dict]:
        return [r for r in self.requests
                if r["method"] == method and r["path"].split("?")[0] == path]

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


# ---------------------------------------------------------------------------
# Minimal WebSocket client (RFC 6455 text frames) for the DevTools protocol
# ---------------------------------------------------------------------------

class _WebSocket:
    def __init__(self, host: str, port: int, path: str, timeout: float) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        _CONNECT(self.sock, (host, port))
        key = base64.b64encode(secrets.token_bytes(16)).decode()
        self.sock.sendall(
            f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n".encode())
        self.buf = b""
        while b"\r\n\r\n" not in self.buf:
            self.buf += self._read()
        head, _, self.buf = self.buf.partition(b"\r\n\r\n")
        if b" 101 " not in head.split(b"\r\n")[0] + b" ":
            raise RuntimeError(f"devtools refused the upgrade: {head[:120]!r}")

    def _read(self) -> bytes:
        chunk = self.sock.recv(65536)
        if not chunk:
            raise RuntimeError("devtools websocket closed")
        return chunk

    def _need(self, count: int) -> None:
        while len(self.buf) < count:
            self.buf += self._read()

    def send(self, text: str) -> None:
        data = text.encode()
        mask = secrets.token_bytes(4)
        header = bytearray([0x81])
        size = len(data)
        if size < 126:
            header.append(0x80 | size)
        elif size < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack(">H", size)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", size)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def recv(self) -> str:
        while True:
            self._need(2)
            opcode, length, offset = self.buf[0] & 0x0F, self.buf[1] & 0x7F, 2
            if length == 126:
                self._need(4)
                length, offset = struct.unpack(">H", self.buf[2:4])[0], 4
            elif length == 127:
                self._need(10)
                length, offset = struct.unpack(">Q", self.buf[2:10])[0], 10
            self._need(offset + length)
            payload, self.buf = self.buf[offset:offset + length], self.buf[offset + length:]
            if opcode in (1, 2):
                return payload.decode()
            if opcode == 8:
                raise RuntimeError("devtools websocket closed by the browser")

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass


def _http_get_json(port: int, path: str, timeout: float):
    """One bounded loopback GET of a DevTools JSON endpoint, read by Content-Length."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        _CONNECT(sock, (LOOPBACK, port))
        sock.sendall(f"GET {path} HTTP/1.1\r\nHost: {LOOPBACK}:{port}\r\n\r\n".encode())
        raw = b""
        while b"\r\n\r\n" not in raw:
            chunk = sock.recv(65536)
            if not chunk:
                raise OSError("devtools closed the connection")
            raw += chunk
        head, _, body = raw.partition(b"\r\n\r\n")
        match = re.search(rb"(?i)content-length:\s*(\d+)", head)
        if match is None:
            raise OSError("devtools sent no content length")
        while len(body) < int(match.group(1)):
            chunk = sock.recv(65536)
            if not chunk:
                raise OSError("devtools closed the connection")
            body += chunk
    finally:
        sock.close()
    return json.loads(body.decode())


# ---------------------------------------------------------------------------
# Browser
# ---------------------------------------------------------------------------

class Browser:
    """One bounded headless browser process with one attached page."""

    def __init__(self, binary: str, profile: Path, url: str,
                 allow_hosts: tuple[str, ...] = ()) -> None:
        self.port = _free_port()
        # Nothing but the loopback stub is resolvable unless a test names further hosts.
        # The only callers that do are the layout tests, which need the page's own
        # public stylesheet to actually apply before geometry means anything.
        resolver = ", ".join(["MAP * ~NOTFOUND", f"EXCLUDE {LOOPBACK}",
                              *(f"EXCLUDE {host}" for host in allow_hosts)])
        self.process = subprocess.Popen(
            [binary, "--headless=new", f"--remote-debugging-port={self.port}",
             f"--user-data-dir={profile}", "--no-sandbox", "--disable-gpu",
             "--disable-dev-shm-usage", "--no-first-run", "--no-default-browser-check",
             "--disable-background-networking", "--disable-component-update",
             "--disable-default-apps", "--disable-sync", "--no-pings",
             f"--host-resolver-rules={resolver}", url],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env={**os.environ, "HOME": str(profile)},
            # Its own session: the launcher may be a wrapper that execs the real
            # browser, so teardown signals the whole group rather than the wrapper.
            start_new_session=True)
        self.next_id = 0
        self.events: list[dict] = []
        self.ws = _WebSocket(LOOPBACK, self.port, self._page_target(), CALL_TIMEOUT)
        # Navigation is driven and awaited through the Page domain: a readiness marker
        # alone cannot tell the incoming document from the one being replaced.
        self.call("Page.enable")
        self.call("Page.setLifecycleEventsEnabled", enabled=True)

    def _page_target(self) -> str:
        deadline = time.monotonic() + LAUNCH_TIMEOUT
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError("the browser exited before devtools came up")
            try:
                targets = _http_get_json(self.port, "/json/list", 2.0)
            except (OSError, ValueError):
                time.sleep(0.2)
                continue
            pages = [t for t in targets
                     if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
            if pages:
                return pages[0]["webSocketDebuggerUrl"].split(f"{self.port}", 1)[1]
            time.sleep(0.2)
        raise TimeoutError("the browser did not expose a page target in time")

    def call(self, method: str, **params):
        self.next_id += 1
        call_id = self.next_id
        self.ws.send(json.dumps({"id": call_id, "method": method, "params": params}))
        deadline = time.monotonic() + CALL_TIMEOUT
        while time.monotonic() < deadline:
            message = self._message()
            if message.get("id") == call_id:
                if "error" in message:
                    raise RuntimeError(f"{method} failed: {message['error']}")
                return message["result"]
        raise TimeoutError(f"{method} did not answer in time")

    def _message(self) -> dict:
        """Read one devtools message, keeping the navigation events for later waits."""
        message = json.loads(self.ws.recv())
        if message.get("method") in NAV_EVENTS:
            self.events.append(message)
        return message

    def _wait_event(self, method: str, matches, what: str) -> dict:
        """Return the first buffered or incoming ``method`` event that ``matches``."""
        deadline = time.monotonic() + CALL_TIMEOUT
        seen = 0
        while time.monotonic() < deadline:
            while seen < len(self.events):
                event = self.events[seen]
                seen += 1
                if event["method"] == method and matches(event["params"]):
                    return event["params"]
            self._message()
        raise TimeoutError(f"the browser did not report {what} in time")

    def evaluate(self, expression: str):
        """Evaluate in the page and return the value (promises are awaited)."""
        result = self.call("Runtime.evaluate", expression=f"(() => {{ {expression} }})()",
                           returnByValue=True, awaitPromise=True)
        if result.get("exceptionDetails"):
            detail = result["exceptionDetails"]
            text = (detail.get("exception") or {}).get("description") or detail.get("text")
            raise AssertionError(f"page script raised: {text}")
        return result["result"].get("value")

    def wait_for(self, expression: str, timeout: float = 15.0, what: str = ""):
        """Poll a page expression until it is truthy; return its value."""
        deadline = time.monotonic() + timeout
        value = None
        while time.monotonic() < deadline:
            value = self.evaluate(expression)
            if value:
                return value
            time.sleep(0.1)
        raise AssertionError(f"timed out waiting for {what or expression}: last={value!r}")

    def open(self, url: str, marker: str = DASHBOARD_READY) -> None:
        outgoing = self._begin_navigation()
        result = self.call("Page.navigate", url=url)
        if result.get("errorText"):
            raise AssertionError(f"navigating to {url} failed: {result['errorText']}")
        self._await_document(outgoing, marker, result.get("loaderId"))

    def reload(self, marker: str = DASHBOARD_READY) -> None:
        outgoing = self._begin_navigation()
        self.call("Page.reload")
        self._await_document(outgoing, marker)

    def _begin_navigation(self) -> str:
        """Drop stale navigation events and name the document about to be replaced."""
        self.events.clear()
        return self.call("Page.getFrameTree")["frameTree"]["frame"]["loaderId"]

    def _await_document(self, outgoing: str, marker: str,
                        loader_id: str | None = None) -> None:
        """Block until the document that replaced ``outgoing`` has fired its load event.

        Waiting on the marker alone is not enough: the outgoing document answers it too,
        so a check can pass against a page that is already on its way out and leave the
        next call to land mid-parse in the incoming one.
        """
        if loader_id == outgoing:  # same-document navigation: nothing is replaced
            self.wait_ready(marker)
            return
        if loader_id is None:
            navigated = self._wait_event(
                "Page.frameNavigated",
                lambda p: (p["frame"].get("parentId") is None
                           and p["frame"]["loaderId"] != outgoing),
                "the new document")
            loader_id = navigated["frame"]["loaderId"]
        self._wait_event(
            "Page.lifecycleEvent",
            lambda p: p.get("loaderId") == loader_id and p.get("name") == "load",
            "the new document's load event")
        self.wait_ready(marker)

    def emulate(self, width: int, height: int, mobile: bool, scale: float = 1) -> None:
        """Adopt a device viewport for everything navigated from here on.

        ``mobile`` is what makes this a phone rather than a narrow desktop window: it
        turns on the visual/layout viewport split, so a page whose content cannot fit
        is zoomed out into a wider layout viewport instead of simply being cut off.
        """
        self.call("Emulation.setDeviceMetricsOverride", width=width, height=height,
                  deviceScaleFactor=scale, mobile=mobile)

    def wait_ready(self, marker: str = DASHBOARD_READY) -> None:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            try:
                if self.evaluate(
                        f"return document.readyState === 'complete' && ({marker});"):
                    return
            except (RuntimeError, AssertionError):
                pass  # the execution context is replaced mid-navigation
            time.sleep(0.1)
        raise AssertionError("the page did not finish loading")

    def close(self) -> None:
        """Shut the browser down: ask it to quit, then signal the whole process group."""
        try:
            self.call("Browser.close")
        except (OSError, RuntimeError, TimeoutError):
            pass
        try:
            self.ws.close()
        finally:
            self._signal_group(signal.SIGTERM)
            try:
                self.process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._signal_group(signal.SIGKILL)
                self.process.wait(timeout=10)

    def _signal_group(self, sig: int) -> None:
        try:
            os.killpg(os.getpgid(self.process.pid), sig)
        except (ProcessLookupError, PermissionError):
            pass


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOOPBACK, 0))
        return sock.getsockname()[1]


# A selection a real user makes: a synthetic in-memory PDF put on the real file input.
def select_file(name: str) -> str:
    return f"""
        const input = document.getElementById('upload-input');
        const transfer = new DataTransfer();
        transfer.items.add(new File([new Blob(['%PDF-1.4 synthetic'])],
                                    {json.dumps(name)}, {{type: 'application/pdf'}}));
        input.files = transfer.files;
        input.dispatchEvent(new Event('change'));
        return null;
    """


# Tailwind is a CDN script the sealed browser cannot fetch, so ``hidden`` carries no
# style here. Visibility is therefore asserted on the class the page itself controls.
def hidden(element_id: str) -> str:
    return (f"return document.getElementById({json.dumps(element_id)})"
            ".classList.contains('hidden');")


def text_of(element_id: str) -> str:
    return (f"return document.getElementById({json.dumps(element_id)})"
            ".textContent.trim();")


def anchor_texts() -> str:
    return ("return Array.from(document.querySelectorAll('a'))"
            ".map((a) => [a.getAttribute('href'), a.textContent.trim()]);")
