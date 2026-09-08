"""hooks/speak_last_reply.sh — the Stop hook, end to end in the sandbox.

Fed a hook payload on stdin, with the fake engine answering on its own port
and the fake ffplay recording instead of playing. Pins what a user sees:
an unarmed session stays silent; an armed one saves the text before the
first sound, speaks once, and does not repeat itself within two minutes.
"""

import hashlib
import os
import time

import speak_last_reply
from conftest import (
    DEAD_PORT,
    NON_SILENT_PCM,
    assistant_turn,
    hook_payload,
    tool_turn,
    user_turn,
    write_transcript,
)

SID = "sid1"
ONE_LINER = "A short reply, one sentence long."
MARKDOWN = "**Bold** start, then `shush` it.\n\n- a bullet\n- another bullet"
FOUR_SENTENCES = (
    "The first sentence is here. The second sentence follows it closely. "
    "The third one is long enough to push the total past the first limit. "
    "And a fourth sentence to be safe."
)


def _speak(sandbox, transcript, sid=SID, env=None):
    payload = hook_payload(sid, transcript_path=str(transcript))
    return sandbox.run([sandbox.hooks / "speak_last_reply.sh"], stdin=payload, env=env)


def _log(sandbox):
    return sandbox.log.read_text() if sandbox.log.exists() else ""


def _played_to_the_end(sandbox, sid=SID):
    """Wait for the background player to publish the audio and exit."""
    pcm = sandbox.lastreply / f"{sid}.pcm"
    assert sandbox.wait_for(pcm.exists), _log(sandbox)
    assert sandbox.wait_quiet(), _log(sandbox)
    return pcm


def test_unarmed_session_plays_nothing(sandbox, fake_engine, tmp_path):
    tp = write_transcript(tmp_path / "t.jsonl", [assistant_turn(ONE_LINER)])
    r = _speak(sandbox, tp)
    assert r.returncode == 0, r.stderr
    assert sandbox.wait_quiet()
    assert list(sandbox.lastreply.iterdir()) == []
    assert sandbox.read_calls() == []
    assert fake_engine.requests == []


def test_armed_session_saves_text_before_playback(sandbox, fake_engine, tmp_path):
    tp = write_transcript(tmp_path / "t.jsonl", [user_turn("hi"), assistant_turn(MARKDOWN)])
    sandbox.arm(SID)
    r = _speak(sandbox, tp)
    assert r.returncode == 0, r.stderr

    assert sandbox.wait_for(lambda: sandbox.calls_to("ffplay")), _log(sandbox)
    txt = sandbox.lastreply / f"{SID}.txt"
    assert txt.exists()
    saved = txt.read_text()
    assert saved == speak_last_reply.rewrite(MARKDOWN)
    assert "**" not in saved and "`" not in saved
    assert not (sandbox.lastreply / f".{SID}.txt.tmp").exists()

    calls = sandbox.read_calls()
    names = [n for _, n, _ in calls]
    first_ffplay = names.index("ffplay")
    assert names[0] == "shush" and calls[0][2] == ["quiet"]
    assert names.index("shush") < first_ffplay
    assert txt.stat().st_mtime <= calls[first_ffplay][0]
    assert sandbox.calls_to("kokoro-server") == []

    assert f"speaking {len(saved)} chars via kokoro" in _log(sandbox)
    _played_to_the_end(sandbox)


def test_saved_pcm_is_the_engine_audio_in_order(sandbox, fake_engine, tmp_path):
    tp = write_transcript(tmp_path / "t.jsonl", [assistant_turn(FOUR_SENTENCES)])
    sandbox.arm(SID)
    assert _speak(sandbox, tp).returncode == 0

    pcm = _played_to_the_end(sandbox)
    bodies = [q["body"] for q in fake_engine.requests]
    assert len(bodies) >= 2, "a four-sentence reply should be spoken in more than one chunk"
    assert " ".join(bodies) == (sandbox.lastreply / f"{SID}.txt").read_text()
    assert pcm.read_bytes() == NON_SILENT_PCM * len(bodies)
    assert len(sandbox.calls_to("ffplay")) == len(bodies)
    assert not (sandbox.lastreply / f"{SID}.pcm.part").exists()
    assert "PLAY done" in _log(sandbox)


def test_duplicate_reply_within_seconds_is_suppressed(sandbox, fake_engine, tmp_path):
    tp = write_transcript(tmp_path / "t.jsonl", [assistant_turn(ONE_LINER)])
    sandbox.arm(SID)
    assert _speak(sandbox, tp).returncode == 0
    _played_to_the_end(sandbox)
    n_requests = len(fake_engine.requests)
    n_ffplay = len(sandbox.calls_to("ffplay"))

    r = _speak(sandbox, tp)
    assert r.returncode == 0, r.stderr
    assert sandbox.wait_quiet()
    log = _log(sandbox)
    assert "duplicate reply suppressed" in log
    assert log.count("speaking ") == 1
    assert len(fake_engine.requests) == n_requests
    assert len(sandbox.calls_to("ffplay")) == n_ffplay

    text = (sandbox.lastreply / f"{SID}.txt").read_bytes()
    marker = (sandbox.lastreply / f".spoken-{SID}").read_text().strip()
    assert marker == hashlib.sha1(text).hexdigest()[:16]
    assert len(marker) == 16


def test_changed_reply_plays_again(sandbox, fake_engine, tmp_path):
    tp = write_transcript(tmp_path / "t.jsonl", [assistant_turn(ONE_LINER)])
    sandbox.arm(SID)
    assert _speak(sandbox, tp).returncode == 0
    _played_to_the_end(sandbox)
    n_requests = len(fake_engine.requests)
    n_ffplay = len(sandbox.calls_to("ffplay"))

    second = "A different reply this time."
    write_transcript(tp, [assistant_turn(ONE_LINER), user_turn("more"), assistant_turn(second)])
    assert _speak(sandbox, tp).returncode == 0
    assert sandbox.wait_for(lambda: len(sandbox.calls_to("ffplay")) > n_ffplay), _log(sandbox)
    assert sandbox.wait_quiet()

    log = _log(sandbox)
    assert "duplicate reply suppressed" not in log
    assert log.count("speaking ") == 2
    assert len(fake_engine.requests) > n_requests
    assert fake_engine.requests[-1]["body"] == second
    assert (sandbox.lastreply / f"{SID}.txt").read_text() == second
    marker = (sandbox.lastreply / f".spoken-{SID}").read_text().strip()
    assert marker == hashlib.sha1(second.encode()).hexdigest()[:16]


def test_same_reply_after_two_minutes_plays_again(sandbox, fake_engine, tmp_path):
    tp = write_transcript(tmp_path / "t.jsonl", [assistant_turn(ONE_LINER)])
    sandbox.arm(SID)
    assert _speak(sandbox, tp).returncode == 0
    _played_to_the_end(sandbox)
    n_requests = len(fake_engine.requests)

    old = time.time() - 130
    os.utime(sandbox.lastreply / f".spoken-{SID}", (old, old))
    assert _speak(sandbox, tp).returncode == 0
    assert sandbox.wait_for(lambda: len(fake_engine.requests) > n_requests), _log(sandbox)
    assert sandbox.wait_quiet()
    log = _log(sandbox)
    assert "duplicate reply suppressed" not in log
    assert log.count("speaking ") == 2


def test_missing_transcript_path_logs_no_transcript(sandbox, fake_engine):
    sandbox.arm(SID)
    r = sandbox.run([sandbox.hooks / "speak_last_reply.sh"], stdin=hook_payload(SID))
    assert r.returncode == 0, r.stderr
    assert sandbox.wait_quiet()
    assert "no transcript" in _log(sandbox)
    assert list(sandbox.lastreply.iterdir()) == []
    assert sandbox.read_calls() == []
    assert fake_engine.requests == []


def test_nonexistent_transcript_file_logs_no_transcript(sandbox, fake_engine, tmp_path):
    sandbox.arm(SID)
    r = _speak(sandbox, tmp_path / "gone.jsonl")
    assert r.returncode == 0, r.stderr
    assert sandbox.wait_quiet()
    assert "no transcript" in _log(sandbox)
    assert list(sandbox.lastreply.iterdir()) == []
    assert sandbox.read_calls() == []
    assert fake_engine.requests == []


def test_tool_only_transcript_plays_nothing(sandbox, fake_engine, tmp_path):
    tp = write_transcript(
        tmp_path / "t.jsonl",
        [user_turn("run it"), tool_turn("Bash", command="ls"), tool_turn("Read", file_path="/x")],
    )
    sandbox.arm(SID)
    r = _speak(sandbox, tp)
    assert r.returncode == 0, r.stderr
    assert sandbox.wait_quiet()
    assert sandbox.calls_to("ffplay") == []
    assert fake_engine.requests == []
    assert list(sandbox.lastreply.iterdir()) == []
    assert "speaking " not in _log(sandbox)


def test_engine_down_asks_kokoro_server_to_start(sandbox, tmp_path):
    assert sandbox.env["KOKORO_PORT"] == DEAD_PORT
    tp = write_transcript(tmp_path / "t.jsonl", [assistant_turn(ONE_LINER)])
    sandbox.arm(SID)
    r = _speak(sandbox, tp)
    assert r.returncode == 0, r.stderr
    assert sandbox.wait_for(lambda: sandbox.calls_to("kokoro-server")), _log(sandbox)
    assert sandbox.calls_to("kokoro-server") == [["start"]]
    assert (sandbox.lastreply / f"{SID}.txt").read_text() == ONE_LINER

    assert sandbox.wait_quiet(), _log(sandbox)
    assert sandbox.calls_to("ffplay") == []
    assert not (sandbox.lastreply / f"{SID}.pcm").exists()
    assert not (sandbox.lastreply / f"{SID}.pcm.part").exists()
