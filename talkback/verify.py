"""``talkback verify <file> <engine> <logfile>``: what ``hooks/verify_audio.sh``
did — is the file audio, and make a failure loud. Owner: player."""

from pathlib import Path


def detail_of(data: bytes) -> str:
    """The JSON message/detail/error walk, else the first 180 characters."""
    raise NotImplementedError("talkback.verify.detail_of — player implementer")


def verify(path: Path, engine: str, log: Path, env, home=None) -> bool:
    """True = audio (publish it), False = not (discard it)."""
    raise NotImplementedError("talkback.verify.verify — player implementer")


def notify(msg: str, title: str) -> None:
    """darwin with osascript on PATH: ``display notification``; elsewhere no-op."""
    raise NotImplementedError("talkback.verify.notify — player implementer")


def say_fail(msg: str, engine: str, log: Path, env, home=None) -> None:
    """Log line + notification + (engine != kokoro and the local engine up) →
    spoken announcement through a detached ``play --file``."""
    raise NotImplementedError("talkback.verify.say_fail — player implementer")
