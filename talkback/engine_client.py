"""The HTTP client side of the engine contract: ``GET /`` liveness and
``POST /`` synthesis, over urllib. Always 127.0.0.1 and an explicit port."""

import http.client
import urllib.error
import urllib.request


def _url(port: int) -> str:
    return f"http://127.0.0.1:{int(port)}/"


def up(port: int, timeout: float = 1.0) -> bool:
    """``curl -sf --max-time 1``: True only for a 200 answer."""
    try:
        with urllib.request.urlopen(_url(port), timeout=timeout) as r:  # loopback only
            return r.status == 200
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        return False


def synth(port: int, text: str, timeout: float = 120.0) -> "bytes | None":
    """POST the text (utf-8, ``text/plain``) and return the raw body whatever
    the status — a 200 JSON error body comes back as-is so ``verify`` can name
    it. None on a connection error or timeout."""
    req = urllib.request.Request(
        _url(port),
        data=text.encode("utf-8"),
        method="POST",
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # loopback only
            return r.read()
    except urllib.error.HTTPError as e:
        try:
            return e.read()
        except (OSError, http.client.HTTPException):
            return b""
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        return None
