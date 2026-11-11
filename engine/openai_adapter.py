"""Optional OpenAI-compatible front door for the Kokoro server. Off by default.

Talkback's own engine (engine/server.py) speaks a tiny private protocol: POST /
with the text as the body, raw PCM back. Plenty of clients only know OpenAI's
speech API, so this small stdlib process translates between the two:

    POST /v1/audio/speech   {"input": "...", "voice": "...", "response_format": "..."}
    GET  /v1/models         a one-entry model list ("kokoro")
    GET  /health            "ok" (no key needed, says nothing else)

It forwards the text to the engine, waits for the whole reply, and returns it
as wav or pcm (stdlib) or mp3 / opus / flac (encoded with ffmpeg). Nothing here
loads a model; the engine stays the only process holding Kokoro.

It is NOT installed or started by install.sh. Run it yourself, or under your own
launch agent, when a client needs it. It refuses to start without a key:

    KOKORO_OPENAI_KEY_FILE=~/.config/kokoro-openai/key \
        python3 engine/openai_adapter.py

Every /v1 request must carry "Authorization: Bearer <that key>"; anything else
gets 401. It binds 127.0.0.1 unless told otherwise. Think hard before binding
0.0.0.0: that publishes your speaker to the whole LAN.

Environment:
    KOKORO_OPENAI_KEY_FILE  file holding the bearer key (required; chmod 600)
    KOKORO_OPENAI_HOST      bind address, default 127.0.0.1
    KOKORO_OPENAI_PORT      listen port, default 8921
    KOKORO_URL              the engine, default http://127.0.0.1:$KOKORO_PORT/
    KOKORO_PORT             the engine's port, default 8910
    KOKORO_OPENAI_MAX_CHARS longest input accepted, default 4096
    FFMPEG                  ffmpeg binary, default "ffmpeg" on PATH

Mapping notes:
    voice   a Kokoro voice name (e.g. "af_heart") is passed to the engine as
            ?voice=. Anything else ("alloy", "default", "kokoro", "") uses the
            engine's installed voice. If the engine returns no audio for a
            named voice, the request is retried once with the default voice.
    speed   accepted and ignored; the engine's speed is fixed at launch.
    model   accepted and ignored; there is one model.
"""

import hmac
import io
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

RATE = 24000  # the engine's output: s16le, 24 kHz, mono
MAX_BODY = 256 * 1024
UPSTREAM_TIMEOUT = 180

# Kokoro voice names look like "af_heart", "bm_george": two letters, underscore, name.
KOKORO_VOICE = re.compile(r"^[a-z]{2}_[a-z0-9_]{1,40}$")

FORMATS = {
    # name: (content type, ffmpeg output args or None for stdlib)
    "wav": ("audio/wav", None),
    "pcm": ("audio/pcm", None),
    "mp3": ("audio/mpeg", ["-c:a", "libmp3lame", "-b:a", "96k", "-f", "mp3"]),
    "opus": ("audio/ogg", ["-c:a", "libopus", "-b:a", "64k", "-f", "ogg"]),
    "flac": ("audio/flac", ["-c:a", "flac", "-f", "flac"]),
}


class UpstreamError(Exception):
    pass


def to_wav(pcm):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return buf.getvalue()


def encode(pcm, fmt, ffmpeg="ffmpeg"):
    if fmt == "pcm":
        return pcm
    if fmt == "wav":
        return to_wav(pcm)
    args = [ffmpeg, "-hide_banner", "-loglevel", "error",
            "-f", "s16le", "-ar", str(RATE), "-ac", "1", "-i", "pipe:0",
            *FORMATS[fmt][1], "pipe:1"]
    out = subprocess.run(args, input=pcm, capture_output=True, timeout=120, check=False)
    if out.returncode != 0 or not out.stdout:
        raise RuntimeError(f"ffmpeg failed: {out.stderr.decode('utf-8', 'replace')[:200]}")
    return out.stdout


def synthesize(upstream, text, voice=None):
    """Ask the engine for PCM. Returns bytes (possibly empty)."""
    url = upstream
    if voice:
        url += ("&" if "?" in url else "?") + urllib.parse.urlencode({"voice": voice})
    req = urllib.request.Request(url, data=text.encode("utf-8"), method="POST",
                                 headers={"Content-Type": "text/plain; charset=utf-8"})
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as r:  # fixed local URL
            return r.read()
    except (urllib.error.URLError, OSError) as e:
        raise UpstreamError(str(e)) from e


def make_handler(key, upstream, max_chars=4096, ffmpeg="ffmpeg"):
    expected = f"Bearer {key}".encode()

    class Handler(BaseHTTPRequestHandler):
        server_version = "kokoro-openai/1"
        sys_version = ""

        def log_message(self, fmt, *args):
            sys.stderr.write(f"{self.address_string()} {fmt % args}\n")

        def send_json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def error(self, code, message, kind="invalid_request_error"):
            self.send_json(code, {"error": {"message": message, "type": kind}})

        def authorized(self):
            got = (self.headers.get("Authorization") or "").encode()
            if hmac.compare_digest(got, expected):
                return True
            self.error(401, "missing or invalid API key", "authentication_error")
            return False

        def do_GET(self):
            path = urllib.parse.urlparse(self.path).path
            if path == "/health":
                body = b"ok"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if not self.authorized():
                return
            if path == "/v1/models":
                self.send_json(200, {"object": "list", "data": [
                    {"id": "kokoro", "object": "model", "owned_by": "local"}]})
                return
            self.error(404, "not found")

        def do_POST(self):
            path = urllib.parse.urlparse(self.path).path
            if not self.authorized():
                return
            if path != "/v1/audio/speech":
                self.error(404, "not found")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length <= 0 or length > MAX_BODY:
                self.error(400, "missing or oversized body")
                return
            try:
                req = json.loads(self.rfile.read(length))
            except ValueError:
                self.error(400, "body is not JSON")
                return
            if not isinstance(req, dict):
                self.error(400, "body must be a JSON object")
                return
            text = req.get("input")
            if not isinstance(text, str) or not text.strip():
                self.error(400, "'input' must be a non-empty string")
                return
            if len(text) > max_chars:
                self.error(400, f"'input' is longer than {max_chars} characters")
                return
            fmt = req.get("response_format") or "mp3"
            if fmt not in FORMATS:
                self.error(400, f"unsupported response_format {fmt!r}; use one of {sorted(FORMATS)}")
                return
            voice = req.get("voice")
            voice = voice if isinstance(voice, str) and KOKORO_VOICE.match(voice) else None
            try:
                pcm = synthesize(upstream, text, voice)
                if not pcm and voice:
                    pcm = synthesize(upstream, text)  # unknown voice: fall back to the default
            except UpstreamError as e:
                self.error(502, f"engine unreachable: {e}", "server_error")
                return
            if not pcm:
                self.error(502, "engine returned no audio", "server_error")
                return
            try:
                audio = encode(pcm, fmt, ffmpeg)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
                self.error(500, f"encoding failed: {e}", "server_error")
                return
            self.send_response(200)
            self.send_header("Content-Type", FORMATS[fmt][0])
            self.send_header("Content-Length", str(len(audio)))
            self.end_headers()
            self.wfile.write(audio)

    return Handler


def read_key(path):
    with open(os.path.expanduser(path)) as f:
        key = f.read().strip()
    if len(key) < 16:
        raise SystemExit(f"key in {path} is shorter than 16 characters; refusing to start")
    return key


def main():
    key_file = os.environ.get("KOKORO_OPENAI_KEY_FILE")
    if not key_file:
        raise SystemExit("KOKORO_OPENAI_KEY_FILE is not set; refusing to start without a key")
    key = read_key(key_file)
    host = os.environ.get("KOKORO_OPENAI_HOST", "127.0.0.1")
    port = int(os.environ.get("KOKORO_OPENAI_PORT", "8921"))
    upstream = os.environ.get("KOKORO_URL") or f"http://127.0.0.1:{os.environ.get('KOKORO_PORT', '8910')}/"
    max_chars = int(os.environ.get("KOKORO_OPENAI_MAX_CHARS", "4096"))
    ffmpeg = os.environ.get("FFMPEG", "ffmpeg")
    handler = make_handler(key, upstream, max_chars, ffmpeg)
    print(f"kokoro-openai ready on {host}:{port} -> {upstream}", flush=True)
    ThreadingHTTPServer((host, port), handler).serve_forever()


if __name__ == "__main__":
    main()
