"""Smoke tests for the harness itself: the sandbox never reaches the real
install, the fake engine answers, the fake sounddevice records instead of
playing, and nothing in the sandbox can pkill."""

import json
import os
import sys

import pytest
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
    # TALKBACK_PYTHON is set on purpose (the .sh shims exec it); nothing else
    # from the TALKBACK_ family may leak in from the pytest process.
    assert [k for k in env if k.startswith("TALKBACK_")] == ["TALKBACK_PYTHON"]
    assert env["TALKBACK_PYTHON"] == sys.executable
    assert env["KOKORO_PORT"] != "8910"
    assert env["HOME"] == str(sandbox.home)
    assert env["PATH"].split(os.pathsep)[0] == str(sandbox.shims)
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(sandbox.fakes)
    assert (sandbox.hooks / "speak_last_reply.sh").exists()
    assert os.access(sandbox.hooks / "play_reply.sh", os.X_OK)
    assert (sandbox.bin / "replay").exists()
    assert sandbox.recording.is_dir()
    assert sandbox.lastreply.is_dir()
    assert not sandbox.player_pid.exists()
    assert not sandbox.server_pid.exists()


def test_python3_is_the_test_interpreter(sandbox):
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


_STREAM = (
    "import sounddevice as sd, sys\n"
    "print(sd.__file__)\n"
    "s = sd.RawOutputStream(samplerate=24000, channels=2, dtype='int16')\n"
    "s.start()\n"
    "s.write(bytes({n}))\n"
    "s.close()\n"
)


def test_fake_sounddevice_records_and_never_plays(sandbox):
    r = sandbox.run(["python3", "-c", _STREAM.format(n=4800)])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().startswith(str(sandbox.fakes) + os.sep)
    calls = sandbox.read_calls()
    assert [c[1] for c in calls] == ["play", "write", "play-done"]
    assert calls[0][2] == ["24000", "2", "int16"]
    assert calls[1][2] == ["4800"]
    assert calls[2][0] - calls[0][0] >= 0.15
    assert sandbox.plays() == [["24000", "2", "int16"]]
    assert sandbox.writes() == [4800]
    assert not sandbox.stopflag.exists()


def test_fake_sounddevice_as_a_context_manager_logs_the_same(sandbox):
    src = (
        "import sounddevice as sd\n"
        "with sd.RawOutputStream(samplerate=24000, channels=2, dtype='int16') as s:\n"
        "    s.write(b'\\0' * 100); s.write(b'\\0' * 200)\n"
    )
    r = sandbox.run(["python3", "-c", src], env=dict(sandbox.env, FAKE_PLAYER_SLEEP="0"))
    assert r.returncode == 0, r.stderr
    assert [c[1] for c in sandbox.read_calls()] == ["play", "write", "write", "play-done"]
    assert sandbox.writes() == [100, 200]


def test_fake_sounddevice_refuses_a_write_before_start(sandbox):
    src = (
        "import sounddevice as sd\n"
        "s = sd.RawOutputStream(samplerate=24000, channels=2, dtype='int16')\n"
        "try:\n    s.write(b'x')\nexcept sd.PortAudioError:\n    print('refused')\n"
    )
    r = sandbox.run(["python3", "-c", src])
    assert r.stdout.strip() == "refused", r.stderr
    assert sandbox.read_calls() == []


def test_fake_player_can_raise_the_stop_flag(sandbox):
    env = dict(sandbox.env, FAKE_PLAYER_TOUCH_STOP="1", FAKE_PLAYER_SLEEP="0")
    assert not sandbox.stopflag.exists()
    r = sandbox.run(["python3", "-c", _STREAM.format(n=100)], env=env)
    assert r.returncode == 0, r.stderr
    assert sandbox.stopflag.exists()
    calls = sandbox.read_calls()
    assert calls[2][0] - calls[0][0] < 0.15


def test_fake_models_raise_at_import(sandbox):
    for mod in ("torch", "kokoro", "kokoro_onnx"):
        r = sandbox.run(["python3", "-c", f"import {mod}"])
        assert r.returncode != 0
        assert "sandbox: the real model is never loaded" in r.stderr, mod


def test_no_sandbox_bin_script_can_pkill(sandbox):
    # Whether a bin file is already a `-m talkback` shim or still the pkill
    # script (replaced by a stand-in), nothing in the sandbox's ~/bin pkills.
    names = sorted(p.name for p in sandbox.bin.iterdir())
    assert {"shush", "replay", "recmode", "kokoro-server"} <= set(names)
    for p in sandbox.bin.iterdir():
        assert "pkill" not in p.read_text(errors="ignore"), p.name
        assert os.access(p, os.X_OK), p.name


def test_fake_osascript_only_logs(sandbox):
    assert sandbox.run(["osascript", "-e", 'display notification "x"']).returncode == 0
    assert sandbox.calls_to("osascript") == [["-e", 'display notification "x"']]
    which = sandbox.run(["/bin/bash", "-c", "command -v osascript"])
    assert which.stdout.strip() == str(sandbox.shims / "osascript")


def test_talkback_is_importable_from_a_sandbox_subprocess(sandbox, repo):
    r = sandbox.run(["python3", "-c", "import talkback, sys; print(talkback.__file__)"])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(repo / "talkback" / "__init__.py")
    r = sandbox.run(sandbox.talkback())
    assert r.returncode == 2
    assert "usage: talkback" in r.stderr


@pytest.mark.pure
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
