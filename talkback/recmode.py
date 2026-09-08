"""``talkback recmode [on [sid] | off | prune]``: what ``bin/recmode`` did.
Owner: session."""

from pathlib import Path


def transcript(sid: str, home=None) -> "Path | None":
    """The first ``projects_dir/**/<sid>.jsonl``."""
    raise NotImplementedError("talkback.recmode.transcript — session implementer")


def prune(home=None, dead_min=None) -> int:
    """Drop flags with no transcript, or one untouched for ``RECMODE_DEAD_MIN``
    (default 60) minutes. Returns the count dropped."""
    raise NotImplementedError("talkback.recmode.prune — session implementer")


def newest_session(home=None) -> "str | None":
    """The newest ``*.jsonl`` modified within 120 minutes, as a session id."""
    raise NotImplementedError("talkback.recmode.newest_session — session implementer")


def run(argv: "list[str]", env, home=None) -> int:
    """``on [sid] | off | prune | (none)`` — outputs verbatim from bin/recmode."""
    raise NotImplementedError("talkback.recmode.run — session implementer")
