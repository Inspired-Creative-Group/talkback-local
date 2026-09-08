"""``talkback play``: the detached reply worker (what ``hooks/play_reply.sh``
did) — chunk, fetch-ahead, play, verify, publish. Owner: player."""

from pathlib import Path


def looks_like_error_body(data: bytes) -> bool:
    """First byte is ``{`` or ``<``."""
    raise NotImplementedError("talkback.play.looks_like_error_body — player implementer")


def reply(engine: str, workdir: Path, save: Path, log: Path, env, home=None) -> int:
    """The four-argument form; always 0. Flow in the Stage 2 spec §2 ``play``."""
    raise NotImplementedError("talkback.play.reply — player implementer")


def announce_file(path: Path, log: Path, home=None) -> int:
    """``play --file <pcm> --log <logfile>``: play a recording; nothing is
    written to the log on this path."""
    raise NotImplementedError("talkback.play.announce_file — player implementer")
