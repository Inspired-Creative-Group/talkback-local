"""Smoke tests for the harness itself: the sandbox never reaches the real
install, the fake engine answers, the fake player records instead of playing."""

import json
import os

from conftest import (
    NON_SILENT_PCM,
    assistant_turn,
    hook_payload,
    tool_turn,
    user_turn,
    write_transcript,
)


def test_env_is_clean(sandbox):
    env = sandbox.env
    assert not [k for k in env if k.startswith(("TTS_", "ELEVENLABS_"))]
    assert env["KOKORO_PORT"] != "8910"
    assert env["HOME"] == str(sandbox.home)
    assert env["PATH"].split(os.pathsep)[0] == str(sandbox.shims)
    assert (sandbox.hooks / "speak_last_reply.sh").exists()
    assert os.access(sandbox.hooks / "play_reply.sh", os.X_OK)
    assert (sandbox.bin / "replay").exists()
    assert sandbox.recording.is_dir()
    assert sandbox.lastreply.is_dir()


def test_python3_is_the_test_interpreter(sandbox):
    import sys

    r = sandbox.run(["python3", "-c", "import sys; print(sys.version_info[:2])"])
    assert r.returncode == 0
    assert r.stdout.strip() == str(sys.version_info[:2])


def test_fake_engine_answers_get_and_post(sandbox, fake_engine):
    assert fake_engine.port != 8910
    assert sandbox.env["KOKORO_PORT"] == str(fake_engine.port)
    r = sandbox.run(["curl", "-sf", "--max-time", "2", fake_engine.url])
    assert r.returncode == 0
    assert r.stdout == "ok"

    out = sandbox.home / "a.pcm"
    r = sandbox.run(["curl", "-sS", "-o", str(out), "-X", "POST", fake_engine.url, "--data-binary", "hello there"])
    assert r.returncode == 0
    assert out.read_bytes() == NON_SILENT_PCM
    assert len(NON_SILENT_PCM) == 4800
    assert any(b != 0 for b in NON_SILENT_PCM)
    assert len(fake_engine.requests) == 1
    req = fake_engine.requests[0]
    assert req["body"] == "hello there"
    assert req["t_end"] >= req["t_start"]


def test_fake_engine_failure_body(sandbox, fake_engine):
    fake_engine.fail_with = b'{"error": "quota"}'
    r = sandbox.run(["curl", "-sS", "-i", "-X", "POST", fake_engine.url, "--data-binary", "x"])
    assert r.returncode == 0
    assert "application/json" in r.stdout
    assert r.stdout.endswith('{"error": "quota"}')


def test_fake_engine_rejects_empty_body(sandbox, fake_engine):
    r = sandbox.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", "-X", "POST", fake_engine.url, "--data-binary", "  "])
    assert r.stdout == "400"


def test_fake_ffplay_records_and_never_plays(sandbox):
    r = sandbox.run(["ffplay", "-f", "s16le", "-ar", "24000", "-ch_layout", "mono", "-nodisp", "/nonexistent.pcm"])
    assert r.returncode == 0
    calls = sandbox.read_calls()
    assert [c[1] for c in calls] == ["ffplay", "ffplay-done"]
    assert calls[0][2] == ["-f", "s16le", "-ar", "24000", "-ch_layout", "mono", "-nodisp", "/nonexistent.pcm"]
    assert calls[1][0] - calls[0][0] >= 0.15
    which = sandbox.run(["/bin/bash", "-c", "command -v ffplay"])
    assert which.stdout.strip() == str(sandbox.shims / "ffplay")


def test_fake_ffplay_can_raise_the_stop_flag(sandbox):
    env = dict(sandbox.env, FAKE_FFPLAY_TOUCH_STOP="1", FAKE_FFPLAY_SLEEP="0")
    assert not sandbox.stopflag.exists()
    sandbox.run(["ffplay", "x.pcm"], env=env)
    assert sandbox.stopflag.exists()


def test_fake_shush_and_kokoro_server_only_log(sandbox):
    assert sandbox.run(["shush", "quiet"]).returncode == 0
    assert sandbox.run(["kokoro-server", "start"]).returncode == 0
    assert sandbox.stopflag.exists()
    assert sandbox.calls_to("shush") == [["quiet"]]
    assert sandbox.calls_to("kokoro-server") == [["start"]]
    assert sandbox.run(["osascript", "-e", 'display notification "x"']).returncode == 0
    assert sandbox.calls_to("osascript") == [["-e", 'display notification "x"']]


def test_transcript_helpers(tmp_path):
    p = write_transcript(
        tmp_path / "t.jsonl",
        [user_turn("hi"), tool_turn("Bash", command="ls"), "not json", assistant_turn(["a", {"type": "tool_use", "id": "1", "name": "x", "input": {}}, "b"])],
    )
    lines = p.read_text().splitlines()
    assert len(lines) == 4
    assert lines[2] == "not json"
    last = json.loads(lines[3])
    assert last["type"] == "assistant"
    assert [b["type"] for b in last["message"]["content"]] == ["text", "tool_use", "text"]
    assert json.loads(lines[1])["message"]["content"][0]["type"] == "tool_use"
    assert json.loads(hook_payload("sid", prompt="tts on")) == {"session_id": "sid", "prompt": "tts on"}


def test_arm_and_wait_for(sandbox):
    sandbox.arm("sid1")
    assert (sandbox.recording / "sid1").exists()
    assert sandbox.wait_for(lambda: True, timeout=1)
    assert not sandbox.wait_for(lambda: False, timeout=0.2)
    assert not sandbox.playing()
