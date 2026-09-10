"""The only code that touches the audio device.

A ``sounddevice`` raw output stream at 24 kHz, **two channels**, int16, with
the engine's mono samples duplicated into both — the stream is opened stereo
on purpose: a mono stream on macOS lands the voice in the left ear (the mpv
bug this project already fixed once). Audio is written in 0.25 s slices and
the stop flag is polled between them, so ``shush`` lands within a quarter of a
second even if the process kill were refused. The player pid file is held for
the life of the process so ``shush`` can find it.

``sounddevice`` is imported lazily inside :func:`open_stream` — nothing at
module import, so the hook process never pays for it. Owner: player.
"""

import array
import atexit
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from talkback import paths, state

RATE, CHANNELS, DTYPE, SLICE_FRAMES = 24000, 2, "int16", 6000  # 0.25 s per slice


def to_stereo(mono: bytes) -> bytes:
    """Duplicate every s16le mono sample into both channels: ``out[0::2] =
    out[1::2] = samples``. A trailing odd byte is dropped. The bytes come in
    little-endian (the engine's ``<i2``) and go out in the host's native order,
    which is what the stream wants; only a big-endian host needs the swap."""
    n = len(mono) - (len(mono) % 2)
    samples = array.array("h")
    samples.frombytes(bytes(mono[:n]))
    if sys.byteorder != "little":
        samples.byteswap()
    out = array.array("h", bytes(2 * n))  # zeroed, two samples per frame
    out[0::2] = samples
    out[1::2] = samples
    return out.tobytes()


def slices(mono: bytes, frames: int = SLICE_FRAMES):
    """Consecutive ranges of ``frames * 2`` bytes (one s16 sample per frame)."""
    step = int(frames) * 2
    for i in range(0, len(mono), step):
        yield mono[i : i + step]


def open_stream(factory=None):
    """``factory`` (a test double) or ``sounddevice.RawOutputStream``, called
    with ``samplerate=24000, channels=2, dtype="int16"`` — the stereo
    contract. Keyword arguments on purpose: the real positional order is
    (samplerate, blocksize, device, ...)."""
    if factory is None:
        import sounddevice  # the audio device: only the player process ever imports this

        factory = sounddevice.RawOutputStream
    return factory(samplerate=RATE, channels=CHANNELS, dtype=DTYPE)


def play_bytes(mono: bytes, home=None, *, factory=None) -> bool:
    """Play one buffer of s16le mono through one stream. Returns True when the
    stop flag appeared between slices and playback was cut short. Zero-length
    input opens no stream."""
    if not mono:
        return False
    stream = open_stream(factory)
    stopped = False
    stream.start()
    try:
        first = True
        for piece in slices(mono):
            if not first and state.stop_requested(home):
                stopped = True
                break
            first = False
            stream.write(to_stereo(piece))
        if stopped:
            stream.abort()  # drop what is still queued rather than play it out
    finally:
        stream.close()
    return stopped


def play_file(path: Path, home=None, *, factory=None) -> bool:
    """Play a saved ``.pcm`` recording; same return as :func:`play_bytes`."""
    return play_bytes(Path(path).read_bytes(), home, factory=factory)


@contextmanager
def hold_pid(home=None):
    """Write this process's pid to ``.player.pid`` for the duration of the
    block; cleared on the way out (and at interpreter exit) — but only while
    the file still names *this* process, so a newer player that has already
    taken the file over is never un-registered by an older one."""
    pidfile = paths.player_pid(home)
    me = os.getpid()
    state.write_pid(pidfile, me)

    def _clear():
        if state.read_pid(pidfile) == me:
            state.clear_pid(pidfile)

    atexit.register(_clear)
    try:
        yield pidfile
    finally:
        _clear()
        atexit.unregister(_clear)
