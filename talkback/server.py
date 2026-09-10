"""The engine's HTTP server (``talkback server``) and its start / stop /
status / restart verbs — what ``bin/kokoro-server`` did with ``pgrep`` and
``pkill``, now through ``kokoro/server.pid``. Owner: server.

HTTP contract, identical for both backends:
  GET  /  -> 200 ``ok``
  POST /  -> blank body: 400 before any synthesis; else 200
             ``Content-Type: audio/L16``, s16le 24 kHz mono, one chunk written
             and flushed as the model yields it, under one lock (the model is
             not reentrant). A client that hangs up mid-reply (shush) is normal.
Speed is ``float(KOKORO_SPEED or 1.15)``; the backend is ``TALKBACK_ENGINE``
(``torch`` default | ``onnx``); every request is logged to
``~/.claude/automation/kokoro/requests.log`` when that directory exists.
"""

import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from talkback import engine_client, log, paths, procs, state

DEFAULT_SPEED = 1.15
BACKENDS = ("torch", "onnx")
WARM_UP_TEXT = "Ready."
START_POLLS, START_INTERVAL = 60, 0.5  # 30 s, as bin/kokoro-server waited
RESTART_PAUSE = 1.0  # `"$0" stop; sleep 1; "$0" start`


@dataclass
class Server:
    handler: type
    port: int
    device: str
    backend: str
    voice: object
    pipe: object
    reqlog: str
    lock: threading.Lock
    speed: float = DEFAULT_SPEED


def speed_of(env) -> float:
    return float(env.get("KOKORO_SPEED") or DEFAULT_SPEED)


def pcm_bytes(audio) -> bytes:
    """One yielded chunk -> s16le bytes. A torch tensor is moved to the CPU
    first; anything else goes through ``np.asarray``. Identical float samples
    give identical bytes whichever backend produced them."""
    import numpy as np

    a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
    return (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()


def _logreq(reqlog: str, text: str, client: str) -> None:
    """Best-effort, never creates the directory: a missing ``kokoro/`` dir
    means no log, not a broken reply."""
    try:
        with open(reqlog, "a", encoding="utf-8") as f:
            f.write(f"{log.ts()} from={client} chars={len(text)} :: {text[:60]!r}\n")
    except OSError:
        pass


def _handler_for(server: "Server") -> type:
    """The handler class is built per boot and closes over its Server, so two
    boots in one process never share a voice, a lock or a log path."""

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def do_POST(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            text = self.rfile.read(n).decode("utf-8", "replace")
            _logreq(server.reqlog, text, self.client_address[0])
            if not text.strip():
                self.send_response(400)
                self.end_headers()
                return
            try:
                self.send_response(200)
                self.send_header("Content-Type", "audio/L16")
                self.end_headers()
                with server.lock:
                    for audio in server.pipe(text, server.speed):
                        self.wfile.write(pcm_bytes(audio))
                        self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass  # client hit shush (before or during the audio); normal

    return H


def _backend(env) -> str:
    backend = env.get("TALKBACK_ENGINE") or "torch"
    if backend not in BACKENDS:
        raise SystemExit(f"TALKBACK_ENGINE={backend!r} is not a backend; expected one of {', '.join(BACKENDS)}")
    return backend


def boot(voice_path: "str | None" = None, env=None, home=None) -> Server:
    """Load the backend named by ``TALKBACK_ENGINE``, warm it up once with
    "Ready." so the first real request is not the slow one, and build the
    handler class for this Server. ``voice_path`` (torch only) overrides
    ``TALKBACK_VOICE`` and the installed ``kokoro/icg_voice.pt``."""
    env = os.environ if env is None else env
    backend = _backend(env)
    if backend == "torch":
        from talkback import synth_torch

        vp = voice_path or env.get("TALKBACK_VOICE") or str(paths.engine_dir(home) / "icg_voice.pt")
        synthesize, voice, device = synth_torch.load(vp, env)
    else:
        from talkback import synth_onnx

        model = env.get("TALKBACK_ONNX_MODEL") or str(paths.engine_dir(home) / "kokoro-v1.0.onnx")
        voices = env.get("TALKBACK_ONNX_VOICES") or str(paths.engine_dir(home) / "voices-v1.0.bin")
        synthesize, voice, device = synth_onnx.load(model, voices, env)

    server = Server(
        handler=None,
        port=paths.port(env),
        device=device,
        backend=backend,
        voice=voice,
        pipe=synthesize,
        reqlog=str(paths.requests_log(home)),
        lock=threading.Lock(),
        speed=speed_of(env),
    )
    for _ in synthesize(WARM_UP_TEXT, server.speed):  # warm the graph
        pass
    server.handler = _handler_for(server)
    return server


def ready_line(server: Server) -> str:
    return f"kokoro server ready on 127.0.0.1:{server.port} (device={server.device})"


def _exit_on_sigterm(signum, frame):
    raise SystemExit(0)


def serve(server: Server, home=None) -> None:
    """Run the server in the foreground — what launchd and the Windows Startup
    launcher run. Writes ``kokoro/server.pid`` for ``server stop`` and clears
    it on the way out; under ``pythonw`` (no console: ``sys.stdout is None``)
    stdout and stderr go to ``kokoro/server.log`` first."""
    if sys.stdout is None or sys.stderr is None:
        logfile = paths.server_log(home)
        logfile.parent.mkdir(parents=True, exist_ok=True)
        f = open(logfile, "a", encoding="utf-8", buffering=1)  # noqa: SIM115  # lives as long as the process
        sys.stdout = sys.stderr = f
    httpd = ThreadingHTTPServer(("127.0.0.1", server.port), server.handler)
    pidfile = paths.server_pid(home)
    state.write_pid(pidfile, os.getpid())
    previous = None
    try:
        try:
            # `server stop` -> clean exit, pid file cleared
            previous = signal.signal(signal.SIGTERM, _exit_on_sigterm)
        except (ValueError, OSError):
            pass  # not the main thread; the pid file is cleared by `stop` anyway
        print(ready_line(server), flush=True)
        httpd.serve_forever()
    finally:
        httpd.server_close()
        state.clear_pid(pidfile)
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)


# -- the verbs of bin/kokoro-server -------------------------------------------
def _say(line: str, quiet: bool = False) -> None:
    if not quiet:
        print(line, flush=True)


def start(env, home=None) -> int:
    """Spawn ``talkback server`` with its output appended to ``server.log`` and
    wait for the port to answer. Blocks up to 30 s, returning as soon as the
    engine answers or the child exits."""
    port = paths.port(env)
    if engine_client.up(port):
        _say("already running")
        return 0
    pid = state.read_pid(paths.server_pid(home))
    if pid and procs.alive(pid):
        _say("a server process is already up (different port?) — not spawning another")
        return 0
    logfile = paths.server_log(home)
    child = procs.spawn_logged(procs.talkback_argv("server"), logfile, env=dict(env))
    for _ in range(START_POLLS):
        if engine_client.up(port):
            _say(f"started (pid {child.pid})")
            return 0
        if child.poll() is not None:
            break
        time.sleep(START_INTERVAL)
    _say(f"failed to start — see {logfile}")
    return 1


def _stop(home=None, quiet: bool = False) -> int:
    pidfile = paths.server_pid(home)
    pid = state.read_pid(pidfile)
    if pid and procs.alive(pid) and procs.terminate(pid):
        _say("stopped", quiet)
    else:
        _say("not running", quiet)
    state.clear_pid(pidfile)  # ours, or stale: either way it is done with
    return 0


def stop(env, home=None) -> int:
    return _stop(home)


def status(env, home=None) -> int:
    port = paths.port(env)
    _say(f"running on {port}" if engine_client.up(port) else "not running")
    return 0


def restart(env, home=None) -> int:
    _stop(home, quiet=True)
    time.sleep(RESTART_PAUSE)
    return start(env, home)
