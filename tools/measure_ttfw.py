#!/usr/bin/env python3
"""Measure the Python player on this Mac — the numbers the README quotes.

    python3 tools/measure_ttfw.py [--port 8930] [--runs 10] [--keep]

Nothing here touches the live install. Everything runs under a throwaway HOME
from ``mktemp``: ``install.sh`` builds a sandbox venv there (real ``uv``,
``launchctl`` shimmed to a no-op so launchd never sees the sandbox plist), a
sandbox server is started on ``--port`` (never 8910), and the Stop hook is fed
ten unique replies. The Hugging Face cache is reused **read-only**
(``HF_HOME`` = the real cache, ``HF_HUB_OFFLINE=1``) so the model is not
downloaded again; it is never written to or cleared. Every ``TTS_*`` /
``TALKBACK_*`` / ``ELEVENLABS_*`` / ``KOKORO_DEVICE`` variable is scrubbed —
the environment is built from scratch.

The only sound device ever opened is the default output, and **every sample
written to it is zero**: the hook subprocesses get a measurement
``sounddevice`` module first on ``PYTHONPATH`` that loads the venv's real
module by file path, records the time of the process's first write, and hands
the real stream the same number of all-zero bytes. The product code is not
instrumented.

Rows printed (paste into the README with the command line):
  Installer, start to finish · Warm start, model already cached ·
  Time to first word of a reply (median; max in parentheses) ·
  Resident memory while running · Disk.
Gate: the median time to first word must be <= 1.0 s (exit 1 otherwise).
"""

import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from talkback import engine_client, paths, procs, state

FORBIDDEN_PORT = 8910
GATE_S = 1.0
HOOK_BUDGET_S = 0.5
SYSTEM_PATH = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin"]

# The measurement stand-in for sounddevice (see the module docstring). {real}
# is the venv's sounddevice.py, baked in at render time.
MEASURE_SOUNDDEVICE = '''\
"""Measurement stand-in for sounddevice (tools/measure_ttfw.py): the venv's
real module loaded by file path; RawOutputStream.write records the time of
this process's FIRST write, then hands the real stream the same number of
ALL-ZERO bytes — same length, same timing, no sound."""
import importlib.util
import os
import sys
import time

_spec = importlib.util.spec_from_file_location("_talkback_real_sounddevice", {real!r})
_real = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _real
_spec.loader.exec_module(_real)
globals().update({{k: v for k, v in vars(_real).items() if not k.startswith("__")}})

_TRACE = os.environ.get("TALKBACK_MEASURE_TRACE")
_first = [True]


class RawOutputStream(_real.RawOutputStream):
    def write(self, data):
        n = memoryview(data).nbytes
        if _first[0] and _TRACE:
            _first[0] = False
            with open(_TRACE, "a") as f:
                f.write(f"{{time.time()}}\\n")
        return super().write(bytes(n))  # zeros: the device is opened, nothing is heard
'''


def _reply_text(i: int, runs: int) -> str:
    """Three sentences, ~180 chars, unique per run so the duplicate guard never fires."""
    return (
        f"Measurement run {i + 1} of {runs}. "
        "This sentence is here so the reply has a middle part of ordinary length for the player. "
        "The third sentence closes the reply and ends the run."
    )


def _transcript(path: Path, text: str) -> None:
    turns = [
        {"type": "user", "message": {"role": "user", "content": "measure"}},
        {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}},
    ]
    with path.open("w", encoding="utf-8") as f:
        for t in turns:
            f.write(json.dumps(t) + "\n")


def _wait(predicate, timeout: float, interval: float = 0.02):
    deadline = time.monotonic() + timeout
    while True:
        if predicate():
            return True
        if time.monotonic() > deadline:
            return False
        time.sleep(interval)


def _du_kb(path: Path) -> int:
    r = subprocess.run(["/usr/bin/du", "-sk", str(path)], capture_output=True, text=True, check=False)
    try:
        return int(r.stdout.split()[0])
    except (IndexError, ValueError):
        return 0


def _fmt_bytes_kb(kb: int) -> str:
    return f"{kb / 1024 / 1024:.1f} GB" if kb >= 1024 * 1024 else f"{kb / 1024:.0f} MB"


def _p90(values):
    s = sorted(values)
    return s[min(len(s) - 1, max(0, round(0.9 * len(s)) - 1))]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8930, help="sandbox engine port (never 8910)")
    ap.add_argument("--runs", type=int, default=10)
    ap.add_argument("--keep", action="store_true", help="leave the sandbox behind for inspection")
    ap.add_argument(
        "--uv-cache",
        choices=("real", "sandbox"),
        default="real",
        help="reuse the machine's uv wheel cache (how the README's installer figure was made) or start empty",
    )
    a = ap.parse_args(argv)
    if a.port == FORBIDDEN_PORT:
        print(f"refusing port {FORBIDDEN_PORT}: that is the live install's server", file=sys.stderr)
        return 2
    if sys.platform != "darwin":
        print("install.sh is macOS-only; run this on the Mac", file=sys.stderr)
        return 2

    real_home = Path(os.path.expanduser("~"))
    sb = Path(tempfile.mkdtemp(prefix="talkback-measure-"))
    home = sb / "home"
    shims, tmp, measure, traces = sb / "shims", sb / "tmp", sb / "measure", sb / "trace"
    for d in (home, shims, tmp, measure, traces):
        d.mkdir(parents=True)
    launchctl = shims / "launchctl"
    launchctl.write_text("#!/bin/bash\nexit 0\n")
    launchctl.chmod(0o755)

    env = {
        "HOME": str(home),
        "PATH": os.pathsep.join([str(shims), *SYSTEM_PATH]),
        "KOKORO_PORT": str(a.port),
        "HF_HOME": str(real_home / ".cache" / "huggingface"),  # read-only reuse
        "HF_HUB_OFFLINE": "1",
        "LANG": "en_US.UTF-8",
        "LC_ALL": "en_US.UTF-8",
        "TMPDIR": str(tmp),
    }
    if a.uv_cache == "real":
        env["UV_CACHE_DIR"] = str(real_home / ".cache" / "uv")
    cmdline = f"python3 tools/measure_ttfw.py --port {a.port} --runs {a.runs} --uv-cache {a.uv_cache}"
    print(f"sandbox: {sb}")
    print(f"command: {cmdline}")

    server = None
    rows = {}
    try:
        # 2. the installer, timed start to finish
        print("installing into the sandbox ...")
        t0 = time.monotonic()
        with (sb / "install.log").open("wb") as f:
            r = subprocess.run(["/bin/bash", str(REPO / "install.sh")], env=env, cwd=str(REPO),
                               stdin=subprocess.DEVNULL, stdout=f, stderr=subprocess.STDOUT, check=False)
        installer_s = time.monotonic() - t0
        if r.returncode != 0:
            print(f"install.sh failed ({r.returncode}); see {sb / 'install.log'}", file=sys.stderr)
            return 1
        rows["Installer, start to finish"] = f"**{installer_s:.0f}s**"
        venv_py = paths.venv_python(home)

        # 3. warm start: spawn the server, poll GET / until it answers
        print("starting the sandbox server ...")
        t0 = time.monotonic()
        server_log = (sb / "server.log").open("ab")
        server = subprocess.Popen([str(venv_py), "-m", "talkback", "server"], env=env, cwd=str(REPO),
                                  stdin=subprocess.DEVNULL, stdout=server_log, stderr=subprocess.STDOUT,
                                  start_new_session=True)
        ok = _wait(lambda: server.poll() is not None or engine_client.up(a.port), timeout=300, interval=0.1)
        warm_s = time.monotonic() - t0
        if not ok or server.poll() is not None:
            print(f"the sandbox server never answered on {a.port}; see {sb / 'server.log'}", file=sys.stderr)
            return 1
        rows["Warm start, model already cached"] = f"**~{warm_s:.0f}s**, once, at login"

        # 4. memory and disk
        r = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(server.pid)], capture_output=True, text=True, check=False)
        rss_kb = int(r.stdout.strip() or 0)
        rows["Resident memory while running"] = f"~{_fmt_bytes_kb(rss_kb)}"
        venv_kb = _du_kb(paths.engine_dir(home) / ".venv")
        model_dir = Path(env["HF_HOME"]) / "hub" / "models--hexgrad--Kokoro-82M"
        model = _fmt_bytes_kb(_du_kb(model_dir)) + " model" if model_dir.is_dir() else "model size not found"
        rows["Disk"] = f"{model} + ~{_fmt_bytes_kb(venv_kb)} Python environment"

        # 5. the measurement sounddevice, for the hook subprocesses only
        r = subprocess.run([str(venv_py), "-c", "import sounddevice; print(sounddevice.__file__)"],
                           env=env, capture_output=True, text=True, check=False)
        real_sd = r.stdout.strip()
        if r.returncode != 0 or not real_sd:
            print(f"the sandbox venv has no sounddevice: {r.stderr}", file=sys.stderr)
            return 1
        (measure / "sounddevice.py").write_text(MEASURE_SOUNDDEVICE.format(real=real_sd))

        # 6. ten replies through the Stop hook
        speak_log = paths.speak_log(home)
        ttfw, hook_wall = [], []
        for i in range(a.runs):
            sid = f"measure{i:02d}"
            tp = sb / f"run{i:02d}.jsonl"
            _transcript(tp, _reply_text(i, a.runs))
            trace = traces / f"run{i:02d}"
            state.arm(sid, home)
            hook_env = dict(env, PYTHONPATH=str(measure), TALKBACK_MEASURE_TRACE=str(trace))
            payload = json.dumps({"session_id": sid, "transcript_path": str(tp)})
            t0 = time.time()
            r = subprocess.run([str(venv_py), "-m", "talkback", "speak"], input=payload, env=hook_env,
                               cwd=str(REPO), capture_output=True, text=True, check=False)
            hook_wall.append(time.time() - t0)
            if r.returncode != 0:
                print(f"run {i}: the Stop hook exited {r.returncode}: {r.stderr}", file=sys.stderr)
                return 1
            if not _wait(trace.exists, timeout=30):
                print(f"run {i}: no first write within 30 s; see {speak_log}", file=sys.stderr)
                return 1
            t_first = float(trace.read_text().splitlines()[0])
            ttfw.append(t_first - t0)

            def _done(n=i + 1):
                return speak_log.exists() and speak_log.read_text().count("PLAY done") >= n

            if not _wait(_done, timeout=60):
                print(f"run {i}: the player never logged PLAY done; see {speak_log}", file=sys.stderr)
                return 1
            print(f"  run {i + 1:2d}: hook {hook_wall[-1]:.3f}s  first word {ttfw[-1]:.3f}s")

        med, p90, mx = statistics.median(ttfw), _p90(ttfw), max(ttfw)
        rows["Time to first word of a reply"] = f"**~{med:.1f}s** (median of {a.runs}; p90 {p90:.2f}s, max {mx:.2f}s)"
        hook_max = max(hook_wall)

        print()
        print("README rows (from this run):")
        for k, v in rows.items():
            print(f"| {k} | {v} |")
        print(f"measured with: {cmdline}")
        print(f"Stop hook wall time: max {hook_max:.3f}s (budget {HOOK_BUDGET_S}s)")
        print(f"first word: median {med:.3f}s  p90 {p90:.3f}s  max {mx:.3f}s  (gate: median <= {GATE_S}s)")

        failed = False
        if med > GATE_S:
            print(f"GATE FAILED: median time to first word {med:.3f}s > {GATE_S}s", file=sys.stderr)
            failed = True
        if hook_max > HOOK_BUDGET_S:
            print(f"GATE FAILED: the Stop hook took {hook_max:.3f}s > {HOOK_BUDGET_S}s", file=sys.stderr)
            failed = True
        return 1 if failed else 0
    finally:
        # 7. only the sandbox's own processes and files
        if server is not None and server.poll() is None:
            procs.terminate(server.pid)
            try:
                server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                server.kill()
        _wait(lambda: not paths.player_pid(home).exists(), timeout=15)
        if a.keep:
            print(f"sandbox kept at {sb}")
        else:
            shutil.rmtree(sb, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
