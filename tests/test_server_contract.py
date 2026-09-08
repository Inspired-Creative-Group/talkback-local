"""Contract tests for engine/server.py with the model mocked.

The server module is imported per test with fake ``torch`` and ``kokoro``
modules planted in ``sys.modules``, HOME pointed at the sandbox (the request
log path is built from HOME at import time) and KOKORO_PORT set to a free port
in 8930-8949 — never 8910, where the owner's live engine runs. Importing runs
the module's top level exactly as launchd would: load the voice, build the
pipeline for the requested device, warm up with "Ready.". The handler class is
then served by a ThreadingHTTPServer in a daemon thread and driven with
http.client.

Nothing real is touched: the voice tensor is whatever the fake ``torch.load``
returns, the "audio" is a couple of small float32 arrays, and no sound is
produced.
"""

import http.client
import importlib.util
import socket
import sys
import threading
import types
import warnings
from http.server import ThreadingHTTPServer
from pathlib import Path

import numpy as np
import pytest
from conftest import ENGINE_PORTS, FORBIDDEN_PORT

# What the fake pipeline yields for every synthesis. The second chunk carries
# out-of-range samples so clipping is exercised.
CHUNKS = [
    np.array([0.0, 0.5, -0.5, 1.0, -1.0], dtype=np.float32),
    np.array([1.5, -1.5, 0.25, 0.0], dtype=np.float32),
]


def s16le(arrays):
    """The bytes the server promises: s16le of each chunk, concatenated."""
    return b"".join((np.clip(a, -1, 1) * 32767).astype("<i2").tobytes() for a in arrays)


# --------------------------------------------------------------------------
# fakes for torch and kokoro
# --------------------------------------------------------------------------
class Fakes:
    """Stand-ins for ``torch`` and ``kokoro`` that record what the server does with them."""

    def __init__(self):
        self.sentinel = object()  # the "voice tensor" torch.load hands back
        self.loads = []           # (path, weights_only) per torch.load
        self.constructions = []   # kwargs per KPipeline(...)
        self.calls = []           # (text, voice, speed) per PIPE(...)
        self.audio = CHUNKS
        self.fail_devices = set()  # devices whose construction raises (once each)
        fakes = self

        torch = types.ModuleType("torch")

        def load(path, weights_only=False):
            fakes.loads.append((path, weights_only))
            return fakes.sentinel

        torch.load = load

        class KPipeline:
            def __init__(self, **kwargs):
                fakes.constructions.append(dict(kwargs))
                device = kwargs.get("device")
                if device in fakes.fail_devices:
                    fakes.fail_devices.discard(device)
                    raise RuntimeError(f"no backend for {device}")

            def __call__(self, text, voice=None, speed=None):
                fakes.calls.append((text, voice, speed))
                return ((f"g{i}", f"p{i}", a) for i, a in enumerate(fakes.audio))

        kokoro = types.ModuleType("kokoro")
        kokoro.KPipeline = KPipeline

        self.torch = torch
        self.kokoro = kokoro


# --------------------------------------------------------------------------
# booting the module and serving its handler
# --------------------------------------------------------------------------
def _probe_free_port():
    for port in ENGINE_PORTS:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            # bind the way HTTPServer does (SO_REUSEADDR), so a port whose
            # previous test connections are still in TIME_WAIT reads as free
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
        return port
    pytest.fail(f"no free port in {ENGINE_PORTS.start}-{ENGINE_PORTS.stop - 1}")


def _serve(handler, first):
    # Prefer the port the module was told about; if a parallel run grabbed it
    # between probe and bind, any other free port in the range will do — the
    # module never binds anything itself outside __main__.
    for port in (first, *(p for p in ENGINE_PORTS if p != first)):
        try:
            server = ThreadingHTTPServer(("127.0.0.1", port), handler)
        except OSError:
            continue
        assert server.server_address[1] != FORBIDDEN_PORT
        return server
    pytest.fail(f"no free port in {ENGINE_PORTS.start}-{ENGINE_PORTS.stop - 1}")


class Booted:
    def __init__(self, module, fakes, server, thread):
        self.module = module
        self.fakes = fakes
        self.server = server
        self.thread = thread
        self.port = server.server_address[1]

    def _request(self, method, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, "/", body=body)
            resp = conn.getresponse()
            return resp.status, resp.getheader("Content-Type"), resp.read()
        finally:
            conn.close()

    def get(self):
        return self._request("GET")

    def post(self, text):
        return self._request("POST", text.encode("utf-8"))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def boot(repo, sandbox, monkeypatch):
    """Factory: import engine/server.py fresh under the given env and serve it.

    ``boot(fakes=None, **env)`` -> Booted. HOME is the sandbox home, KOKORO_PORT
    a free port; KOKORO_DEVICE / KOKORO_SPEED are cleared unless passed.
    """
    started = []

    def _boot(fakes=None, **env):
        fakes = fakes or Fakes()
        port = _probe_free_port()
        monkeypatch.setenv("HOME", str(sandbox.home))
        monkeypatch.setenv("KOKORO_PORT", str(port))
        for key in ("KOKORO_DEVICE", "KOKORO_SPEED"):
            monkeypatch.delenv(key, raising=False)
        for key, value in env.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setitem(sys.modules, "torch", fakes.torch)
        monkeypatch.setitem(sys.modules, "kokoro", fakes.kokoro)

        spec = importlib.util.spec_from_file_location("talkback_server_under_test", repo / "engine" / "server.py")
        module = importlib.util.module_from_spec(spec)
        with warnings.catch_warnings():  # the module mutes warnings globally; keep that inside the import
            spec.loader.exec_module(module)
        assert module.PORT == port
        assert module.PORT != FORBIDDEN_PORT

        server = _serve(module.H, first=port)
        server.daemon_threads = True
        # short poll so shutdown() at teardown does not wait out the default 0.5 s
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        thread.start()
        booted = Booted(module, fakes, server, thread)
        started.append(booted)
        return booted

    yield _boot
    for booted in started:
        booted.close()


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------
def test_post_text_returns_l16_pcm_of_every_chunk(boot):
    eng = boot()
    status, ctype, body = eng.post("Hello there.")
    assert status == 200
    assert ctype == "audio/L16"
    assert body == s16le(CHUNKS)
    assert len(body) == 2 * sum(len(a) for a in CHUNKS)
    samples = np.frombuffer(body, dtype="<i2").tolist()
    assert samples[:5] == [0, 16383, -16383, 32767, -32767]
    assert samples[5:7] == [32767, -32767]  # 1.5 / -1.5 clipped to full scale
    assert eng.fakes.calls[-1][0] == "Hello there."  # text reaches the model verbatim


@pytest.mark.parametrize("body", ["", "   \n\t"], ids=["empty", "whitespace"])
def test_blank_body_is_rejected_with_400_and_never_reaches_the_model(boot, body):
    eng = boot()
    before = list(eng.fakes.calls)
    status, ctype, out = eng.post(body)
    assert status == 400
    assert ctype != "audio/L16"
    assert out == b""
    assert eng.fakes.calls == before


def test_warm_up_runs_once_before_any_request_and_never_again(boot):
    eng = boot()
    assert eng.fakes.calls == [("Ready.", eng.fakes.sentinel, 1.15)]
    eng.post("First.")
    eng.post("Second.")
    assert [text for text, _, _ in eng.fakes.calls] == ["Ready.", "First.", "Second."]
    # the model is loaded once for the life of the process, not per request
    assert len(eng.fakes.constructions) == 1
    assert len(eng.fakes.loads) == 1


def test_voice_is_the_tensor_loaded_from_icg_voice_pt_beside_the_server(boot, repo):
    eng = boot()
    assert len(eng.fakes.loads) == 1
    path, weights_only = eng.fakes.loads[0]
    assert Path(path).resolve() == (repo / "engine" / "icg_voice.pt").resolve()
    assert weights_only is True
    eng.post("Hello there.")
    assert [voice is eng.fakes.sentinel for _, voice, _ in eng.fakes.calls] == [True, True]


@pytest.mark.parametrize(
    "device, expected",
    [(None, "cpu"), ("", "cpu"), ("mps", "mps")],
    ids=["unset", "empty", "mps"],
)
def test_kokoro_device_env_picks_the_pipeline_device(boot, device, expected):
    env = {} if device is None else {"KOKORO_DEVICE": device}
    eng = boot(**env)
    assert eng.fakes.constructions == [
        {"lang_code": "a", "repo_id": "hexgrad/Kokoro-82M", "device": expected},
    ]


def test_unusable_device_falls_back_to_cpu_and_still_serves(boot, capsys):
    fakes = Fakes()
    fakes.fail_devices = {"mps"}
    eng = boot(fakes=fakes, KOKORO_DEVICE="mps")
    assert [c["device"] for c in fakes.constructions] == ["mps", "cpu"]
    assert "falling back to cpu" in capsys.readouterr().out
    assert fakes.calls[0][0] == "Ready."
    status, ctype, body = eng.post("Hello there.")
    assert (status, ctype, body) == (200, "audio/L16", s16le(CHUNKS))


@pytest.mark.parametrize("speed, expected", [(None, 1.15), ("1.3", 1.3)], ids=["default", "1.3"])
def test_kokoro_speed_env_is_passed_to_every_synthesis(boot, speed, expected):
    env = {} if speed is None else {"KOKORO_SPEED": speed}
    eng = boot(**env)
    eng.post("Hello there.")
    assert [s for _, _, s in eng.fakes.calls] == [expected, expected]


def test_get_root_answers_ok(boot):
    eng = boot()
    status, _, body = eng.get()
    assert status == 200
    assert body == b"ok"


def test_requests_are_logged_under_home_and_nowhere_else(boot, sandbox, tmp_path, repo):
    logdir = sandbox.home / ".claude" / "automation" / "kokoro"
    logdir.mkdir(parents=True)
    eng = boot()
    reqlog = Path(eng.module.REQLOG)
    assert reqlog == logdir / "requests.log"

    texts = ["Hello there.", "A second reply, a little longer than the first."]
    for text in texts:
        eng.post(text)

    lines = reqlog.read_text().splitlines()
    assert len(lines) == len(texts)
    for line, text in zip(lines, texts):
        assert "from=127.0.0.1" in line
        assert f"chars={len(text)}" in line
        assert repr(text[:60]) in line
    # the only requests.log anywhere in the sandbox is the one under HOME
    assert list(tmp_path.rglob("requests.log")) == [reqlog]
    assert not (repo / "engine" / "requests.log").exists()


def test_missing_log_dir_does_not_break_synthesis(boot, sandbox):
    eng = boot()
    assert not (sandbox.home / ".claude" / "automation" / "kokoro").exists()
    status, ctype, body = eng.post("Hello there.")
    assert (status, ctype, body) == (200, "audio/L16", s16le(CHUNKS))
    assert not Path(eng.module.REQLOG).exists()
