"""``talkback verify <file> <engine> <logfile>``: what ``hooks/verify_audio.sh``
did — decide whether a downloaded file is actually audio, and make a failure
loud. A failure must never be silent: a dated line in the log, a desktop
notification, and — for a cloud engine — the failure read aloud by the local
engine, which is free and independent. Owner: player."""

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from talkback import engine_client, log, paths, procs

MIN_BYTES = 2000  # well under a second in every format we use
DEFAULT_DETAIL = "the service returned an error instead of audio"


def detail_of(data: bytes) -> str:
    """The message of an API error body: the JSON ``message`` / ``detail`` /
    ``error`` walk, else the body's first 180 characters on one line —
    exactly what the shell heredoc printed."""
    raw = bytes(data).decode("utf-8", "replace")[:2000]
    try:
        d = json.loads(raw)
        while isinstance(d, dict):
            for k in ("message", "detail", "error"):
                if k in d:
                    d = d[k]
                    break
            else:
                break
        return str(d)[:180]
    except Exception:  # noqa: BLE001  # not JSON: quote the text itself
        return raw.strip().replace("\n", " ")[:180]


def notify(msg: str, title: str) -> None:
    """A desktop notification through ``osascript`` — macOS with osascript on
    PATH only; elsewhere a no-op. Double quotes are stripped from the message
    (``${msg//\\"/}``) so it cannot break out of the AppleScript string."""
    if sys.platform != "darwin":
        return
    exe = shutil.which("osascript")
    if not exe:
        return
    msg = msg.replace('"', "")
    try:
        subprocess.run(  # argv built here, not from a shell string
            [exe, "-e", f'display notification "{msg}" with title "{title}"'],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        pass


def _announce(text: str, port: int, log_path: Path) -> None:
    """Say ``text`` through the local engine in a detached ``play --file``
    (the scratch dir is removed by the player once it has played)."""
    from talkback import play  # the dir-naming convention lives there

    data = engine_client.synth(port, text)
    if not data or play.looks_like_error_body(data):
        return
    scratch = Path(tempfile.mkdtemp(prefix=play.FAIL_DIR_PREFIX))
    pcm = scratch / play.FAIL_FILE
    pcm.write_bytes(data)
    procs.spawn_detached(procs.talkback_argv("play", "--file", str(pcm), "--log", str(log_path)))


def say_fail(msg: str, engine: str, log_path: Path, env, home=None) -> None:
    """Log line + notification + (a cloud engine, with the local one up) the
    failure spoken aloud. The local engine never announces its own failure
    through itself."""
    log.append(Path(log_path), f"TTS FAILED [{engine}]: {msg}")
    notify(msg, f"Voice failed ({engine})")
    if engine != "kokoro":
        port = paths.port(env)
        if engine_client.up(port):
            _announce("Voice failed. " + msg, port, Path(log_path))


def verify(path: Path, engine: str, log_path: Path, env, home=None) -> bool:
    """True = real audio (the caller publishes it). False = not audio (the
    caller discards it), after :func:`say_fail`."""
    path = Path(path)
    if not path.is_file():
        say_fail("nothing was downloaded", engine, log_path, env, home)
        return False
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            head = f.read(1)
    except OSError:
        size, head = 0, b""
    # an API error is a 200 response with a JSON or HTML body, not audio
    if head in (b"{", b"<"):
        try:
            body = path.read_bytes()
        except OSError:
            body = head
        say_fail(detail_of(body) or DEFAULT_DETAIL, engine, log_path, env, home)
        return False
    # too small to be speech
    if size < MIN_BYTES:
        say_fail(f"only {size} bytes came back — not enough to be speech", engine, log_path, env, home)
        return False
    return True
