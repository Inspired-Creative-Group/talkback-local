"""talkback.server with both backends faked in ``sys.modules``.

``torch`` / ``kokoro`` (the macOS default) and ``kokoro_onnx`` (the
``TALKBACK_ENGINE=onnx`` route) are stand-ins planted with monkeypatch; the
"audio" is a couple of small float32 arrays and nothing real is loaded or
played. ``boot()`` takes ``env`` and ``home`` explicitly, so most of this runs
on ``tmp_path`` alone and is marked ``pure``; the HTTP tests serve the booted
handler on a loopback port picked by the OS (port 0 — never 8910).

The Stage 1 contract test (``test_server_contract.py``) still drives
``engine/server.py`` unchanged; this file is where the onnx backend, the
backend selection and the start-up surface of ``talkback.server`` are pinned.
"""

import http.client
import io
import socket
import sys
import threading
import types
import warnings
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import numpy as np
import pytest

from talkback import paths
from talkback import server as tserver

# The same floats for both backends; the second chunk carries out-of-range
# samples so clipping is exercised.
CHUNKS = [
    np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32),
    np.array([1.5, -1.5, 0.25, 0.0], dtype=np.float32),
]
HEART = np.array([1.0, 2.0, 3.0, 4.0], dtype=np.float32)
SARAH = np.array([10.0, 20.0, 30.0, 40.0], dtype=np.float32)


def s16le(arrays):
    return b"".join((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes() for a in arrays)


# --------------------------------------------------------------------------
# fakes
# --------------------------------------------------------------------------
class TorchFakes:
    """``torch`` and ``kokoro`` stand-ins recording what the backend does."""

    def __init__(self):
        self.sentinel = object()
        self.loads = []
        self.constructions = []
        self.calls = []  # (text, voice, speed)
        self.audio = CHUNKS
        self.fail_devices = set()
        fakes = self

        torch = types.ModuleType("torch")

        def load(path, weights_only=False):
            fakes.loads.append((path, weights_only))
            return fakes.sentinel

        torch.load = load

        class KPipeline:
            def __init__(self, **kwargs):
                fakes.constructions.append(dict(kwargs))
                if kwargs.get("device") in fakes.fail_devices:
                    fakes.fail_devices.discard(kwargs["device"])
                    raise RuntimeError(f"no backend for {kwargs['device']}")

            def __call__(self, text, voice=None, speed=None):
                fakes.calls.append((text, voice, speed))
                return ((f"g{i}", f"p{i}", a) for i, a in enumerate(fakes.audio))

        kokoro = types.ModuleType("kokoro")
        kokoro.KPipeline = KPipeline
        self.modules = {"torch": torch, "kokoro": kokoro}

    def plant(self, monkeypatch):
        for name, mod in self.modules.items():
            monkeypatch.setitem(sys.modules, name, mod)


class OnnxFakes:
    """``kokoro_onnx.Kokoro`` stand-in: records construction, hands out two
    distinct voice styles, records every ``create`` and returns the chunks
    concatenated at 24 kHz."""

    def __init__(self):
        self.constructions = []  # (model_path, voices_path)
        self.styles = []
        self.calls = []  # (text, voice, speed, lang)
        self.audio = CHUNKS
        self.rate = 24000
        fakes = self

        class Kokoro:
            def __init__(self, model_path, voices_path):
                fakes.constructions.append((model_path, voices_path))

            def get_voice_style(self, name):
                fakes.styles.append(name)
                return {"af_heart": HEART, "af_sarah": SARAH}[name]

            def create(self, text, voice=None, speed=None, lang=None):
                fakes.calls.append((text, voice, speed, lang))
                return np.concatenate(list(fakes.audio)), fakes.rate

        mod = types.ModuleType("kokoro_onnx")
        mod.Kokoro = Kokoro
        self.modules = {"kokoro_onnx": mod}

    def plant(self, monkeypatch):
        for name, mod in self.modules.items():
            monkeypatch.setitem(sys.modules, name, mod)


def _home(tmp_path):
    return tmp_path / "home"


def _onnx_files(home):
    d = paths.engine_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    model, voices = d / "kokoro-v1.0.onnx", d / "voices-v1.0.bin"
    model.write_bytes(b"onnx")
    voices.write_bytes(b"voices")
    return model, voices


def _env(backend, port="8931", **more):
    env = {"KOKORO_PORT": port}
    if backend == "onnx":
        env["TALKBACK_ENGINE"] = "onnx"
    env.update(more)
    return env


def _boot(backend, fakes, home, voice_path=None, **env):
    with warnings.catch_warnings():  # synth_torch mutes warnings globally; keep it inside
        return tserver.boot(voice_path=voice_path, env=_env(backend, **env), home=home)


@pytest.fixture(params=["torch", "onnx"])
def backend(request):
    return request.param


@pytest.fixture
def fakes(backend, monkeypatch, tmp_path):
    """The right fakes for ``backend``, planted; onnx model files in place."""
    f = TorchFakes() if backend == "torch" else OnnxFakes()
    f.plant(monkeypatch)
    if backend == "onnx":
        _onnx_files(_home(tmp_path))
    else:
        d = paths.engine_dir(_home(tmp_path))
        d.mkdir(parents=True, exist_ok=True)
        (d / "icg_voice.pt").write_bytes(b"pt")
    return f


def _synth_calls(backend, fakes):
    """(text, speed) of every synthesis, whichever backend recorded it."""
    if backend == "torch":
        return [(t, s) for t, _, s in fakes.calls]
    return [(t, s) for t, _, s, _ in fakes.calls]


# --------------------------------------------------------------------------
# serving a booted handler over HTTP (not pure: a loopback socket)
# --------------------------------------------------------------------------
class Served:
    def __init__(self, srv):
        self.srv = srv
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), srv.handler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        assert self.port != 8910
        self.thread = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()

    def request(self, method, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, "/", body=body)
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type"), resp.read()
        finally:
            conn.close()

    def post(self, text):
        return self.request("POST", text.encode("utf-8"))

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def served(backend, fakes, tmp_path):
    started = []

    def factory(**env):
        s = Served(_boot(backend, fakes, _home(tmp_path), **env))
        started.append(s)
        return s

    yield factory
    for s in started:
        s.close()


# --------------------------------------------------------------------------
# the HTTP contract, both backends
# --------------------------------------------------------------------------
def test_post_text_returns_l16_pcm_of_every_chunk(served, backend, fakes):
    eng = served()
    status, ctype, body = eng.post("Hello there.")
    assert (status, ctype) == (200, "audio/L16")
    assert body == s16le(CHUNKS)
    samples = np.frombuffer(body, dtype="<i2").tolist()
    assert samples[:5] == [0, 16383, -16383, 32767, -32767]
    assert samples[5:7] == [32767, -32767]
    assert _synth_calls(backend, fakes)[-1][0] == "Hello there."


def test_both_backends_produce_byte_identical_pcm_for_the_same_floats(monkeypatch, tmp_path):
    home = _home(tmp_path)
    _onnx_files(home)
    t, o = TorchFakes(), OnnxFakes()
    t.plant(monkeypatch)
    o.plant(monkeypatch)
    bodies = []
    for backend in ("torch", "onnx"):
        eng = Served(_boot(backend, None, home, voice_path=str(tmp_path / "v.pt")))
        try:
            bodies.append(eng.post("Same floats, same bytes.")[2])
        finally:
            eng.close()
    assert bodies[0] == bodies[1] == s16le(CHUNKS)


@pytest.mark.parametrize("body", ["", "   \n\t"], ids=["empty", "whitespace"])
def test_blank_body_is_rejected_with_400_before_any_synthesis(served, backend, fakes, body):
    eng = served()
    before = len(_synth_calls(backend, fakes))
    status, ctype, out = eng.post(body)
    assert status == 400
    assert ctype != "audio/L16"
    assert out == b""
    assert len(_synth_calls(backend, fakes)) == before


def test_get_root_answers_ok(served):
    assert served().request("GET") == (200, None, b"ok")


@pytest.mark.parametrize("speed, expected", [(None, 1.15), ("1.3", 1.3)], ids=["default", "1.3"])
def test_kokoro_speed_env_is_passed_to_every_synthesis(served, backend, fakes, speed, expected):
    eng = served(**({} if speed is None else {"KOKORO_SPEED": speed}))
    eng.post("Hello there.")
    assert [s for _, s in _synth_calls(backend, fakes)] == [expected, expected]


def test_requests_are_logged_under_home_and_nowhere_else(served, tmp_path):
    home = _home(tmp_path)
    paths.engine_dir(home).mkdir(parents=True, exist_ok=True)
    eng = served()
    reqlog = Path(eng.srv.reqlog)
    assert reqlog == home / ".claude" / "automation" / "kokoro" / "requests.log"
    texts = ["Hello there.", "A second reply, a little longer than the first."]
    for text in texts:
        eng.post(text)
    lines = reqlog.read_text().splitlines()
    assert len(lines) == len(texts)
    for line, text in zip(lines, texts):
        assert "from=127.0.0.1" in line
        assert f"chars={len(text)}" in line
        assert repr(text[:60]) in line
    assert list(tmp_path.rglob("requests.log")) == [reqlog]


def test_missing_log_dir_does_not_break_synthesis(backend, monkeypatch, tmp_path):
    home = _home(tmp_path)
    fakes = TorchFakes() if backend == "torch" else OnnxFakes()
    fakes.plant(monkeypatch)
    model, voices = _onnx_files(tmp_path / "elsewhere")  # onnx files outside HOME
    env = {"TALKBACK_ONNX_MODEL": str(model), "TALKBACK_ONNX_VOICES": str(voices)}
    eng = Served(_boot(backend, fakes, home, voice_path=str(tmp_path / "v.pt"), **env))
    try:
        assert not paths.engine_dir(home).exists()
        assert eng.post("Hello there.") == (200, "audio/L16", s16le(CHUNKS))
        assert not Path(eng.srv.reqlog).exists()
    finally:
        eng.close()


def test_client_hanging_up_mid_reply_is_tolerated(backend, fakes, tmp_path):
    """shush closes the connection while audio is being written: the handler
    swallows the broken pipe instead of dying with a traceback. (A socketpair,
    so not ``pure``.)"""
    srv = _boot(backend, fakes, _home(tmp_path))
    ours, theirs = socket.socketpair()

    def audio_then_hangup():
        yield CHUNKS[0]
        theirs.close()  # shush: the client is gone while audio is still coming
        yield CHUNKS[1]

    fakes.audio = audio_then_hangup()
    try:
        theirs.sendall(b"POST / HTTP/1.0\r\nContent-Length: 5\r\n\r\nHello")
        srv.handler(ours, ("127.0.0.1", 1), types.SimpleNamespace())  # returns; does not raise
    finally:
        ours.close()
        theirs.close()
    assert _synth_calls(backend, fakes)[-1][0] == "Hello"  # the model was reached; only the write failed


# --------------------------------------------------------------------------
# boot(): warm-up, selection, isolation, the PCM conversion
# --------------------------------------------------------------------------
@pytest.mark.pure
def test_warm_up_runs_once_before_any_request_and_never_again(backend, fakes, tmp_path):
    srv = _boot(backend, fakes, _home(tmp_path))
    assert _synth_calls(backend, fakes) == [("Ready.", 1.15)]
    list(srv.pipe("First.", srv.speed))
    list(srv.pipe("Second.", srv.speed))
    assert [t for t, _ in _synth_calls(backend, fakes)] == ["Ready.", "First.", "Second."]
    assert len(fakes.constructions) == 1


@pytest.mark.pure
def test_pcm_bytes_are_identical_for_a_tensor_and_an_ndarray():
    class Tensor:  # the three calls the server makes on a torch tensor
        def __init__(self, a):
            self.a = a

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.a

    for a in CHUNKS:
        assert tserver.pcm_bytes(Tensor(a)) == tserver.pcm_bytes(a) == s16le([a])
    assert tserver.pcm_bytes([0.0, 1.0, -1.0, 2.0]) == s16le([np.array([0.0, 1.0, -1.0, 2.0])])


@pytest.mark.pure
@pytest.mark.parametrize(
    "value, backend, device",
    [(None, "torch", "cpu"), ("", "torch", "cpu"), ("torch", "torch", "cpu"), ("onnx", "onnx", "onnx")],
    ids=["unset", "empty", "torch", "onnx"],
)
def test_backend_is_selected_by_talkback_engine(monkeypatch, tmp_path, value, backend, device):
    home = _home(tmp_path)
    _onnx_files(home)
    TorchFakes().plant(monkeypatch)
    OnnxFakes().plant(monkeypatch)
    env = {"KOKORO_PORT": "8931"}
    if value is not None:
        env["TALKBACK_ENGINE"] = value
    with warnings.catch_warnings():
        srv = tserver.boot(voice_path=str(tmp_path / "v.pt"), env=env, home=home)
    assert (srv.backend, srv.device, srv.port) == (backend, device, 8931)


@pytest.mark.pure
def test_unknown_backend_is_a_clear_error(tmp_path):
    with pytest.raises(SystemExit) as e:
        tserver.boot(env={"TALKBACK_ENGINE": "banana"}, home=_home(tmp_path))
    assert "TALKBACK_ENGINE='banana'" in str(e.value)
    assert "torch" in str(e.value) and "onnx" in str(e.value)


@pytest.mark.pure
def test_two_boots_in_one_process_share_nothing(monkeypatch, tmp_path):
    a, b = TorchFakes(), TorchFakes()
    a.plant(monkeypatch)
    s1 = _boot("torch", a, _home(tmp_path) / "one", voice_path="one.pt")
    b.plant(monkeypatch)
    s2 = _boot("torch", b, _home(tmp_path) / "two", voice_path="two.pt", KOKORO_SPEED="0.9")
    assert s1.handler is not s2.handler
    assert s1.lock is not s2.lock
    assert s1.voice is a.sentinel and s2.voice is b.sentinel
    assert (s1.speed, s2.speed) == (1.15, 0.9)
    assert Path(s1.reqlog).parents[3] != Path(s2.reqlog).parents[3]
    list(s2.pipe("Only two.", s2.speed))
    assert [t for t, _, _ in a.calls] == ["Ready."]
    assert [t for t, _, _ in b.calls] == ["Ready.", "Only two."]


# --------------------------------------------------------------------------
# the torch backend
# --------------------------------------------------------------------------
@pytest.mark.pure
def test_torch_voice_path_precedence(monkeypatch, tmp_path):
    home = _home(tmp_path)
    f = TorchFakes()
    f.plant(monkeypatch)
    _boot("torch", f, home)
    _boot("torch", f, home, TALKBACK_VOICE=str(tmp_path / "custom.pt"))
    _boot("torch", f, home, voice_path=str(tmp_path / "beside.pt"), TALKBACK_VOICE=str(tmp_path / "custom.pt"))
    assert [Path(p) for p, _ in f.loads] == [
        home / ".claude" / "automation" / "kokoro" / "icg_voice.pt",
        tmp_path / "custom.pt",
        tmp_path / "beside.pt",
    ]
    assert all(w is True for _, w in f.loads)


@pytest.mark.pure
@pytest.mark.parametrize("device, expected", [(None, "cpu"), ("", "cpu"), ("mps", "mps")], ids=["unset", "empty", "mps"])
def test_torch_kokoro_device_env_picks_the_pipeline_device(monkeypatch, tmp_path, device, expected):
    f = TorchFakes()
    f.plant(monkeypatch)
    env = {} if device is None else {"KOKORO_DEVICE": device}
    srv = _boot("torch", f, _home(tmp_path), voice_path="v.pt", **env)
    assert f.constructions == [{"lang_code": "a", "repo_id": "hexgrad/Kokoro-82M", "device": expected}]
    assert srv.device == expected


@pytest.mark.pure
def test_torch_unusable_device_falls_back_to_cpu(monkeypatch, tmp_path, capsys):
    f = TorchFakes()
    f.fail_devices = {"mps"}
    f.plant(monkeypatch)
    srv = _boot("torch", f, _home(tmp_path), voice_path="v.pt", KOKORO_DEVICE="mps")
    assert [c["device"] for c in f.constructions] == ["mps", "cpu"]
    assert srv.device == "cpu"
    assert "device mps failed (no backend for mps); falling back to cpu" in capsys.readouterr().out
    assert f.calls[0] == ("Ready.", f.sentinel, 1.15)


# --------------------------------------------------------------------------
# the onnx backend
# --------------------------------------------------------------------------
@pytest.mark.pure
def test_onnx_voice_is_the_af_heart_af_sarah_blend(monkeypatch, tmp_path):
    home = _home(tmp_path)
    _onnx_files(home)
    f = OnnxFakes()
    f.plant(monkeypatch)
    srv = _boot("onnx", f, home)
    assert sorted(f.styles) == ["af_heart", "af_sarah"]
    assert srv.voice.dtype == np.float32
    assert np.allclose(srv.voice, 0.65 * HEART + 0.35 * SARAH)
    # the blend, not a stock voice, is what every synthesis is given
    list(srv.pipe("Hello there.", srv.speed))
    assert all(v is srv.voice for _, v, _, _ in f.calls)


@pytest.mark.pure
def test_onnx_paths_come_from_the_environment(monkeypatch, tmp_path):
    home = _home(tmp_path)
    default_model, default_voices = _onnx_files(home)
    custom_model, custom_voices = _onnx_files(tmp_path / "custom")
    f = OnnxFakes()
    f.plant(monkeypatch)
    _boot("onnx", f, home)
    _boot("onnx", f, home, TALKBACK_ONNX_MODEL=str(custom_model), TALKBACK_ONNX_VOICES=str(custom_voices))
    assert [tuple(map(Path, c)) for c in f.constructions] == [
        (default_model, default_voices),
        (custom_model, custom_voices),
    ]
    assert default_model == home / ".claude" / "automation" / "kokoro" / "kokoro-v1.0.onnx"
    assert default_voices == home / ".claude" / "automation" / "kokoro" / "voices-v1.0.bin"


@pytest.mark.pure
def test_onnx_ignores_kokoro_device(monkeypatch, tmp_path, capsys):
    home = _home(tmp_path)
    _onnx_files(home)
    f = OnnxFakes()
    f.plant(monkeypatch)
    srv = _boot("onnx", f, home, KOKORO_DEVICE="mps")
    assert srv.device == "onnx"
    assert srv.backend == "onnx"
    assert capsys.readouterr().out == ""


@pytest.mark.pure
def test_onnx_warm_up_runs_once(monkeypatch, tmp_path):
    home = _home(tmp_path)
    _onnx_files(home)
    f = OnnxFakes()
    f.plant(monkeypatch)
    _boot("onnx", f, home)
    assert [(t, s, lang) for t, _, s, lang in f.calls] == [("Ready.", 1.15, "en-us")]
    assert len(f.constructions) == 1


@pytest.mark.pure
def test_onnx_create_gets_speed_and_lang_en_us(monkeypatch, tmp_path):
    home = _home(tmp_path)
    _onnx_files(home)
    f = OnnxFakes()
    f.plant(monkeypatch)
    srv = _boot("onnx", f, home, KOKORO_SPEED="1.3")
    out = list(srv.pipe("Hello there.", srv.speed))
    assert f.calls[-1][0] == "Hello there."
    assert f.calls[-1][2] == 1.3
    assert f.calls[-1][3] == "en-us"
    assert len(out) == 1  # kokoro-onnx returns one array per call
    assert tserver.pcm_bytes(out[0]) == s16le(CHUNKS)


@pytest.mark.pure
def test_onnx_audio_at_the_wrong_rate_is_refused(monkeypatch, tmp_path):
    home = _home(tmp_path)
    _onnx_files(home)
    f = OnnxFakes()
    f.plant(monkeypatch)
    srv = _boot("onnx", f, home)
    f.rate = 22050
    with pytest.raises(RuntimeError, match="22050"):
        list(srv.pipe("Hello there.", srv.speed))


@pytest.mark.pure
def test_onnx_missing_library_is_a_clear_error(monkeypatch, tmp_path):
    home = _home(tmp_path)
    _onnx_files(home)
    monkeypatch.setitem(sys.modules, "kokoro_onnx", None)  # `import kokoro_onnx` -> ImportError
    with pytest.raises(SystemExit) as e:
        tserver.boot(env={"TALKBACK_ENGINE": "onnx"}, home=home)
    msg = str(e.value)
    assert "kokoro-onnx" in msg and "talkback-local[onnx]" in msg


@pytest.mark.pure
def test_onnx_missing_model_file_is_a_clear_error(monkeypatch, tmp_path):
    home = _home(tmp_path)
    f = OnnxFakes()
    f.plant(monkeypatch)
    with pytest.raises(SystemExit) as e:
        tserver.boot(env={"TALKBACK_ENGINE": "onnx"}, home=home)
    msg = str(e.value)
    assert str(home / ".claude" / "automation" / "kokoro" / "kokoro-v1.0.onnx") in msg
    assert "kokoro-v1.0.onnx" in msg and "TALKBACK_ONNX_MODEL" in msg
    assert f.constructions == []  # never handed to onnxruntime


# --------------------------------------------------------------------------
# serve(): the start-up line, the pid file, the console-less redirect
# --------------------------------------------------------------------------
class FakeHTTPD:
    """Stands in for ThreadingHTTPServer: records the bind, and its
    serve_forever returns at once after noting what the pid file held."""

    instances: ClassVar[list] = []

    def __init__(self, address, handler):
        self.address, self.handler = address, handler
        self.pid_seen = None
        self.closed = False
        FakeHTTPD.instances.append(self)

    def serve_forever(self):
        self.pid_seen = paths.server_pid(self.home).read_text()

    def server_close(self):
        self.closed = True


@pytest.fixture
def fake_httpd(monkeypatch, tmp_path):
    FakeHTTPD.instances = []
    FakeHTTPD.home = _home(tmp_path)
    monkeypatch.setattr(tserver, "ThreadingHTTPServer", FakeHTTPD)
    return FakeHTTPD


@pytest.mark.pure
def test_startup_line_names_the_backend(backend, fakes, tmp_path, fake_httpd, capsys):
    home = _home(tmp_path)
    srv = _boot(backend, fakes, home, port="8932")
    tserver.serve(srv, home=home)
    device = "cpu" if backend == "torch" else "onnx"
    assert capsys.readouterr().out == f"kokoro server ready on 127.0.0.1:8932 (device={device})\n"
    (httpd,) = fake_httpd.instances
    assert httpd.address == ("127.0.0.1", 8932)
    assert httpd.handler is srv.handler
    assert httpd.closed


@pytest.mark.pure
def test_serve_holds_the_pid_file_while_serving_and_clears_it_after(monkeypatch, tmp_path, fake_httpd):
    home = _home(tmp_path)
    f = TorchFakes()
    f.plant(monkeypatch)
    srv = _boot("torch", f, home, voice_path="v.pt")
    tserver.serve(srv, home=home)
    (httpd,) = fake_httpd.instances
    import os

    assert httpd.pid_seen == f"{os.getpid()}\n"
    assert not paths.server_pid(home).exists()


@pytest.mark.pure
def test_serve_without_a_console_writes_to_server_log(monkeypatch, tmp_path, fake_httpd):
    """pythonw on Windows: sys.stdout is None, so the ready line (and any
    traceback) must land in kokoro/server.log."""
    home = _home(tmp_path)
    f = TorchFakes()
    f.plant(monkeypatch)
    srv = _boot("torch", f, home, voice_path="v.pt", port="8933")
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    tserver.serve(srv, home=home)
    assert isinstance(sys.stdout, io.TextIOBase)
    sys.stdout.close()
    assert paths.server_log(home).read_text() == "kokoro server ready on 127.0.0.1:8933 (device=cpu)\n"
