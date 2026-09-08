"""Shared test harness for Talkback Local.

Everything here keeps the tests away from the owner's live install: every
subprocess runs with HOME pointed at a throwaway sandbox, with fake ``ffplay``
/ ``osascript`` / ``shush`` / ``kokoro-server`` shims ahead of the real ones on
PATH, and with KOKORO_PORT pointing at either a dead port or the in-process
fake engine — never at the real server on 8910. Nothing in this file calls
``pkill``; teardown only *waits* for the sandbox's own background players.

Standard library only.

FIXTURES
--------
repo : pathlib.Path (session)
    The repository root (the directory that holds ``hooks/``, ``bin/``,
    ``install.sh``).

sandbox : Sandbox (function)
    A fresh sandbox HOME per test, built under ``tmp_path``. Attributes:

    .home        sandbox HOME
    .hooks       home/.claude/automation/notifications — every file from the
                 repo's hooks/ copied in, *.sh made executable. This mirrors
                 install.sh, so the scripts find each other via $HOME exactly
                 as in production. Run them as ``sandbox.hooks / "x.sh"``.
    .bin         home/bin — the REAL bin/replay and bin/recmode copied in, plus
                 FAKE ``shush`` and ``kokoro-server``:
                   shush          logs "shush <args>" to the calls log, touches
                                  the stop flag (like the real one), exits 0.
                                  It does NOT kill anything — the real shush
                                  uses machine-wide pkill, which is exactly what
                                  the sandbox exists to avoid. A background
                                  play_reply.sh therefore keeps running until it
                                  notices the stop flag at its next chunk.
                   kokoro-server  logs "kokoro-server <args>", exits 0. It never
                                  starts anything; use ``fake_engine`` for a
                                  live engine.
    .recording   home/.claude/automation/recording  (armed-session flags)
    .lastreply   home/.claude/automation/lastreply  (<sid>.txt / <sid>.pcm)
    .stopflag    home/.claude/automation/.tts-stop
    .log         .hooks / "speak.log" (may not exist until a hook writes it)
    .shims       a dir at the front of PATH holding:
                   ffplay     logs "ffplay <args>", then — if
                              $FAKE_FFPLAY_TOUCH_STOP is set (any value) —
                              touches .stopflag, then sleeps
                              $FAKE_FFPLAY_SLEEP seconds (default 0.15), then
                              logs "ffplay-done". Drains stdin when given "-".
                              Never touches the real ffplay or any audio device.
                   osascript  logs "osascript <args>", exits 0.
                   python3    exec-wrapper around sys.executable, so scripts
                              that call python3 run on pytest's interpreter.
                   jq         symlink to the machine's jq only when jq is not
                              in /usr/bin:/bin:/usr/sbin:/sbin.
    .calls       Path of the calls log (one line per shim invocation:
                 "<time.time()> <name> <shell-quoted args>").
    .read_calls() -> list[(t: float, name: str, args: list[str])]
                 Parsed calls log, in order. Names: "ffplay", "ffplay-done",
                 "osascript", "shush", "kokoro-server".
    .calls_to(name) -> list[list[str]]
                 Just the args of every call to ``name``.
    .env         A clean environment dict for subprocesses:
                   HOME=sandbox home
                   PATH=shims:home/bin:/usr/bin:/bin:/usr/sbin:/sbin
                   KOKORO_PORT="8999" — a dead port, nothing listens there —
                     unless the ``fake_engine`` fixture is also requested, in
                     which case it is the engine's port.
                   LANG=LC_ALL=en_US.UTF-8, TMPDIR=<sandbox>/tmp
                 Note: macOS ``mktemp -d`` (as the hooks call it, no template)
                 ignores TMPDIR and uses the per-user Darwin temp dir, exactly
                 as in production; play_reply.sh removes its own dir when done.
                 No TTS_* or ELEVENLABS_* keys. Copy it and add keys to test
                 tunables: ``env = dict(sandbox.env, TTS_ENGINE="elevenlabs")``.
    .run(cmd, stdin=None, env=None, timeout=30) -> subprocess.CompletedProcess
                 subprocess.run in text mode with capture_output, cwd=repo,
                 env defaulting to .env. Never raises on a non-zero exit.
    .arm(sid)    touch recording/<sid> (what "TTS on" does).
    .wait_for(predicate, timeout=10, interval=0.05) -> bool
                 Poll until predicate() is truthy; False on timeout.
    .playing() -> bool
                 True while a play_reply.sh from THIS sandbox is running
                 (read-only pgrep on the sandbox path).
    .wait_quiet(timeout=10) -> bool
                 wait_for(lambda: not self.playing()).

    Teardown waits up to 10 s for the sandbox's own play_reply.sh to finish.
    It never kills anything.

fake_engine : FakeEngine (function; requires sandbox)
    An in-process threaded HTTP server that stands in for engine/server.py,
    bound to 127.0.0.1 on the first free port in 8930-8949 (asserted never to
    be 8910). Sets sandbox.env["KOKORO_PORT"] to its port.
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

``repo/hooks`` is on sys.path, so ``import speak_last_reply`` works.
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
# shim sources
# --------------------------------------------------------------------------
_LOGGER = '''\
import os, shlex, sys, time
LOG = {log!r}
def log(name, args):
    with open(LOG, "a") as f:
        f.write(f"{{time.time()}} {{name}} {{shlex.join(args)}}\\n")
'''

_FFPLAY = _LOGGER + '''\
STOP = {stop!r}
args = sys.argv[1:]
log("ffplay", args)
if os.environ.get("FAKE_FFPLAY_TOUCH_STOP"):
    open(STOP, "a").close()
if "-" in args:
    try:
        sys.stdin.buffer.read()
    except OSError:
        pass
time.sleep(float(os.environ.get("FAKE_FFPLAY_SLEEP", "0.15")))
log("ffplay-done", [])
'''

_OSASCRIPT = _LOGGER + '''\
log("osascript", sys.argv[1:])
'''

_SHUSH = _LOGGER + '''\
STOP = {stop!r}
log("shush", sys.argv[1:])
open(STOP, "a").close()
'''

_KOKORO_SERVER = _LOGGER + '''\
log("kokoro-server", sys.argv[1:])
'''


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
        self.log = self.hooks / "speak.log"
        self.shims = root / "shims"
        self.calls = root / "calls.log"
        self._impl = root / "shim_impl"
        self._tmp = root / "tmp"
        for d in (self.hooks, self.bin, self.recording, self.lastreply, self.shims, self._impl, self._tmp):
            d.mkdir(parents=True, exist_ok=True)
        self.calls.touch()

        # hooks, as install.sh lays them out
        for f in (repo / "hooks").iterdir():
            if f.is_file():
                shutil.copy(f, self.hooks / f.name)
        for f in self.hooks.glob("*.sh"):
            _write_exec(f, f.read_text())

        # real commands that are safe: they only read files and spawn players
        for name in ("replay", "recmode"):
            shutil.copy(repo / "bin" / name, self.bin / name)
            _write_exec(self.bin / name, (self.bin / name).read_text())

        log, stop = str(self.calls), str(self.stopflag)
        _shim(self.bin, self._impl, "shush", _SHUSH.format(log=log, stop=stop))
        _shim(self.bin, self._impl, "kokoro-server", _KOKORO_SERVER.format(log=log))
        _shim(self.shims, self._impl, "ffplay", _FFPLAY.format(log=log, stop=stop))
        _shim(self.shims, self._impl, "osascript", _OSASCRIPT.format(log=log))
        _write_exec(self.shims / "python3", f'#!/bin/bash\nexec "{sys.executable}" "$@"\n')

        jq = shutil.which("jq", path=os.pathsep.join(SYSTEM_PATH)) or shutil.which("jq")
        if jq and str(Path(jq).parent) not in SYSTEM_PATH:
            (self.shims / "jq").symlink_to(jq)

        self.env = {
            "HOME": str(self.home),
            "PATH": os.pathsep.join([str(self.shims), str(self.bin), *SYSTEM_PATH]),
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
        pattern = re.escape(str(self.hooks / "play_reply.sh"))
        r = subprocess.run(["/usr/bin/pgrep", "-f", pattern], capture_output=True, check=False)
        return r.returncode == 0

    def wait_quiet(self, timeout=10):
        return self.wait_for(lambda: not self.playing(), timeout=timeout)

    # -- inspecting the shims ---------------------------------------------
    def read_calls(self):
        out = []
        for line in self.calls.read_text().splitlines():
            parts = shlex.split(line)
            if len(parts) >= 2:
                out.append((float(parts[0]), parts[1], parts[2:]))
        return out

    def calls_to(self, name):
        return [args for _, n, args in self.read_calls() if n == name]


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
