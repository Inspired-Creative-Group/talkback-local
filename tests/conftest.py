"""Shared test harness for Talkback Local.

Everything here keeps the tests away from the owner's live install: every
subprocess runs with HOME pointed at a throwaway sandbox, with a fake
``sounddevice`` (and raising fakes of ``torch`` / ``kokoro`` / ``kokoro_onnx``)
first on PYTHONPATH, fake ``osascript`` / ``python3`` shims ahead of the real
ones on PATH, and with KOKORO_PORT pointing at either a dead port or the
in-process fake engine — never at the real server on 8910. Nothing in this
file calls ``pkill``; teardown only *waits* for the sandbox's own background
players. The real ``sounddevice`` is never imported by a test.

Standard library only. Nothing is created at import time, so this file loads
on Windows for the ``pure`` tests.

FIXTURES
--------
repo : pathlib.Path (session)
    The repository root (the directory that holds ``hooks/``, ``bin/``,
    ``talkback/``, ``install.sh``).

sandbox : Sandbox (function)
    A fresh sandbox HOME per test, built under ``tmp_path``. Attributes:

    .home        sandbox HOME
    .hooks       home/.claude/automation/notifications — every file from the
                 repo's hooks/ copied in, *.sh made executable. This mirrors
                 install.sh, so the scripts find each other via $HOME exactly
                 as in production. Run them as ``sandbox.hooks / "x.sh"``.
    .bin         home/bin — the repo's bin/ copied whole, executable. SAFETY
                 GUARD: a bin file that still contains ``pkill`` (a script not
                 yet turned into a ``-m talkback`` shim) is NOT copied; a
                 logging stand-in takes its place (``shush`` also touches the
                 stop flag). The real pkill-based scripts never run here.
    .recording   home/.claude/automation/recording  (armed-session flags)
    .lastreply   home/.claude/automation/lastreply  (<sid>.txt / <sid>.pcm)
    .stopflag    home/.claude/automation/.tts-stop
    .player_pid  home/.claude/automation/.player.pid      (the player's pid file)
    .server_pid  home/.claude/automation/kokoro/server.pid (the server's pid file)
    .log         .hooks / "speak.log" (may not exist until a hook writes it)
    .fakes       a dir FIRST on every subprocess's PYTHONPATH holding:
                   sounddevice.py  the recording fake (see FAKE SOUNDDEVICE)
                   torch.py, kokoro.py, kokoro_onnx.py
                                   ``raise ImportError("sandbox: the real model
                                   is never loaded")`` — so ``talkback server``
                                   started from a hook dies at import, fast and
                                   deterministically, whatever the host has.
    .shims       a dir at the front of PATH holding:
                   osascript  logs "osascript <args>", exits 0.
                   python3    exec-wrapper around sys.executable, so scripts
                              that call python3 run on pytest's interpreter.
    .calls       Path of the calls log (one line per recorded event:
                 "<time.time()> <name> <shell-quoted args>").
    .read_calls() -> list[(t: float, name: str, args: list[str])]
                 Parsed calls log, in order. Names: "play", "write",
                 "play-done", "osascript", and — only while a bin script is
                 still the pkill version — "shush" / "kokoro-server".
    .calls_to(name) -> list[list[str]]
                 Just the args of every call to ``name``.
    .plays() -> list[list[str]]
                 ``calls_to("play")``: one ``[samplerate, channels, dtype]``
                 (as strings, e.g. ["24000", "2", "int16"]) per stream opened.
    .writes() -> list[int]
                 The byte count of every stream write, in order.
    .env         A clean environment dict for subprocesses:
                   HOME=sandbox home
                   PATH=shims:home/bin:/usr/bin:/bin:/usr/sbin:/sbin
                   PYTHONPATH=<fakes>:<repo>  (fakes first; the checkout second
                     so ``talkback`` resolves even when it is not installed)
                   TALKBACK_PYTHON=sys.executable  (what the .sh shims exec)
                   KOKORO_PORT="8999" — a dead port, nothing listens there —
                     unless the ``fake_engine`` fixture is also requested, in
                     which case it is the engine's port.
                   LANG=LC_ALL=en_US.UTF-8, TMPDIR=<sandbox>/tmp
                 Built from scratch: no TTS_* / ELEVENLABS_* key, and no
                 TALKBACK_* key other than TALKBACK_PYTHON. Copy it and add
                 keys to test tunables: ``env = dict(sandbox.env,
                 TTS_ENGINE="elevenlabs")``.
    .run(cmd, stdin=None, env=None, timeout=30) -> subprocess.CompletedProcess
                 subprocess.run in text mode with capture_output, cwd=repo,
                 env defaulting to .env. Never raises on a non-zero exit.
    .talkback(*args) -> list[str]
                 ``[sys.executable, "-m", "talkback", *args]`` — pass to .run().
    .arm(sid)    touch recording/<sid> (what "TTS on" does).
    .wait_for(predicate, timeout=10, interval=0.05) -> bool
                 Poll until predicate() is truthy; False on timeout.
    .playing() -> bool
                 True while a player from THIS sandbox is running: a
                 ``-m talkback play`` whose command line carries a path under
                 the sandbox HOME (read-only pgrep).
    .wait_quiet(timeout=10) -> bool
                 wait_for(lambda: not self.playing()).

    Teardown waits up to 10 s for the sandbox's own players to finish.
    It never kills anything.

FAKE SOUNDDEVICE (``sandbox.fakes / "sounddevice.py"``)
----------------------------------------------------
Imported by every subprocess as ``import sounddevice`` (PYTHONPATH wins over
site-packages). It never opens an audio device. Mirrors the real API:

    RawOutputStream(samplerate=None, blocksize=None, device=None, channels=None,
                    dtype=None, **more)          # the real positional order —
                                                 # pass samplerate/channels/dtype
                                                 # as KEYWORDS
      .start()  or  ``with stream:``   logs  play [samplerate, channels, dtype]
                                       (once per stream)
      .write(buffer)                   logs  write [nbytes]  (nbytes = len of
                                       the buffer); on the FIRST write, touches
                                       the sandbox stop flag when the env var
                                       FAKE_PLAYER_TOUCH_STOP is set (any
                                       value); then sleeps FAKE_PLAYER_SLEEP
                                       seconds (default 0.15) — the stand-in
                                       for playback time. Raises PortAudioError
                                       if the stream was never started, as the
                                       real one does.
      .stop() / .abort()               no-ops
      .close()  or leaving ``with``    logs  play-done  (once, if started)
      .samplerate .channels .dtype .active .closed
    OutputStream                       same as RawOutputStream (numpy arrays
                                       expose the buffer protocol)
    PortAudioError                     the exception class
    stop(), wait()                     no-ops
    play(...)                          raises — the mono-in-one-ear path this
                                       project deliberately avoids
    sounddevice.__file__ is under sandbox.fakes (asserted by test_harness).

fake_engine : FakeEngine (function; requires sandbox)
    An in-process threaded HTTP server that stands in for the engine, bound to
    127.0.0.1 on the first free port in 8930-8949 (asserted never to be
    8910). Sets sandbox.env["KOKORO_PORT"] to its port.
      GET  /  -> 200 "ok"
      POST /  -> records {"body": str, "t_start": float, "t_end": float} in
                 .requests, sleeps .delay seconds (default 0.0), then answers:
                   - .fail_with is None (default): 200, Content-Type audio/L16,
                     body .pcm (default: 4800 bytes of non-silent s16le, i.e.
                     0.1 s at 24 kHz mono)
                   - .fail_with is bytes: 200, that body verbatim, Content-Type
                     .fail_content_type (default "application/json")
                 An empty/whitespace body gets 400, like the real server.
    Attributes: .port, .url ("http://127.0.0.1:<port>/"), .requests, .delay,
    .pcm, .fail_with, .fail_content_type. All mutable from the test at any
    time. Shut down at teardown.

HELPERS (plain functions — ``from conftest import ...``)
--------------------------------------------------------
assistant_turn(content)   -> dict   assistant transcript line. ``content`` is a
                                    str (one text block) or a list whose items
                                    are str (text block) or dict (block as-is).
tool_turn(name="Bash", **input)  -> dict   assistant line whose only block is a
                                    tool_use (no text at all).
user_turn(text)           -> dict   user transcript line.
write_transcript(path, turns) -> Path   writes the dicts as JSONL, one per
                                    line; ``turns`` items that are str are
                                    written verbatim (to inject a corrupt line).
hook_payload(session_id, **fields) -> str   JSON for a hook's stdin, e.g.
                                    hook_payload("sid1", transcript_path=str(p))
                                    or hook_payload("sid1", prompt="tts on").
NON_SILENT_PCM            bytes    the default engine body (4800 bytes).

``repo`` and ``repo/hooks`` are on sys.path, so ``import talkback`` and
``import speak_last_reply`` work in-process.

MARKERS
-------
``@pytest.mark.pure`` — no sandbox, no subprocess, no symlink, no bash: only
``tmp_path`` and function calls. These are what the windows-latest CI job
runs (``pytest -q -m pure``).
"""

import http.server
import json
import math
import os
import re
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "hooks"))
sys.path.insert(0, str(REPO))

SYSTEM_PATH = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
DEAD_PORT = "8999"
ENGINE_PORTS = range(8930, 8950)
FORBIDDEN_PORT = 8910


def _tone(n_bytes=4800, hz=440.0, rate=24000, amp=8000):
    n = n_bytes // 2
    return b"".join(struct.pack("<h", int(amp * math.sin(2 * math.pi * hz * i / rate))) for i in range(n))


NON_SILENT_PCM = _tone()


# --------------------------------------------------------------------------
# transcript helpers
# --------------------------------------------------------------------------
def assistant_turn(content):
    if isinstance(content, str):
        blocks = [{"type": "text", "text": content}]
    else:
        blocks = [{"type": "text", "text": b} if isinstance(b, str) else b for b in content]
    return {"type": "assistant", "message": {"role": "assistant", "content": blocks}}


def tool_turn(name="Bash", **tool_input):
    block = {"type": "tool_use", "id": "toolu_test", "name": name, "input": tool_input}
    return {"type": "assistant", "message": {"role": "assistant", "content": [block]}}


def user_turn(text):
    return {"type": "user", "message": {"role": "user", "content": text}}


def write_transcript(path, turns):
    path = Path(path)
    with path.open("w") as f:
        for t in turns:
            f.write(t if isinstance(t, str) else json.dumps(t))
            f.write("\n")
    return path


def hook_payload(session_id, **fields):
    return json.dumps({"session_id": session_id, **fields})


# --------------------------------------------------------------------------
# shim and fake sources
# --------------------------------------------------------------------------
_LOGGER = '''\
import os, shlex, sys, time
LOG = {log!r}
def log(name, args):
    with open(LOG, "a") as f:
        f.write(f"{{time.time()}} {{name}} {{shlex.join([str(a) for a in args])}}\\n")
'''

_OSASCRIPT = _LOGGER + '''\
log("osascript", sys.argv[1:])
'''

# Stand-in for a bin/ script that is still the pkill version (safety guard).
_STANDIN = _LOGGER + '''\
STOP = {stop!r}
log({name!r}, sys.argv[1:])
if {name!r} == "shush":
    open(STOP, "a").close()
'''

_SOUNDDEVICE = '''\
"""Sandbox stand-in for the sounddevice module: records stream parameters
and writes to the calls log, sleeps instead of playing, never opens an audio
device. See tests/conftest.py, FAKE SOUNDDEVICE."""
import os, shlex, time

LOG = {log!r}
STOP = {stop!r}


def _log(name, args):
    with open(LOG, "a") as f:
        f.write(f"{{time.time()}} {{name}} {{shlex.join([str(a) for a in args])}}\\n")


class PortAudioError(Exception):
    pass


class RawOutputStream:
    def __init__(self, samplerate=None, blocksize=None, device=None, channels=None, dtype=None,
                 latency=None, extra_settings=None, callback=None, finished_callback=None,
                 clip_off=None, dither_off=None, never_drop_input=None,
                 prime_output_buffers_using_stream_callback=None):
        self.samplerate = samplerate
        self.blocksize = blocksize
        self.device = device
        self.channels = channels
        self.dtype = dtype
        self.active = False
        self.stopped = True
        self.closed = False
        self._opened = False
        self._writes = 0

    def start(self):
        if self.closed:
            raise PortAudioError("Error starting stream: the stream is closed")
        if not self._opened:
            self._opened = True
            _log("play", [self.samplerate, self.channels, self.dtype])
        self.active, self.stopped = True, False

    def stop(self, ignore_errors=True):
        self.active, self.stopped = False, True

    def abort(self, ignore_errors=True):
        self.stop()

    def write(self, data):
        if not self.active:
            raise PortAudioError("Error writing to stream: the stream is not started")
        n = memoryview(data).nbytes
        self._writes += 1
        _log("write", [n])
        if self._writes == 1 and os.environ.get("FAKE_PLAYER_TOUCH_STOP"):
            open(STOP, "a").close()
        time.sleep(float(os.environ.get("FAKE_PLAYER_SLEEP", "0.15")))

    def close(self, ignore_errors=True):
        if self.closed:
            return
        self.closed = True
        self.active, self.stopped = False, True
        if self._opened:
            _log("play-done", [])

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()


class OutputStream(RawOutputStream):
    pass


def stop(ignore_errors=True):
    pass


def wait(ignore_errors=True):
    return None


def play(*args, **kwargs):
    raise PortAudioError("sandbox: sd.play() is the mono-in-one-ear path this project avoids; use RawOutputStream")
'''

_RAISING_MODULE = 'raise ImportError("sandbox: the real model is never loaded")\n'


def _write_exec(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _shim(dirpath, impl_dir, name, source):
    impl = impl_dir / f"{name}.py"
    impl.write_text(source)
    _write_exec(dirpath / name, f'#!/bin/bash\nexec "{sys.executable}" "{impl}" "$@"\n')


# --------------------------------------------------------------------------
# sandbox
# --------------------------------------------------------------------------
class Sandbox:
    def __init__(self, root, repo):
        self.repo = repo
        self.home = root / "home"
        auto = self.home / ".claude" / "automation"
        self.hooks = auto / "notifications"
        self.bin = self.home / "bin"
        self.recording = auto / "recording"
        self.lastreply = auto / "lastreply"
        self.stopflag = auto / ".tts-stop"
        self.player_pid = auto / ".player.pid"
        self.server_pid = auto / "kokoro" / "server.pid"
        self.log = self.hooks / "speak.log"
        self.shims = root / "shims"
        self.fakes = root / "fakes"
        self.calls = root / "calls.log"
        self._impl = root / "shim_impl"
        self._tmp = root / "tmp"
        for d in (self.hooks, self.bin, self.recording, self.lastreply, self.shims, self.fakes, self._impl, self._tmp):
            d.mkdir(parents=True, exist_ok=True)
        self.calls.touch()

        log, stop = str(self.calls), str(self.stopflag)

        # hooks, as install.sh lays them out
        for f in (repo / "hooks").iterdir():
            if f.is_file():
                shutil.copy(f, self.hooks / f.name)
        for f in self.hooks.glob("*.sh"):
            _write_exec(f, f.read_text())

        # bin/, copied whole — except a script that still pkills, which is
        # replaced by a logging stand-in. The real pkill scripts kill the
        # owner's live playback; they must never run from a test.
        for f in (repo / "bin").iterdir():
            if not f.is_file():
                continue
            if "pkill" in f.read_text(errors="ignore"):
                _shim(self.bin, self._impl, f.name, _STANDIN.format(log=log, stop=stop, name=f.name))
            else:
                shutil.copy(f, self.bin / f.name)
                _write_exec(self.bin / f.name, (self.bin / f.name).read_text())

        # PYTHONPATH fakes: the audio device and the model never get touched
        (self.fakes / "sounddevice.py").write_text(_SOUNDDEVICE.format(log=log, stop=stop))
        for name in ("torch", "kokoro", "kokoro_onnx"):
            (self.fakes / f"{name}.py").write_text(_RAISING_MODULE)

        _shim(self.shims, self._impl, "osascript", _OSASCRIPT.format(log=log))
        _write_exec(self.shims / "python3", f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')

        self.env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(self.shims), str(self.bin), *SYSTEM_PATH]),
            "PYTHONPATH": os.pathsep.join([str(self.fakes), str(self.repo)]),
            "TALKBACK_PYTHON": sys.executable,
            "KOKORO_PORT": DEAD_PORT,
            "LANG": "en_US.UTF-8",
            "LC_ALL": "en_US.UTF-8",
            "TMPDIR": str(self._tmp),
        }

    # -- running things ---------------------------------------------------
    def run(self, cmd, stdin=None, env=None, timeout=30):
        return subprocess.run(
            [str(c) for c in cmd],
            input=stdin,
            env=self.env if env is None else env,
            cwd=str(self.repo),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )

    @staticmethod
    def talkback(*args):
        return [sys.executable, "-m", "talkback", *[str(a) for a in args]]

    def arm(self, sid):
        (self.recording / sid).touch()

    @staticmethod
    def wait_for(predicate, timeout=10, interval=0.05):
        deadline = time.monotonic() + timeout
        while True:
            if predicate():
                return True
            if time.monotonic() > deadline:
                return False
            time.sleep(interval)

    def playing(self):
        patterns = (
            re.escape("-m talkback play") + ".*" + re.escape(str(self.home)),
            re.escape(str(self.hooks / "play_reply.sh")),  # the shell worker, until its shim lands
        )
        for pattern in patterns:
            r = subprocess.run(["/usr/bin/pgrep", "-f", pattern], capture_output=True, check=False)
            if r.returncode == 0:
                return True
        return False

    def wait_quiet(self, timeout=10):
        return self.wait_for(lambda: not self.playing(), timeout=timeout)

    # -- inspecting the calls log -----------------------------------------
    def read_calls(self):
        out = []
        for line in self.calls.read_text().splitlines():
            parts = shlex.split(line)
            if len(parts) >= 2:
                out.append((float(parts[0]), parts[1], parts[2:]))
        return out

    def calls_to(self, name):
        return [args for _, n, args in self.read_calls() if n == name]

    def plays(self):
        return self.calls_to("play")

    def writes(self):
        return [int(args[0]) for args in self.calls_to("write")]


@pytest.fixture(scope="session")
def repo():
    return REPO


@pytest.fixture
def sandbox(tmp_path, repo):
    sb = Sandbox(tmp_path, repo)
    yield sb
    sb.wait_quiet(timeout=10)


# --------------------------------------------------------------------------
# fake engine
# --------------------------------------------------------------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def do_POST(self):
        eng = self.server.engine
        t0 = time.time()
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace")
        rec = {"body": body, "t_start": t0, "t_end": None}
        with eng.lock:
            eng.requests.append(rec)
        if eng.delay:
            time.sleep(eng.delay)
        if not body.strip():
            self.send_response(400)
            self.end_headers()
            rec["t_end"] = time.time()
            return
        if eng.fail_with is not None:
            payload, ctype = eng.fail_with, eng.fail_content_type
        else:
            payload, ctype = eng.pcm, "audio/L16"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        try:
            self.wfile.write(payload)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass
        rec["t_end"] = time.time()


class FakeEngine:
    def __init__(self):
        self.requests = []
        self.lock = threading.Lock()
        self.delay = 0.0
        self.pcm = NON_SILENT_PCM
        self.fail_with = None
        self.fail_content_type = "application/json"
        self.server = None
        for port in ENGINE_PORTS:
            try:
                self.server = http.server.ThreadingHTTPServer(("127.0.0.1", port), _Handler)
            except OSError:
                continue
            break
        if self.server is None:
            pytest.fail(f"no free port in {ENGINE_PORTS.start}-{ENGINE_PORTS.stop - 1}")
        self.server.engine = self
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        assert self.port != FORBIDDEN_PORT
        self.url = f"http://127.0.0.1:{self.port}/"
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)


@pytest.fixture
def fake_engine(sandbox):
    eng = FakeEngine()
    sandbox.env["KOKORO_PORT"] = str(eng.port)
    yield eng
    sandbox.wait_quiet(timeout=10)
    eng.close()
