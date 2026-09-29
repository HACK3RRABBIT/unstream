"""Proxy routing, proxy discovery and failure classification in app/net.py.

The proxies here are real sockets on loopback, so these tests exercise the
same urlopen() path every provider takes.
"""

import socket
import socketserver
import struct
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from starlette.testclient import TestClient

from app import main, net, ytdlp


class _Origin(BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"origin"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class _HttpProxy(BaseHTTPRequestHandler):
    """Forwards absolute-URI GETs; answers CONNECT without dialling out."""

    seen: list[str] = []

    def do_GET(self):
        type(self).seen.append(self.path)
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            self.path, timeout=5
        ) as resp:
            body = resp.read()
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_CONNECT(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):
        pass


class _Socks5(socketserver.BaseRequestHandler):
    """Minimal no-auth SOCKS5 that records the host it was asked for."""

    seen: list[str] = []

    def handle(self):
        sock = self.request
        sock.recv(3)
        sock.sendall(b"\x05\x00")
        _ver, _cmd, _rsv, atyp = sock.recv(4)
        if atyp == 3:
            host = sock.recv(sock.recv(1)[0]).decode()
        else:
            host = socket.inet_ntoa(sock.recv(4))
        (port,) = struct.unpack(">H", sock.recv(2))
        type(self).seen.append(host)
        upstream = socket.create_connection(("127.0.0.1" if host == "localhost" else host, port))
        sock.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("127.0.0.1") + struct.pack(">H", port))
        sock.settimeout(2)
        upstream.settimeout(2)
        request = sock.recv(65536)
        upstream.sendall(request)
        while chunk := upstream.recv(65536):
            sock.sendall(chunk)
        upstream.close()


class _ThreadingTCP(socketserver.ThreadingMixIn, socketserver.TCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, *args):
        pass  # detect() speaks HTTP at it on purpose


class _Http(HTTPServer):
    def handle_error(self, *args):
        pass  # ...and SOCKS at this one


def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def origin():
    server = _serve(_Http(("127.0.0.1", 0), _Origin))
    yield f"http://localhost:{server.server_address[1]}/"
    server.shutdown()


@pytest.fixture
def http_proxy():
    _HttpProxy.seen = []
    server = _serve(_Http(("127.0.0.1", 0), _HttpProxy))
    yield server.server_address[1]
    server.shutdown()


@pytest.fixture
def socks_proxy():
    _Socks5.seen = []
    server = _serve(_ThreadingTCP(("127.0.0.1", 0), _Socks5))
    yield server.server_address[1]
    server.shutdown()


@pytest.fixture(autouse=True)
def _restore_setting(monkeypatch):
    for var in ("http_proxy", "https_proxy", "all_proxy", "no_proxy",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
        monkeypatch.delenv(var, raising=False)
    before = net.setting()
    yield
    net.configure(before)


def _fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as resp:
        return resp.read()


def test_normalize_prefers_remote_dns_and_rejects_junk():
    assert net.normalize("") == net.SYSTEM
    assert net.normalize("Direct") == net.OFF
    assert net.normalize("127.0.0.1:10809") == "http://127.0.0.1:10809"
    assert net.normalize("socks5://127.0.0.1:10808") == "socks5h://127.0.0.1:10808"
    assert net.normalize("socks5://user:pw@127.0.0.1:1080") == "socks5h://user:pw@127.0.0.1:1080"
    for bad in ("ftp://127.0.0.1:21", "http://127.0.0.1", "socks5://:1080"):
        with pytest.raises(ValueError):
            net.normalize(bad)


def test_custom_http_proxy_carries_plain_urlopen(origin, http_proxy):
    net.configure(f"http://127.0.0.1:{http_proxy}")
    assert _fetch(origin) == b"origin"
    assert _HttpProxy.seen == [origin]


def test_custom_socks_proxy_resolves_names_remotely(origin, socks_proxy):
    net.configure(f"socks5://127.0.0.1:{socks_proxy}")
    assert _fetch(origin) == b"origin"
    # socks5h: the proxy was handed the name, not a locally resolved address.
    assert _Socks5.seen == ["localhost"]


def test_system_mode_picks_up_a_proxy_set_after_start(origin, http_proxy, monkeypatch):
    net.configure("system")
    assert _fetch(origin) == b"origin"
    assert _HttpProxy.seen == []

    # The VPN is switched on while the app is already running.
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{http_proxy}")
    assert _fetch(origin) == b"origin"
    assert _HttpProxy.seen == [origin]


def test_off_ignores_the_system_proxy(origin, http_proxy, monkeypatch):
    monkeypatch.setenv("http_proxy", f"http://127.0.0.1:{http_proxy}")
    net.configure("off")
    assert _fetch(origin) == b"origin"
    assert _HttpProxy.seen == []


def test_ytdlp_gets_the_same_proxy():
    net.configure("system")
    assert "proxy" not in ytdlp.base_opts()
    net.configure("off")
    assert ytdlp.base_opts()["proxy"] == ""
    net.configure("socks5://127.0.0.1:1080")
    assert ytdlp.base_opts()["proxy"] == "socks5h://127.0.0.1:1080"


def test_detect_finds_http_and_socks_proxies(http_proxy, socks_proxy, monkeypatch):
    monkeypatch.setattr(net, "COMMON_PORTS", (http_proxy, socks_proxy, 1))
    found = net.detect()["found"]
    assert f"http://127.0.0.1:{http_proxy}" in found
    assert f"socks5h://127.0.0.1:{socks_proxy}" in found
    assert len(found) == 2


def test_detect_skips_an_ordinary_web_server(origin, monkeypatch):
    port = int(origin.rsplit(":", 1)[1].strip("/"))
    monkeypatch.setattr(net, "COMMON_PORTS", (port,))
    assert net.detect()["found"] == []


def test_a_dead_proxy_is_reported_as_the_proxy(monkeypatch):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead = probe.getsockname()[1]
    net.configure(f"http://127.0.0.1:{dead}")
    monkeypatch.setattr(net, "SERVICES", (("deezer", "http://example.invalid/"),))
    [row] = net.check_services()
    assert row == {**row, "id": "deezer", "ok": False, "error": "proxy"}


@pytest.mark.parametrize(
    ("message", "kind"),
    [
        (ytdlp.bot_check_message(), "bot_check"),
        ("ERROR: [youtube] x: Sign in to confirm you're not a bot", "bot_check"),
        ("Could not reach Deezer: timed out", "network"),
        ("Failed after 4 attempts: <urlopen error [Errno 61] Connection refused>", "network"),
        ("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed", "tls"),
        ("ERROR: Could not copy Chrome cookie database", "cookies"),
        ("ffmpeg conversion failed: bad input", "encoding"),
        ("Failed after 4 attempts: No results found", "not_found"),
        ("This video is DRM protected", "other"),
        (None, None),
    ],
)
def test_error_kind(message, kind):
    assert net.error_kind(message) == kind


def test_unreadable_cookie_store_is_skipped_not_fatal(monkeypatch):
    calls = []

    def locked(browser, logger=None):
        calls.append(browser)
        raise PermissionError("Could not copy Chrome cookie database")

    monkeypatch.setattr(ytdlp, "extract_cookies_from_browser", locked)
    ytdlp.set_cookies_from_browser("chrome")
    try:
        opts = ytdlp.base_opts()
        assert "cookiesfrombrowser" not in opts
        status = ytdlp.browser_cookie_status()
        assert status["ok"] is False
        assert "cookie database" in status["error"]
        ytdlp.base_opts()
        assert calls == ["chrome"]  # cached, not re-read per extraction
    finally:
        ytdlp.set_cookies_from_browser("")


def test_readable_cookie_store_is_handed_to_ytdlp(monkeypatch):
    from http.cookiejar import Cookie, CookieJar

    def cookie(name):
        return Cookie(0, name, "v", None, False, ".youtube.com", True, True, "/",
                      True, True, None, False, None, None, {})

    def readable(browser, logger=None):
        jar = CookieJar()
        jar.set_cookie(cookie("SAPISID"))
        jar.set_cookie(cookie("PREF"))
        return jar

    monkeypatch.setattr(ytdlp, "extract_cookies_from_browser", readable)
    ytdlp.set_cookies_from_browser("firefox")
    try:
        assert ytdlp.base_opts()["cookiesfrombrowser"] == ("firefox",)
        status = ytdlp.browser_cookie_status()
        assert status["signed_in"] is True
        assert status["youtube_cookies"] == 2
    finally:
        ytdlp.set_cookies_from_browser("")


def test_config_endpoint_validates_and_applies_proxy():
    client = TestClient(main.app)
    res = client.post("/api/desktop/config", json={"proxy": "socks5://127.0.0.1:10808"})
    assert res.status_code == 200
    assert res.json()["proxy"] == "socks5h://127.0.0.1:10808"
    assert net.setting() == "socks5h://127.0.0.1:10808"

    res = client.post("/api/desktop/config", json={"proxy": "ftp://nope"})
    assert res.status_code == 400
    assert res.json()["detail"].startswith("Bad proxy")
    assert net.setting() == "socks5h://127.0.0.1:10808"


def test_search_that_hears_from_nobody_is_an_error(monkeypatch):
    def hang(*_args):
        time.sleep(0.5)
        return []

    monkeypatch.setattr(main, "SEARCH_TIMEOUT_SECONDS", 0.05)
    for name in ("deezer", "itunes"):
        monkeypatch.setattr(getattr(main, name), "search", hang)
    monkeypatch.setattr(main.soundcloud, "search", hang)
    monkeypatch.setattr(main.ytdlp, "search_youtube", hang)
    with pytest.raises(main.ProviderError, match="^Search failed"):
        main.search_any("anything")


def test_a_timeout_mid_body_is_unreachable_not_a_500(monkeypatch):
    def slow(_url):
        raise TimeoutError("The read operation timed out")

    monkeypatch.setattr(main, "resolve_any", slow)
    client = TestClient(main.app, raise_server_exceptions=False)
    res = client.post("/api/resolve", json={"url": "https://www.deezer.com/album/1"})
    assert res.status_code == 400
    assert res.json()["detail"].startswith("Could not reach")
