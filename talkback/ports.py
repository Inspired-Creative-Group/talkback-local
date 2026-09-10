"""Is the port free, held by a Talkback server, or busy? Owner: installer.

The installer asks this before it writes a single file: a port that something
else holds would leave the hooks talking to a stranger. Standard library only,
loopback only.
"""

import http.client

FREE, TALKBACK, BUSY = "free", "talkback", "busy"


def check_port(port: int, timeout: float = 1.5) -> str:
    """``"free"`` when nothing accepts a connection on 127.0.0.1:``port``;
    ``"talkback"`` when what answers is an HTTP server whose ``GET /`` is a
    200 ``ok`` (the engine's liveness contract); ``"busy"`` for anything
    else — a listener that stays silent (after ``timeout`` seconds), speaks
    something other than HTTP, or answers HTTP with a different body."""
    port = int(port)
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.connect()
    except ConnectionRefusedError:
        return FREE
    except OSError:
        # a connect that times out or is reset: something holds the port
        return BUSY
    try:
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read(64)
    except (OSError, http.client.HTTPException):  # TimeoutError is an OSError
        return BUSY
    finally:
        conn.close()
    if resp.status == 200 and body.strip() == b"ok":
        return TALKBACK
    return BUSY


def explain(state: str, port: int) -> str:
    """One line for a person, per state."""
    port = int(port)
    if state == FREE:
        return f"port {port} is free"
    if state == TALKBACK:
        return f"a Talkback server already answers on port {port} — upgrading in place"
    return f"Port {port} is in use by something that is not Talkback. Set KOKORO_PORT to a free port and re-run."
