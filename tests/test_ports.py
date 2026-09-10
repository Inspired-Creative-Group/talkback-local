"""``talkback.ports.check_port``: the question install.sh and install.ps1 ask
before they write anything. Loopback sockets only, never 8910, in-process.

Pure: no sandbox, no subprocess — a socket bound to port 0 and an in-process
HTTP server thread are all it needs, so it also runs on windows-latest."""

import http.server
import socket
import threading
import time

import pytest

from talkback import ports

pytestmark = pytest.mark.pure

FORBIDDEN_PORT = 8910


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def silent_listener():
    """Accepts the TCP connection and never says a word."""
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    port = s.getsockname()[1]
    assert port != FORBIDDEN_PORT
    yield port
    s.close()


def _http_server(body: bytes, status: int = 200):
    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    srv.daemon_threads = True
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv, t


@pytest.fixture
def http_ok():
    srv, t = _http_server(b"ok")
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    t.join(timeout=5)


@pytest.fixture
def http_other():
    srv, t = _http_server(b"<html>hello</html>")
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    t.join(timeout=5)


def test_closed_port_is_free():
    port = _free_port()  # bound to 0 and released: nothing listens there now
    assert ports.check_port(port) == ports.FREE


def test_silent_listener_is_busy_within_the_timeout(silent_listener):
    t0 = time.monotonic()
    assert ports.check_port(silent_listener, timeout=1.5) == ports.BUSY
    assert time.monotonic() - t0 < 2.5  # the 1.5 s read timeout, not a hang


def test_http_ok_is_a_talkback_server(http_ok):
    assert ports.check_port(http_ok) == ports.TALKBACK


def test_http_with_another_body_is_busy(http_other):
    assert ports.check_port(http_other) == ports.BUSY


def test_http_ok_with_a_non_200_status_is_busy():
    srv, t = _http_server(b"ok", status=503)
    try:
        assert ports.check_port(srv.server_address[1]) == ports.BUSY
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_explain_names_the_port_and_the_fix():
    assert ports.explain(ports.FREE, 8930) == "port 8930 is free"
    assert "upgrading in place" in ports.explain(ports.TALKBACK, 8930)
    busy = ports.explain(ports.BUSY, 8930)
    assert busy == "Port 8930 is in use by something that is not Talkback. Set KOKORO_PORT to a free port and re-run."
