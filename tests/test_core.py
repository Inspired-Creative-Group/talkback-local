"""The frozen core modules: paths, log, state, procs and engine_client."""

import os
import re
import subprocess
import sys
import time

import pytest

from talkback import engine_client, log, paths, procs, state

pure = pytest.mark.pure


@pure
def test_every_path_hangs_off_the_given_home(tmp_path):
    h = tmp_path
    auto = h / ".claude" / "automation"
    assert paths.automation(h) == auto
    assert paths.engine_dir(h) == auto / "kokoro"
    assert paths.hooks_dir(h) == auto / "notifications"
    assert paths.recording_dir(h) == auto / "recording"
    assert paths.lastreply_dir(h) == auto / "lastreply"
    assert paths.speak_log(h) == auto / "notifications" / "speak.log"
    assert paths.server_log(h) == auto / "kokoro" / "server.log"
    assert paths.requests_log(h) == auto / "kokoro" / "requests.log"
    assert paths.stop_flag(h) == auto / ".tts-stop"
    assert paths.player_pid(h) == auto / ".player.pid"
    assert paths.server_pid(h) == auto / "kokoro" / "server.pid"
    assert paths.projects_dir(h) == h / ".claude" / "projects"
    vp = paths.venv_python(h)
    assert vp.parts[-4:-2] == ("kokoro", ".venv")
    assert vp.name in ("python", "python.exe")
    assert paths.venv_pythonw(h).name == "pythonw.exe"


@pure
def test_home_follows_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert paths.home() == tmp_path
    assert paths.stop_flag() == tmp_path / ".claude" / "automation" / ".tts-stop"


@pure
def test_model_paths_come_from_the_environment_or_the_engine_dir(monkeypatch, tmp_path):
    for k in ("TALKBACK_VOICE", "TALKBACK_ONNX_MODEL", "TALKBACK_ONNX_VOICES"):
        monkeypatch.delenv(k, raising=False)
    eng = tmp_path / ".claude" / "automation" / "kokoro"
    assert paths.voice_path(tmp_path) == eng / "icg_voice.pt"
    assert paths.onnx_model(tmp_path) == eng / "kokoro-v1.0.onnx"
    assert paths.onnx_voices(tmp_path) == eng / "voices-v1.0.bin"
    monkeypatch.setenv("TALKBACK_VOICE", str(tmp_path / "v.pt"))
    monkeypatch.setenv("TALKBACK_ONNX_MODEL", str(tmp_path / "m.onnx"))
    monkeypatch.setenv("TALKBACK_ONNX_VOICES", str(tmp_path / "v.bin"))
    assert paths.voice_path(tmp_path) == tmp_path / "v.pt"
    assert paths.onnx_model(tmp_path) == tmp_path / "m.onnx"
    assert paths.onnx_voices(tmp_path) == tmp_path / "v.bin"


@pure
def test_port_is_kokoro_port_or_8910():
    assert paths.port({}) == 8910
    assert paths.port({"KOKORO_PORT": ""}) == 8910
    assert paths.port({"KOKORO_PORT": "8931"}) == 8931
    assert paths.port({"KOKORO_PORT": "nope"}) == 8910


@pure
def test_log_lines_are_timestamped_like_date_F_T(tmp_path):
    p = tmp_path / "deep" / "speak.log"
    log.append(p, "hello there")
    log.append(p, "second")
    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} hello there", lines[0])
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", log.ts())
    # a path that cannot be written never raises
    log.append(p / "not-a-dir" / "x.log", "silent")


@pure
def test_armed_flags_stop_flag_and_marker(tmp_path):
    h = tmp_path
    assert state.armed_sessions(h) == []
    assert not state.is_armed("s1", h)
    state.arm("s1", h)
    state.arm("s2", h)
    assert state.is_armed("s1", h)
    assert state.armed_sessions(h) == ["s1", "s2"]
    state.disarm("s1", h)
    state.disarm("s1", h)  # missing is fine
    assert state.armed_sessions(h) == ["s2"]
    assert not state.stop_requested(h)
    state.raise_stop(h)
    assert state.stop_requested(h)
    assert paths.stop_flag(h).exists()
    state.clear_stop(h)
    state.clear_stop(h)
    assert not state.stop_requested(h)
    assert state.spoken_marker("abc", h) == h / ".claude" / "automation" / "lastreply" / ".spoken-abc"


@pure
def test_text_hash_is_the_first_16_hex_of_sha1():
    # shasum -a 1 of "hello\n" is f572d396fae9206628714fb2ce00f72e94f2258f
    assert state.text_hash(b"hello\n") == "f572d396fae92066"
    assert len(state.text_hash(b"")) == 16


@pure
def test_pid_file_round_trip(tmp_path):
    p = tmp_path / "deep" / ".player.pid"
    assert state.read_pid(p) is None
    state.write_pid(p, 4321)
    assert p.read_text(encoding="ascii") == "4321\n"
    assert state.read_pid(p) == 4321
    assert not p.with_name(p.name + ".tmp").exists()
    p.write_text("garbage", encoding="ascii")
    assert state.read_pid(p) is None
    state.clear_pid(p)
    state.clear_pid(p)
    assert not p.exists()


@pure
def test_talkback_argv_uses_the_running_interpreter():
    assert procs.talkback_argv("play", "--file", "x") == [sys.executable, "-m", "talkback", "play", "--file", "x"]


@pure
def test_alive_and_terminate_on_a_real_child():
    assert procs.alive(os.getpid())
    assert not procs.alive(2**22 + 1)
    assert not procs.alive(0)
    assert not procs.alive(-1)
    assert not procs.terminate(2**22 + 1)
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        assert procs.alive(p.pid)
        assert procs.terminate(p.pid)
        p.wait(timeout=5)
        assert not procs.alive(p.pid)  # reaped, not a zombie
    finally:
        if p.poll() is None:
            p.kill()
            p.wait()


def test_spawn_detached_returns_at_once_and_the_child_outlives_the_call(tmp_path):
    marker = tmp_path / "done"
    code = f"import time, pathlib; time.sleep(0.3); pathlib.Path({str(marker)!r}).touch()"
    t0 = time.monotonic()
    pid = procs.spawn_detached([sys.executable, "-c", code])
    assert time.monotonic() - t0 < 0.25
    assert procs.alive(pid)
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.exists()
    deadline = time.monotonic() + 5
    while procs.alive(pid) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not procs.alive(pid)


def test_spawn_logged_appends_the_child_output(tmp_path):
    logf = tmp_path / "kokoro" / "server.log"
    p = procs.spawn_logged([sys.executable, "-c", "import sys; print('out'); print('err', file=sys.stderr)"], logf)
    assert p.wait(timeout=10) == 0
    assert set(logf.read_text().split()) == {"out", "err"}
    p2 = procs.spawn_logged([sys.executable, "-c", "print('again')"], logf)
    p2.wait(timeout=10)
    assert "again" in logf.read_text()
    assert "out" in logf.read_text()


def test_engine_client_up_and_synth_against_the_fake_engine(sandbox, fake_engine):
    assert engine_client.up(fake_engine.port)
    assert not engine_client.up(int(sandbox.env["KOKORO_PORT"]) + 60)  # nothing listens there
    assert engine_client.synth(fake_engine.port, "hello there") == fake_engine.pcm
    assert fake_engine.requests[-1]["body"] == "hello there"
    fake_engine.fail_with = b'{"error": "quota"}'
    assert engine_client.synth(fake_engine.port, "x") == b'{"error": "quota"}'
    fake_engine.fail_with = None
    assert engine_client.synth(fake_engine.port, "   ") == b""  # the 400 body, not None
    assert engine_client.synth(int(sandbox.env["KOKORO_PORT"]) + 60, "x") is None
    fake_engine.delay = 2.0
    assert engine_client.synth(fake_engine.port, "slow", timeout=0.3) is None
    fake_engine.delay = 0.0
