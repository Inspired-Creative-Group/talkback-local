"""The player: a 24 kHz **stereo** int16 stream with the mono voice duplicated
into both channels (the mono-in-one-ear bug must never return), 0.25 s slices
with the stop flag polled between them, the pid file held while playing, and
an engine error body that is never sent to the speaker.

The pure half drives ``talkback.player`` with an injected stream factory; the
sandbox half watches the real detached worker through the fake sounddevice."""

import os
import struct
import subprocess
import sys
import time

import pytest
from conftest import NON_SILENT_PCM

from talkback import paths, play, player, state

pure = pytest.mark.pure
STEREO = ["24000", "2", "int16"]


# --------------------------------------------------------------------------
# a stream double that records what the player hands it
# --------------------------------------------------------------------------
class FakeStream:
    def __init__(self, samplerate=None, blocksize=None, device=None, channels=None, dtype=None, **kw):
        self.kwargs = {"samplerate": samplerate, "channels": channels, "dtype": dtype}
        self.blocksize, self.device, self.extra = blocksize, device, kw
        self.writes = []
        self.started = self.closed = self.aborted = False
        self.on_write = None

    def start(self):
        self.started = True

    def write(self, data):
        assert self.started and not self.closed
        self.writes.append(bytes(data))
        if self.on_write:
            self.on_write(len(self.writes))

    def abort(self):
        self.aborted = True

    def stop(self):
        pass

    def close(self):
        self.closed = True


class Factory:
    """Records every stream it makes."""

    def __init__(self, on_write=None):
        self.streams = []
        self.on_write = on_write

    def __call__(self, **kwargs):
        s = FakeStream(**kwargs)
        s.on_write = self.on_write
        self.streams.append(s)
        return s


def _mono(samples):
    return b"".join(struct.pack("<h", s) for s in samples)


# --------------------------------------------------------------------------
# pure: the stereo conversion, the slices, the stream contract, the stop flag
# --------------------------------------------------------------------------
@pure
def test_to_stereo_duplicates_each_sample_into_both_channels():
    mono = _mono([1, -2, 32767])
    out = player.to_stereo(mono)
    assert len(out) == 2 * len(mono)
    frames = struct.unpack("<6h", out) if sys.byteorder == "little" else struct.unpack(">6h", out)
    left, right = frames[0::2], frames[1::2]
    assert left == (1, -2, 32767)
    assert right == left
    # a trailing odd byte is dropped, not turned into a sample
    assert player.to_stereo(mono + b"\x7f") == out
    assert player.to_stereo(b"") == b""


@pure
def test_slices_are_a_quarter_second_and_cover_every_byte():
    assert player.SLICE_FRAMES == 6000  # 0.25 s at 24 kHz
    assert player.RATE == 24000 and player.CHANNELS == 2 and player.DTYPE == "int16"
    one_slice = player.SLICE_FRAMES * 2  # bytes
    mono = bytes(range(256)) * 100  # 25600 bytes: two full slices and a remainder
    pieces = list(player.slices(mono))
    assert [len(p) for p in pieces] == [one_slice, one_slice, 25600 - 2 * one_slice]
    assert b"".join(pieces) == mono
    assert list(player.slices(b"")) == []
    assert [len(p) for p in player.slices(b"x" * 10, frames=2)] == [4, 4, 2]


@pure
def test_play_bytes_opens_a_24k_stereo_int16_stream(tmp_path):
    factory = Factory()
    stopped = player.play_bytes(NON_SILENT_PCM, tmp_path, factory=factory)

    assert stopped is False
    assert len(factory.streams) == 1
    s = factory.streams[0]
    assert s.kwargs == {"samplerate": 24000, "channels": 2, "dtype": "int16"}
    assert s.blocksize is None and s.device is None and s.extra == {}
    assert s.started and s.closed
    assert sum(len(w) for w in s.writes) == 2 * len(NON_SILENT_PCM)
    assert b"".join(s.writes) == player.to_stereo(NON_SILENT_PCM)
    # zero-length input opens no stream at all
    assert player.play_bytes(b"", tmp_path, factory=factory) is False
    assert len(factory.streams) == 1


@pure
def test_play_bytes_stops_between_slices_when_the_stop_flag_appears(tmp_path):
    four_slices = bytes(4 * player.SLICE_FRAMES * 2)
    factory = Factory()

    assert player.play_bytes(four_slices, tmp_path, factory=factory) is False
    assert len(factory.streams[0].writes) == 4

    def raise_flag_on_first_write(n):
        if n == 1:
            state.raise_stop(tmp_path)

    factory = Factory(on_write=raise_flag_on_first_write)
    assert not state.stop_requested(tmp_path)
    assert player.play_bytes(four_slices, tmp_path, factory=factory) is True
    s = factory.streams[0]
    assert len(s.writes) <= 2
    assert s.aborted and s.closed
    assert state.stop_requested(tmp_path)  # the flag is the worker's to clear, not the player's


@pure
def test_play_file_plays_the_recording_through_the_same_stream(tmp_path):
    pcm = tmp_path / "r.pcm"
    pcm.write_bytes(NON_SILENT_PCM)
    factory = Factory()
    assert player.play_file(pcm, tmp_path, factory=factory) is False
    assert factory.streams[0].kwargs == {"samplerate": 24000, "channels": 2, "dtype": "int16"}
    assert b"".join(factory.streams[0].writes) == player.to_stereo(NON_SILENT_PCM)


@pure
def test_hold_pid_writes_this_process_and_clears_only_its_own_entry(tmp_path):
    pidfile = paths.player_pid(tmp_path)
    with player.hold_pid(tmp_path):
        assert state.read_pid(pidfile) == os.getpid()
    assert not pidfile.exists()
    # a newer player that took the file over is left alone
    with player.hold_pid(tmp_path):
        state.write_pid(pidfile, 424242)
    assert state.read_pid(pidfile) == 424242


@pure
def test_tts_tempo_is_ignored_with_one_log_line(tmp_path, monkeypatch):
    work = tmp_path / "work"
    work.mkdir()
    (work / "text.txt").write_text("One short sentence.")
    save = paths.lastreply_dir(tmp_path) / "sid1.pcm"
    log = paths.speak_log(tmp_path)
    played = []
    monkeypatch.setattr(play.engine_client, "synth", lambda port, text, timeout=120.0: NON_SILENT_PCM)
    monkeypatch.setattr(play.player, "play_bytes", lambda data, home=None, **kw: played.append(data) or False)

    rc = play.reply("kokoro", work, save, log, {"TTS_TEMPO": "1.25", "KOKORO_PORT": "8999"}, tmp_path)

    assert rc == 0
    assert played == [NON_SILENT_PCM]
    assert save.read_bytes() == NON_SILENT_PCM
    lines = log.read_text().splitlines()
    tempo = [ln for ln in lines if "TTS_TEMPO" in ln]
    assert len(tempo) == 1
    assert tempo[0].endswith(" " + play.TEMPO_LINE)
    assert "TTS_TEMPO is ignored: playback speed is set at synthesis (KOKORO_SPEED / TTS_SPEED)" in tempo[0]
    assert not work.exists()
    assert not paths.player_pid(tmp_path).exists()

    # unset: no such line
    work.mkdir()
    (work / "text.txt").write_text("Another short sentence.")
    play.reply("kokoro", work, save, log, {"KOKORO_PORT": "8999"}, tmp_path)
    assert len([ln for ln in log.read_text().splitlines() if "TTS_TEMPO" in ln]) == 1


@pure
def test_error_body_detection_keys_on_the_first_byte():
    assert play.looks_like_error_body(b'{"detail": "x"}')
    assert play.looks_like_error_body(b"<html>")
    assert not play.looks_like_error_body(NON_SILENT_PCM)
    assert not play.looks_like_error_body(b"")


# --------------------------------------------------------------------------
# sandbox: the real detached worker seen through the fake sounddevice
# --------------------------------------------------------------------------
def _workdir(tmp_path, text):
    work = tmp_path / "work"
    work.mkdir()
    (work / "text.txt").write_text(text)
    return work


def test_detached_player_opens_a_24k_stereo_int16_stream(sandbox, fake_engine, tmp_path):
    """The plan's stereo test: the stream the worker opens has two channels."""
    work = _workdir(tmp_path, "One short sentence.")
    save = sandbox.lastreply / "sid1.pcm"

    r = sandbox.run([sandbox.hooks / "play_reply.sh", "kokoro", work, save, sandbox.log])

    assert r.returncode == 0, r.stderr
    assert sandbox.plays()[0] == STEREO
    assert sandbox.plays()[0][1] == "2", "the stream must be opened stereo or the voice sits in one ear"
    assert sandbox.writes()[0] == 2 * len(fake_engine.pcm)
    assert save.read_bytes() == fake_engine.pcm


def test_player_writes_its_pid_file_while_playing_and_removes_it_after(sandbox, tmp_path):
    pcm = tmp_path / "r.pcm"
    pcm.write_bytes(NON_SILENT_PCM)
    assert not sandbox.player_pid.exists()
    env = dict(sandbox.env, FAKE_PLAYER_SLEEP="1")

    p = subprocess.Popen(
        sandbox.talkback("play", "--file", pcm, "--log", sandbox.log),
        env=env,
        cwd=str(sandbox.repo),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert sandbox.wait_for(lambda: sandbox.plays())
        assert sandbox.player_pid.exists()
        assert state.read_pid(sandbox.player_pid) == p.pid
        assert sandbox.playing()
        assert sandbox.calls_to("play-done") == []
        p.wait(timeout=10)
    finally:
        if p.poll() is None:
            p.wait(timeout=10)
    assert p.returncode == 0
    assert not sandbox.player_pid.exists()
    assert sandbox.plays() == [STEREO]
    assert sandbox.writes() == [2 * len(NON_SILENT_PCM)]
    assert not sandbox.log.exists(), "play --file writes nothing to the log"


def test_error_body_chunk_is_not_played(sandbox, fake_engine, tmp_path):
    fake_engine.fail_with = b'{"detail": "model not loaded"}'
    work = _workdir(tmp_path, "One short sentence.")
    save = sandbox.lastreply / "sid1.pcm"

    r = sandbox.run([sandbox.hooks / "play_reply.sh", "kokoro", work, save, sandbox.log])

    assert r.returncode == 0, r.stderr
    assert sandbox.plays() == []
    assert sandbox.writes() == []
    log = sandbox.log.read_text()
    assert "TTS FAILED [kokoro]: model not loaded" in log
    assert not save.exists()
    assert not save.with_name("sid1.pcm.part").exists()
    assert not work.exists()
    assert not sandbox.player_pid.exists()


def test_stop_flag_between_slices_ends_a_long_recording_early(sandbox, tmp_path):
    """A stop that lands mid-chunk is honoured at the next 0.25 s slice, not
    only at the next chunk (change 9)."""
    pcm = tmp_path / "long.pcm"
    pcm.write_bytes(bytes(8 * player.SLICE_FRAMES * 2))  # eight slices, two seconds of silence
    env = dict(sandbox.env, FAKE_PLAYER_TOUCH_STOP="1", FAKE_PLAYER_SLEEP="0.05")
    t0 = time.time()

    r = sandbox.run(sandbox.talkback("play", "--file", pcm, "--log", sandbox.log), env=env)

    assert r.returncode == 0, r.stderr
    assert sandbox.plays() == [STEREO]
    assert 1 <= len(sandbox.writes()) <= 2
    assert time.time() - t0 < 5
    assert sandbox.stopflag.exists()
