"""bin/replay says a session's last reply again.

It prefers the saved text re-spoken through the local engine (so "again" is
always the whole latest reply), falls back to the last complete recording on
disk when there is no text or no engine, and exits 1 when there is nothing.

Everything runs in the sandbox: the real bin/replay and play_reply.sh, the
fake engine on its own port, and the ffplay/shush shims. replay backgrounds
the player and returns at once, so every test polls for the outcome.
"""

import os
import time

import pytest
from conftest import DEAD_PORT, NON_SILENT_PCM

SHORT = "This one sentence fits in a single chunk."
# Two sentences whose total is over chunk_text.py's 120-char first chunk, so
# the engine has to receive this reply in more than one piece.
LONG = (
    "The first sentence of this reply is long enough to fill most of the opening chunk on its own, more or less. "
    "The second sentence lands in the second chunk."
)
STALE = b"stale-audio" * 400


def _save_text(sandbox, sid, text, age=0):
    p = sandbox.lastreply / f"{sid}.txt"
    p.write_text(text)
    if age:
        t = time.time() - age
        os.utime(p, (t, t))
    return p


def _save_pcm(sandbox, sid, payload=STALE):
    p = sandbox.lastreply / f"{sid}.pcm"
    p.write_bytes(payload)
    return p


def _replay(sandbox, *args, env=None):
    return sandbox.run([sandbox.bin / "replay", *args], env=env)


def _log(sandbox):
    return sandbox.log.read_text() if sandbox.log.exists() else ""


def _wait_played(sandbox):
    """Block until the backgrounded play_reply.sh has run to completion."""
    assert sandbox.wait_for(lambda: "PLAY done" in _log(sandbox)), _log(sandbox)
    assert sandbox.wait_quiet()


def test_saved_text_is_respoken_through_the_engine(sandbox, fake_engine):
    _save_text(sandbox, "sid1", LONG)

    r = _replay(sandbox, "sid1")
    assert r.returncode == 0, r.stderr
    _wait_played(sandbox)

    assert sandbox.calls_to("shush") == [["quiet"]]
    bodies = [q["body"] for q in fake_engine.requests]
    assert len(bodies) >= 2
    assert " ".join(bodies) == LONG
    plays = sandbox.calls_to("ffplay")
    assert len(plays) == len(bodies)
    for args in plays:
        assert args[:4] == ["-f", "s16le", "-ar", "24000"]
    assert (sandbox.lastreply / "sid1.pcm").read_bytes() == NON_SILENT_PCM * len(bodies)
    # shush raised the stop flag; the re-speak must not be counted as stopped by it
    assert "stopped=0" in _log(sandbox)
    assert not sandbox.stopflag.exists()


def test_text_beats_a_stale_recording_when_the_engine_is_up(sandbox, fake_engine):
    _save_text(sandbox, "sid1", SHORT)
    stale = _save_pcm(sandbox, "sid1")

    r = _replay(sandbox, "sid1")
    assert r.returncode == 0, r.stderr
    _wait_played(sandbox)

    assert [q["body"] for q in fake_engine.requests] == [SHORT]
    assert str(stale) not in [a[-1] for a in sandbox.calls_to("ffplay")]
    assert stale.read_bytes() == NON_SILENT_PCM
    assert stale.read_bytes() != STALE


@pytest.mark.parametrize("engine_up", [True, False], ids=["engine-up", "engine-down"])
def test_recording_alone_plays_straight_from_disk(sandbox, request, engine_up):
    engine = request.getfixturevalue("fake_engine") if engine_up else None
    if not engine_up:
        assert sandbox.env["KOKORO_PORT"] == DEAD_PORT
    pcm = _save_pcm(sandbox, "sid1")

    r = _replay(sandbox, "sid1")
    assert r.returncode == 0, r.stderr
    assert sandbox.wait_for(lambda: sandbox.calls_to("ffplay-done"))

    plays = sandbox.calls_to("ffplay")
    assert len(plays) == 1
    assert plays[0][:4] == ["-f", "s16le", "-ar", "24000"]
    assert plays[0][-1] == str(pcm)
    assert sandbox.calls_to("shush") == [["quiet"]]
    if engine is not None:
        assert engine.requests == []


def test_text_without_an_engine_falls_back_to_the_recording(sandbox):
    assert sandbox.env["KOKORO_PORT"] == DEAD_PORT
    _save_text(sandbox, "sid1", SHORT)
    pcm = _save_pcm(sandbox, "sid1")

    r = _replay(sandbox, "sid1")
    assert r.returncode == 0, r.stderr
    assert sandbox.wait_for(lambda: sandbox.calls_to("ffplay-done"))

    assert [a[-1] for a in sandbox.calls_to("ffplay")] == [str(pcm)]
    # play_reply.sh logs "PLAY start" the moment it runs; no log means the
    # engine path was never taken
    assert not sandbox.log.exists()
    assert not sandbox.playing()


def test_text_without_an_engine_or_recording_reports_nothing(sandbox):
    assert sandbox.env["KOKORO_PORT"] == DEAD_PORT
    _save_text(sandbox, "sid1", SHORT)

    r = _replay(sandbox, "sid1")
    assert r.returncode == 1
    assert r.stdout.strip() == "nothing recorded yet"
    assert sandbox.calls_to("ffplay") == []


def test_no_argument_picks_the_most_recently_saved_text(sandbox, fake_engine):
    # Written first and last alphabetically, so only mtime points at it.
    _save_text(sandbox, "zz-latest", "This is the newer reply.")
    _save_text(sandbox, "aa-stale", "This is the older reply.", age=300)

    r = _replay(sandbox)
    assert r.returncode == 0, r.stderr
    _wait_played(sandbox)

    assert [q["body"] for q in fake_engine.requests] == ["This is the newer reply."]
    assert (sandbox.lastreply / "zz-latest.pcm").exists()
    assert not (sandbox.lastreply / "aa-stale.pcm").exists()


@pytest.mark.parametrize("args", [["ghost"], []], ids=["with-sid", "no-sid"])
def test_nothing_saved_exits_1(sandbox, args):
    r = _replay(sandbox, *args)
    assert r.returncode == 1
    assert r.stdout.strip() == "nothing recorded yet"
    assert sandbox.calls_to("ffplay") == []
    assert sandbox.calls_to("shush") == []


def test_fallback_clears_the_stop_flag_before_playing(sandbox):
    pcm = _save_pcm(sandbox, "sid1")
    sandbox.stopflag.touch()  # left behind by an earlier "shush"
    env = dict(sandbox.env, FAKE_FFPLAY_SLEEP="1")

    r = _replay(sandbox, "sid1", env=env)
    assert r.returncode == 0, r.stderr
    assert sandbox.wait_for(lambda: sandbox.calls_to("ffplay"))

    # playback has started and is still running; the flag is already gone
    assert sandbox.calls_to("ffplay-done") == []
    assert not sandbox.stopflag.exists()
    assert sandbox.calls_to("ffplay")[0][-1] == str(pcm)
    # shush (which raises the flag) ran, and replay lowered it again afterwards
    assert sandbox.calls_to("shush") == [["quiet"]]
    assert sandbox.wait_for(lambda: sandbox.calls_to("ffplay-done"), timeout=5)
