"""``talkback replay [sid]``: what ``bin/replay`` did — say a session's last
reply again. No API call, no cost.

Re-speaks from the saved text through the local engine, so it is always the
latest reply and always the whole thing: the audio file on disk only exists
for replies that played to the end, and a reply cut short by shush, by
"again", or by the next reply never got that far (demo bug, 2026-09-07). The
recording is the fallback when there is no text or no local engine. Exit 1
when there is nothing to play, so callers can say so.
"""

import shutil
import tempfile
from pathlib import Path

from talkback import engine_client, paths, procs, shush, state


def _newest(files):
    files = [f for f in files if f.is_file() and not f.name.startswith(".")]
    if not files:
        return None
    return max(files, key=lambda f: f.stat().st_mtime)


def select(lastreply: Path, sid: "str | None", engine_up) -> "tuple[str, str, Path] | None":
    """``("text", sid, <sid>.txt)`` when a non-empty text exists (the newest
    ``*.txt`` by mtime when sid is None) and the engine is up; else
    ``("file", sid, <sid>.pcm)`` when a recording exists (the newest
    ``*.pcm`` when no session was named or found); else None. ``.mp3`` is
    ignored. ``engine_up`` may be a bool or a callable, consulted only when
    there is a text worth re-speaking."""
    lastreply = Path(lastreply)
    txt = None
    if sid:
        cand = lastreply / f"{sid}.txt"
        if cand.is_file():
            txt = cand
    else:
        txt = _newest(lastreply.glob("*.txt")) if lastreply.is_dir() else None
        if txt is not None:
            sid = txt.name[: -len(".txt")]

    if txt is not None and txt.stat().st_size > 0:
        up = engine_up() if callable(engine_up) else bool(engine_up)
        if up:
            return ("text", sid, txt)

    # Fallback: the last complete recording on disk.
    if sid:
        pcm = lastreply / f"{sid}.pcm"
        return ("file", sid, pcm) if pcm.is_file() else None
    pcm = _newest(lastreply.glob("*.pcm")) if lastreply.is_dir() else None
    if pcm is None:
        return None
    return ("file", pcm.name[: -len(".pcm")], pcm)


def run(sid: "str | None", env, home=None) -> int:
    """0 when a player was spawned; 1 and ``nothing recorded yet`` on stdout."""
    lastreply = paths.lastreply_dir(home)
    logp = paths.speak_log(home)
    port = paths.port(env)

    choice = select(lastreply, sid or None, lambda: engine_client.up(port))
    if choice is None:
        print("nothing recorded yet")
        return 1
    kind, sid, path = choice

    shush.shush(home=home, quiet=True)
    if kind == "text":
        tmp = tempfile.mkdtemp()
        shutil.copyfile(path, Path(tmp) / "text.txt")
        procs.spawn_detached(
            procs.talkback_argv("play", "kokoro", tmp, str(lastreply / f"{sid}.pcm"), str(logp)),
            env=dict(env),
        )
        return 0

    state.clear_stop(home)
    procs.spawn_detached(
        procs.talkback_argv("play", "--file", str(path), "--log", str(logp)),
        env=dict(env),
    )
    return 0
