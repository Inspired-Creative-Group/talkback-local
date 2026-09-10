"""``talkback recmode [on [sid] | off | prune]``: what ``bin/recmode`` did —
which Claude Code sessions are speaking.

    recmode            list armed sessions (prunes dead ones first)
    recmode off        silence ALL sessions (panic switch)
    recmode on [id]    arm a session; with no id, the most recently active one
    recmode prune      drop flags for sessions that have gone quiet
"""

import os
import time
from pathlib import Path

from talkback import paths, state

DEAD_MIN = 60  # quiet this long = the session is gone (RECMODE_DEAD_MIN)
RECENT_MIN = 120  # "on" with no id picks a transcript touched within this

_TRANSCRIPT = ".jsonl"


def _dead_min(env) -> int:
    raw = env.get("RECMODE_DEAD_MIN")
    try:
        return int(raw) if raw else DEAD_MIN
    except (TypeError, ValueError):
        return DEAD_MIN


def _flags(home) -> "list[Path]":
    """The armed-session flags, by name — dotfiles are not sessions."""
    d = paths.recording_dir(home)
    if not d.is_dir():
        return []
    return sorted((p for p in d.iterdir() if not p.name.startswith(".")), key=lambda p: p.name)


def transcript(sid: str, home=None) -> "Path | None":
    """The first ``projects_dir/**/<sid>.jsonl``."""
    proj = paths.projects_dir(home)
    if not proj.is_dir():
        return None
    for p in proj.rglob(f"{sid}{_TRANSCRIPT}"):
        if p.is_file():
            return p
    return None


def prune(home=None, dead_min=None) -> int:
    """Drop flags with no transcript, or one untouched for ``dead_min``
    minutes (``RECMODE_DEAD_MIN``, default 60). Returns the count dropped."""
    if dead_min is None:
        dead_min = _dead_min(os.environ)
    paths.recording_dir(home).mkdir(parents=True, exist_ok=True)
    now = time.time()
    n = 0
    for f in _flags(home):
        if not f.is_file():
            continue
        t = transcript(f.name, home)
        if t is None or now - t.stat().st_mtime >= dead_min * 60:
            try:
                f.unlink()
            except FileNotFoundError:
                pass
            n += 1
    return n


def newest_session(home=None) -> "str | None":
    """The newest ``*.jsonl`` modified within 120 minutes, as a session id."""
    proj = paths.projects_dir(home)
    if not proj.is_dir():
        return None
    now = time.time()
    best, best_t = None, None
    for p in proj.rglob(f"*{_TRANSCRIPT}"):
        try:
            if not p.is_file():
                continue
            t = p.stat().st_mtime
        except OSError:
            continue
        if now - t >= RECENT_MIN * 60:
            continue
        if best_t is None or t > best_t:
            best, best_t = p, t
    return None if best is None else best.name[: -len(_TRANSCRIPT)]


def _list(home, dead_min) -> int:
    d = prune(home, dead_min)
    flags = _flags(home)
    if d:
        print(f"(pruned {d} dead)")
    if not flags:
        print("OFF — no session is speaking")
        return 0
    print(f"{len(flags)} session(s) speaking:")
    now = int(time.time())
    for f in flags:
        t = transcript(f.name, home)
        project = t.parent.name if t is not None else ""
        age = f"{(now - int(t.stat().st_mtime)) // 60}m ago" if t is not None else "?"
        print(f"  {f.name[:8]}  {project or 'unknown'}  (active {age})")
    return 0


def run(argv: "list[str]", env, home=None) -> int:
    """``on [sid] | off | prune | (none)`` — outputs verbatim from bin/recmode."""
    dead_min = _dead_min(env)
    paths.recording_dir(home).mkdir(parents=True, exist_ok=True)
    verb = argv[0] if argv else ""

    if verb == "on":
        sid = (argv[1] if len(argv) > 1 else "") or newest_session(home)
        if not sid:
            print("no recent session — type 'TTS on' in the session you want")
            return 1
        prune(home, dead_min)
        state.arm(sid, home)
        print(f"speaking: {sid[:8]}")
        return 0

    if verb == "off":
        from talkback import shush

        flags = _flags(home)
        for f in flags:
            if f.is_file():
                try:
                    f.unlink()
                except FileNotFoundError:
                    pass
        shush.shush(home=home, quiet=True)
        print(f"silenced {len(flags)} session(s)")
        return 0

    if verb == "prune":
        print(f"pruned {prune(home, dead_min)} dead session(s)")
        return 0

    return _list(home, dead_min)
