"""``talkback replay [sid]``: what ``bin/replay`` did. Owner: session."""

from pathlib import Path


def select(lastreply: Path, sid: "str | None", engine_up: bool) -> "tuple[str, str, Path] | None":
    """``("text", sid, <sid>.txt)`` when a non-empty text exists (newest by
    mtime when sid is None) and the engine is up; else ``("file", sid,
    <sid>.pcm)`` when a recording exists; else None. ``.mp3`` is ignored."""
    raise NotImplementedError("talkback.replay.select — session implementer")


def run(sid: "str | None", env, home=None) -> int:
    """0 when a player was spawned; 1 and ``nothing recorded yet`` on stdout."""
    raise NotImplementedError("talkback.replay.run — session implementer")
