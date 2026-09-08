"""The only code that touches the audio device: a stereo ``sounddevice``
stream, mono duplicated into both channels, stop-flag polling between
0.25 s slices, and the player pid file. ``sounddevice`` is imported lazily
inside :func:`open_stream` — nothing at module import. Owner: player."""

from pathlib import Path

RATE, CHANNELS, DTYPE, SLICE_FRAMES = 24000, 2, "int16", 6000  # 0.25 s per slice


def to_stereo(mono: bytes) -> bytes:
    """``array('h')`` slice assignment: out[0::2] = out[1::2] = mono samples; a
    trailing odd byte is dropped; byteswap when ``sys.byteorder != "little"``."""
    raise NotImplementedError("talkback.player.to_stereo — player implementer")


def slices(mono: bytes, frames: int = SLICE_FRAMES):
    """Consecutive mono byte ranges of ``frames * 2`` bytes."""
    raise NotImplementedError("talkback.player.slices — player implementer")


def open_stream(factory=None):
    """``factory`` or ``sounddevice.RawOutputStream``, called with
    ``samplerate=24000, channels=2, dtype="int16"`` — the stereo contract."""
    raise NotImplementedError("talkback.player.open_stream — player implementer")


def play_bytes(mono: bytes, home=None, *, factory=None) -> bool:
    """One stream per call; writes ``to_stereo(slice)`` per slice and checks
    ``stop_requested(home)`` between slices. True if it stopped early.
    Zero-length input opens no stream."""
    raise NotImplementedError("talkback.player.play_bytes — player implementer")


def play_file(path: Path, home=None, *, factory=None) -> bool:
    raise NotImplementedError("talkback.player.play_file — player implementer")


def hold_pid(home=None):
    """Context manager: ``write_pid(player_pid)`` with ``os.getpid()``; cleared
    on exit (finally + atexit)."""
    raise NotImplementedError("talkback.player.hold_pid — player implementer")
