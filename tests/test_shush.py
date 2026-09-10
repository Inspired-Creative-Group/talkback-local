"""``shush`` stops Talkback's own player — found through ``.player.pid``, never
a machine-wide pkill — and raises the stop flag; the Stop hook uses the same
path so two replies never talk over each other."""

import os
import subprocess

import pytest
from conftest import (
    NON_SILENT_PCM,
    assistant_turn,
    hook_payload,
    user_turn,
    write_transcript,
)

from talkback import procs, state

pure = pytest.mark.pure


@pure
def test_pid_file_round_trip_and_liveness(tmp_path):
    pidfile = tmp_path / ".player.pid"
    state.write_pid(pidfile, 12345)
    assert pidfile.read_text() == "12345\n"
    assert state.read_pid(pidfile) == 12345
    assert not list(tmp_path.glob("*.tmp"))
    state.clear_pid(pidfile)
    assert not pidfile.exists()
    assert state.read_pid(pidfile) is None
    state.clear_pid(pidfile)  # missing is fine
    pidfile.write_text("not a pid\n")
    assert state.read_pid(pidfile) is None

    assert procs.alive(os.getpid())
    assert not procs.alive(2**22 + 1)
    assert not procs.alive(0)
    assert not procs.alive(-1)


def _start_player(sandbox, tmp_path, sleep="5"):
    """A detached ``play --file`` that holds its stream open for ``sleep`` s."""
    pcm = tmp_path / "r.pcm"
    pcm.write_bytes(NON_SILENT_PCM)
    p = subprocess.Popen(
        sandbox.talkback("play", "--file", pcm, "--log", sandbox.log),
        env=dict(sandbox.env, FAKE_PLAYER_SLEEP=sleep),
        cwd=str(sandbox.repo),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert sandbox.wait_for(lambda: sandbox.plays() and sandbox.player_pid.exists())
    assert state.read_pid(sandbox.player_pid) == p.pid
    return p


def test_shush_kills_the_running_player_and_reports_shushed(sandbox, tmp_path):
    p = _start_player(sandbox, tmp_path)
    assert not sandbox.stopflag.exists()

    r = sandbox.run([sandbox.bin / "shush"])

    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "shushed"
    assert sandbox.wait_for(lambda: p.poll() is not None, timeout=1), "the player is still running 1 s after shush"
    assert sandbox.stopflag.exists()
    assert not sandbox.player_pid.exists()
    assert sandbox.wait_quiet(1)


def test_shush_with_nothing_playing_says_nothing_was_speaking(sandbox):
    r = sandbox.run([sandbox.bin / "shush"])
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "nothing was speaking"
    assert sandbox.stopflag.exists()
    assert not sandbox.player_pid.exists()

    # a stale pid file (a process long gone) is reported the same way and removed
    state.write_pid(sandbox.player_pid, 2**22 + 1)
    r = sandbox.run([sandbox.bin / "shush"])
    assert r.stdout.strip() == "nothing was speaking"
    assert not sandbox.player_pid.exists()
    assert sandbox.plays() == []


def test_shush_quiet_prints_nothing(sandbox, tmp_path):
    r = sandbox.run([sandbox.bin / "shush", "quiet"])
    assert r.returncode == 0
    assert r.stdout == "" and r.stderr == ""
    assert sandbox.stopflag.exists()

    p = _start_player(sandbox, tmp_path)
    r = sandbox.run([sandbox.bin / "shush", "quiet"])
    assert r.returncode == 0
    assert r.stdout == "" and r.stderr == ""
    assert sandbox.wait_for(lambda: p.poll() is not None, timeout=1)
    assert not sandbox.player_pid.exists()


def test_next_reply_kills_the_previous_player(sandbox, fake_engine, tmp_path):
    """The Stage 1 gap: two Stop hooks back to back; the second reply must
    not talk over the first."""
    sid = "sid1"
    sandbox.arm(sid)
    first = "The first reply is a slow one that takes a while to play."
    second = "The second reply arrives before the first has finished."
    tp = write_transcript(tmp_path / "t.jsonl", [user_turn("go"), assistant_turn(first)])

    r = sandbox.run(
        [sandbox.hooks / "speak_last_reply.sh"],
        stdin=hook_payload(sid, transcript_path=str(tp)),
        env=dict(sandbox.env, FAKE_PLAYER_SLEEP="5"),
    )
    assert r.returncode == 0, r.stderr
    assert sandbox.wait_for(lambda: sandbox.plays() and sandbox.player_pid.exists())
    first_pid = state.read_pid(sandbox.player_pid)
    assert first_pid and procs.alive(first_pid)

    write_transcript(tp, [user_turn("go"), assistant_turn(first), user_turn("more"), assistant_turn(second)])
    r = sandbox.run([sandbox.hooks / "speak_last_reply.sh"], stdin=hook_payload(sid, transcript_path=str(tp)))
    assert r.returncode == 0, r.stderr

    assert sandbox.wait_for(lambda: not procs.alive(first_pid), timeout=2), "the first player outlived the second hook"
    assert sandbox.wait_for(lambda: (sandbox.lastreply / f"{sid}.pcm").exists())
    assert sandbox.wait_quiet()
    log = sandbox.log.read_text()
    assert log.count("speaking ") == 2
    assert [q["body"] for q in fake_engine.requests] == [first, second]
    assert (sandbox.lastreply / f"{sid}.txt").read_text() == second
    assert (sandbox.lastreply / f"{sid}.pcm").read_bytes() == NON_SILENT_PCM
    assert len(sandbox.plays()) == 2
    assert not sandbox.player_pid.exists()
    assert not (sandbox.lastreply / f"{sid}.pcm.part").exists()
