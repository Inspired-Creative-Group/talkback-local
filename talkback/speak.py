"""The Stop hook (``talkback speak``): what ``hooks/speak_last_reply.sh`` did —
speak Claude's last reply aloud, for THIS session only.

Armed per session:  ~/.claude/automation/recording/<session_id>
Toggle:             type "TTS on" / "TTS off" as the whole prompt
Stop mid-reply:     shush            Repeat:  replay

The hook itself never plays anything: it saves the text, makes sure the
engine is up, spawns the detached player and returns — always 0, so Claude
Code is never blocked.
"""

import contextlib
import io
import json
import os
import shutil
import tempfile
import time
from pathlib import Path

from talkback import engine_client, log, paths, procs, rewrite, shush, state

DUPLICATE_WINDOW = 120  # seconds: the same text again within this is not spoken twice
KEEP_DAYS = 3  # lastreply files this old are deleted (= find -mtime +2)


def _housekeeping(lastreply: Path, now: float) -> None:
    """Saved audio, text and marker files from sessions long gone."""
    try:
        entries = list(lastreply.iterdir())
    except OSError:
        return
    for f in entries:
        try:
            if f.is_file() and now - f.stat().st_mtime >= KEEP_DAYS * 86400:
                f.unlink()
        except OSError:
            pass


def _duplicate_age(marker: Path, digest: str) -> "int | None":
    """Seconds since the marker was written, when it holds this digest."""
    try:
        if marker.read_text(encoding="utf-8").strip() != digest:
            return None
        return int(time.time()) - int(marker.stat().st_mtime)
    except OSError:
        return None


def _ensure_engine(port: int, logp: Path, env, home) -> None:
    if engine_client.up(port):
        return
    log.append(logp, f"engine not answering on {port}; starting it")
    from talkback import server

    # kokoro-server start ran with its output discarded and its failure
    # ignored; the reply still goes to the player, which makes a dead engine
    # loud through verify.
    with (
        contextlib.redirect_stdout(io.StringIO()),
        contextlib.redirect_stderr(io.StringIO()),
        contextlib.suppress(Exception),  # a start that blows up must not block the hook
    ):
        server.start(env, home)


def run(stdin_text: str, env, home=None) -> int:
    """Hook JSON on ``stdin_text`` (``session_id``, ``transcript_path``);
    always returns 0 so Claude Code is never blocked."""
    try:
        payload = json.loads(stdin_text)
    except (TypeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    sid = str(payload.get("session_id") or "unknown")
    if not state.is_armed(sid, home):
        return 0

    lastreply = paths.lastreply_dir(home)
    logp = paths.speak_log(home)
    lastreply.mkdir(parents=True, exist_ok=True)

    tp = payload.get("transcript_path")
    if not isinstance(tp, str) or not tp or not os.path.isfile(tp):
        log.append(logp, "no transcript")
        return 0

    # never let two replies talk over each other
    shush.shush(home=home, quiet=True)

    tmp = tempfile.mkdtemp()
    reasons = io.StringIO()
    with contextlib.redirect_stderr(reasons):
        chars = rewrite.prepare(tp, tmp, env, home)
    if chars == 0:
        for line in reasons.getvalue().splitlines():
            if line.strip():
                log.append(logp, line.strip())
        shutil.rmtree(tmp, ignore_errors=True)
        return 0

    engine = env.get("TTS_ENGINE") or "kokoro"
    save = lastreply / f"{sid}.pcm"
    text_file = Path(tmp) / "text.txt"

    # Belt-and-braces against speaking one reply twice. The real cause of the
    # 2026-09-05 double-playback was a leftover second player, not a double
    # hook fire — but a cheap guard here costs nothing.
    digest = state.text_hash(text_file.read_bytes())
    marker = state.spoken_marker(sid, home)
    age = _duplicate_age(marker, digest)
    if age is not None and age < DUPLICATE_WINDOW:
        log.append(logp, f"duplicate reply suppressed ({age}s since the same text)")
        shutil.rmtree(tmp, ignore_errors=True)
        return 0
    marker.write_text(digest + "\n", encoding="utf-8")

    _housekeeping(lastreply, time.time())

    if engine == "kokoro":
        _ensure_engine(paths.port(env), logp, env, home)

    # Keep the text, not just the audio. The audio file is only written when a
    # reply plays to the end, and a reply cut short — by shush, by "again", or
    # by the next reply's own hook — is killed before that step, so it was
    # never on disk at all. `replay` re-speaks from this file, so "again" is
    # always the latest reply, whole, even mid-sentence. (Found live, 2026-09-07.)
    staged = lastreply / f".{sid}.txt.tmp"
    shutil.copyfile(text_file, staged)
    os.replace(staged, lastreply / f"{sid}.txt")

    # The ElevenLabs key stays in <tmp>/key — never on the command line, where
    # every `ps` on the machine would show it.
    pid = procs.spawn_detached(
        procs.talkback_argv("play", engine, tmp, str(save), str(logp)),
        env=dict(env),
    )
    log.append(logp, f"speaking {chars} chars via {engine} (pid {pid})")
    return 0
