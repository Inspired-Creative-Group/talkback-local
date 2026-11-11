"""Tests for engine/openai_adapter.py, the optional OpenAI-compatible front door.

A fake engine (a stdlib HTTP server on an OS-assigned port) stands in for
engine/server.py: it records each request's path and body and answers with
fixed PCM, or nothing for voices named "xx_bad". The adapter is served from its
own make_handler() on another OS-assigned port. Nothing touches 8910.
"""

import http.client
import io
import json
import shutil
import socket
import threading
import wave
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
KEY = "test-key-0123456789abcdef"
PCM = b"\x00\x00\xff\x7f\x01\x80" * 4000  # 12000 samples = 0.5 s at 24 kHz


def load_adapter():
    import importlib.util

    spec = importlib.util.spec_from_file_location("openai_adapter", ROOT / "engine" / "openai_adapter.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def serve(handler):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


@pytest.fixture
def engine():
    calls = []

    class Fake(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode()
            calls.append((self.path, body))
            self.send_response(200)
            self.end_headers()
            if "voice=xx_bad" not in self.path:
                self.wfile.write(PCM)

    srv = serve(Fake)
    srv.calls = calls
    yield srv
    srv.shutdown()


@pytest.fixture
def adapter(engine):
    mod = load_adapter()
    srv = serve(mod.make_handler(KEY, f"http://127.0.0.1:{engine.server_port}/", max_chars=100))
    yield srv
    srv.shutdown()


def request(srv, method, path, body=None, key=KEY):
    conn = http.client.HTTPConnection("127.0.0.1", srv.server_port, timeout=30)
    headers = {"Content-Type": "application/json"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    r = conn.getresponse()
    return r.status, r.getheader("Content-Type"), r.read()


def speech(srv, key=KEY, **body):
    body.setdefault("input", "Hello there.")
    return request(srv, "POST", "/v1/audio/speech", body, key)


# ---------------------------------------------------------------- auth
def test_no_key_is_401(adapter, engine):
    status, _, body = speech(adapter, key=None)
    assert status == 401
    assert json.loads(body)["error"]["type"] == "authentication_error"
    assert engine.calls == []


def test_wrong_key_is_401(adapter, engine):
    assert speech(adapter, key="nope")[0] == 401
    assert request(adapter, "GET", "/v1/models", key="nope")[0] == 401
    assert engine.calls == []


def test_health_needs_no_key(adapter):
    assert request(adapter, "GET", "/health", key=None)[::2] == (200, b"ok")


def test_models(adapter):
    status, _, body = request(adapter, "GET", "/v1/models")
    assert status == 200
    assert [m["id"] for m in json.loads(body)["data"]] == ["kokoro"]


# ---------------------------------------------------------------- formats
def test_wav_wraps_engine_pcm(adapter, engine):
    status, ctype, body = speech(adapter, response_format="wav")
    assert (status, ctype) == (200, "audio/wav")
    with wave.open(io.BytesIO(body)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate()) == (1, 2, 24000)
        assert w.readframes(w.getnframes()) == PCM
    assert engine.calls == [("/", "Hello there.")]


def test_pcm_is_passthrough(adapter):
    assert speech(adapter, response_format="pcm")[2] == PCM


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
@pytest.mark.parametrize("fmt,magic", [("mp3", (b"ID3", b"\xff\xfb", b"\xff\xf3")), ("opus", (b"OggS",)), ("flac", (b"fLaC",))])
def test_ffmpeg_formats(adapter, fmt, magic):
    status, _, body = speech(adapter, response_format=fmt)
    assert status == 200
    assert body.startswith(magic)


def test_default_format_is_mp3(adapter):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg not installed")
    assert speech(adapter)[1] == "audio/mpeg"


def test_unknown_format_is_400(adapter, engine):
    assert speech(adapter, response_format="aiff")[0] == 400
    assert engine.calls == []


# ---------------------------------------------------------------- input validation
@pytest.mark.parametrize("body", [{"input": ""}, {"input": "   "}, {"input": 5}, {"input": "x" * 101}])
def test_bad_input_is_400(adapter, engine, body):
    assert request(adapter, "POST", "/v1/audio/speech", body)[0] == 400
    assert engine.calls == []


def test_non_json_is_400(adapter):
    conn = http.client.HTTPConnection("127.0.0.1", adapter.server_port, timeout=10)
    conn.request("POST", "/v1/audio/speech", body=b"not json", headers={"Authorization": f"Bearer {KEY}"})
    assert conn.getresponse().status == 400


def test_unknown_path_is_404(adapter):
    assert request(adapter, "POST", "/v1/chat/completions", {"input": "x"})[0] == 404


# ---------------------------------------------------------------- voice mapping
def test_kokoro_voice_is_forwarded(adapter, engine):
    speech(adapter, voice="bf_emma", response_format="pcm")
    assert engine.calls[0][0] == "/?voice=bf_emma"


@pytest.mark.parametrize("voice", ["alloy", "default", "", None, "../etc", "af_heart&x=1"])
def test_other_voices_use_engine_default(adapter, engine, voice):
    speech(adapter, voice=voice, response_format="pcm")
    assert engine.calls[0][0] == "/"


def test_voice_with_no_audio_retries_default(adapter, engine):
    status, _, body = speech(adapter, voice="xx_bad", response_format="pcm")
    assert (status, body) == (200, PCM)
    assert [p for p, _ in engine.calls] == ["/?voice=xx_bad", "/"]


# ---------------------------------------------------------------- engine down
def test_engine_down_is_502():
    mod = load_adapter()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    srv = serve(mod.make_handler(KEY, f"http://127.0.0.1:{dead}/"))
    try:
        status, _, body = speech(srv, response_format="wav")
        assert status == 502
        assert json.loads(body)["error"]["type"] == "server_error"
    finally:
        srv.shutdown()


# ---------------------------------------------------------------- startup
def test_refuses_to_start_without_key(monkeypatch):
    mod = load_adapter()
    monkeypatch.delenv("KOKORO_OPENAI_KEY_FILE", raising=False)
    with pytest.raises(SystemExit):
        mod.main()


def test_refuses_short_key(tmp_path):
    mod = load_adapter()
    f = tmp_path / "key"
    f.write_text("short\n")
    with pytest.raises(SystemExit):
        mod.read_key(str(f))


def test_binds_localhost_by_default():
    src = (ROOT / "engine" / "openai_adapter.py").read_text()
    assert 'os.environ.get("KOKORO_OPENAI_HOST", "127.0.0.1")' in src
