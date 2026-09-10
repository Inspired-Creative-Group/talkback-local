"""``talkback server start|stop|status|restart`` — what ``bin/kokoro-server``
did with ``pgrep`` / ``pkill``, now through ``kokoro/server.pid``.

Every verb runs as a subprocess under the sandbox env, so ``KOKORO_PORT`` is
either the dead port or the in-process fake engine, and any ``talkback
server`` the ``start`` verb spawns dies at import on the sandbox's raising
``torch`` fake — no model is ever loaded and no server ever binds a port.
Nothing here touches 8910. "A live pid" is a sleeping ``python -c`` child of
pytest, never a real engine.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from talkback import paths, state
from talkback import server as tserver

ALREADY_UP = "a server process is already up (different port?) — not spawning another"


# --------------------------------------------------------------------------
# in-process: the paths a subprocess cannot reach (the sandbox's fake torch
# always kills the spawned server, so "started" and the 30 s timeout are
# driven here with the spawn and the port probe replaced)
# --------------------------------------------------------------------------
class _Child:
    def __init__(self, pid=4242, exit_code=None):
        self.pid, self.exit_code = pid, exit_code

    def poll(self):
        return self.exit_code


@pytest.fixture
def fast(monkeypatch):
    """No real waiting: the poll interval and the restart pause are zero."""
    monkeypatch.setattr(tserver, "START_INTERVAL", 0)
    monkeypatch.setattr(tserver, "RESTART_PAUSE", 0)


def _probe(monkeypatch, answers):
    """engine_client.up answering the given sequence, the last value repeated."""
    answers = list(answers)
    seen = []

    def up(port, timeout=1.0):
        seen.append(port)
        return answers.pop(0) if len(answers) > 1 else answers[0]

    monkeypatch.setattr(tserver.engine_client, "up", up)
    return seen


def _spawn(monkeypatch, child):
    spawned = []

    def spawn_logged(argv, log, env=None):
        spawned.append((list(argv), Path(log), env))
        return child

    monkeypatch.setattr(tserver.procs, "spawn_logged", spawn_logged)
    return spawned


@pytest.mark.pure
def test_start_reports_the_pid_once_the_port_answers(monkeypatch, tmp_path, capsys, fast):
    home = tmp_path / "home"
    probes = _probe(monkeypatch, [False, False, True])  # not up before the spawn, up on the second poll
    spawned = _spawn(monkeypatch, _Child(pid=4242))

    assert tserver.start({"KOKORO_PORT": "8934"}, home=home) == 0

    assert capsys.readouterr().out == "started (pid 4242)\n"
    assert probes == [8934, 8934, 8934]
    ((argv, log, env),) = spawned
    assert argv[1:] == ["-m", "talkback", "server"]  # the foreground server, on this interpreter
    assert argv[0] == sys.executable
    assert log == paths.server_log(home)
    assert env["KOKORO_PORT"] == "8934"  # the child binds the port the caller was asked about


@pytest.mark.pure
def test_start_gives_up_after_the_poll_budget_when_the_child_hangs(monkeypatch, tmp_path, capsys, fast):
    home = tmp_path / "home"
    probes = _probe(monkeypatch, [False])
    _spawn(monkeypatch, _Child(exit_code=None))  # never answers, never exits

    assert tserver.start({}, home=home) == 1

    assert capsys.readouterr().out == f"failed to start — see {paths.server_log(home)}\n"
    assert len(probes) == 1 + tserver.START_POLLS  # one probe before the spawn, then every poll


@pytest.mark.pure
def test_start_budget_is_the_30_seconds_kokoro_server_waited():
    assert (tserver.START_POLLS, tserver.START_INTERVAL) == (60, 0.5)
    assert tserver.RESTART_PAUSE == 1.0


@pytest.mark.pure
def test_start_notices_the_child_exit_before_the_next_poll(monkeypatch, tmp_path, capsys, fast):
    probes = _probe(monkeypatch, [False])
    _spawn(monkeypatch, _Child(exit_code=1))
    assert tserver.start({}, home=tmp_path / "home") == 1
    assert "failed to start" in capsys.readouterr().out
    assert len(probes) == 2  # the pre-spawn probe and one poll, not sixty


@pytest.mark.pure
def test_start_uses_the_default_port_when_kokoro_port_is_unset(monkeypatch, tmp_path, capsys, fast):
    probes = _probe(monkeypatch, [True])
    assert tserver.start({}, home=tmp_path / "home") == 0
    assert capsys.readouterr().out == "already running\n"
    assert probes == [8910]  # the probe only — nothing is spawned, nothing binds


@pytest.mark.pure
def test_restart_stops_quietly_then_starts(monkeypatch, tmp_path, capsys, fast):
    home = tmp_path / "home"
    state.write_pid(paths.server_pid(home), 777)
    order = []
    monkeypatch.setattr(tserver.procs, "alive", lambda pid: order.append(("alive", pid)) or True)
    monkeypatch.setattr(tserver.procs, "terminate", lambda pid: order.append(("terminate", pid)) or True)
    _probe(monkeypatch, [False, True])
    _spawn(monkeypatch, _Child(pid=4243))

    assert tserver.restart({"KOKORO_PORT": "8935"}, home=home) == 0

    assert order == [("alive", 777), ("terminate", 777)]
    assert capsys.readouterr().out == "started (pid 4243)\n"  # stop's own "stopped" is silent under restart
    assert not paths.server_pid(home).exists()


def _server(sandbox, *verbs, env=None):
    return sandbox.run(sandbox.talkback("server", *verbs), env=env)


@pytest.fixture
def sleeper():
    """A live pid that is not a Talkback server: a child of pytest sleeping in
    the background. Terminated at teardown if a test did not already do it."""
    p = subprocess.Popen(  # the interpreter running the tests, a fixed one-liner
        [sys.executable, "-c", "import time; time.sleep(60)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    yield p
    if p.poll() is None:
        p.terminate()
        p.wait(timeout=5)


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------
def test_status_reports_running_on_the_engine_port(sandbox, fake_engine):
    r = _server(sandbox, "status")
    assert r.returncode == 0, r.stderr
    assert r.stdout == f"running on {fake_engine.port}\n"
    assert r.stderr == ""


def test_status_with_a_dead_port_says_not_running(sandbox):
    r = _server(sandbox, "status")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "not running\n"
    assert not sandbox.server_pid.exists()


def test_status_ignores_the_pid_file_and_asks_the_port(sandbox, sleeper):
    """status is "does the port answer", as kokoro-server's curl was — a pid
    file alone does not make a server."""
    state.write_pid(sandbox.server_pid, sleeper.pid)
    r = _server(sandbox, "status")
    assert r.stdout == "not running\n"
    assert sandbox.server_pid.exists()  # status never touches it


# --------------------------------------------------------------------------
# start
# --------------------------------------------------------------------------
def test_start_when_the_port_answers_says_already_running(sandbox, fake_engine):
    r = _server(sandbox, "start")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "already running\n"
    # nothing was spawned: no pid file, no log, no request reached the engine
    assert not sandbox.server_pid.exists()
    assert not (sandbox.home / ".claude" / "automation" / "kokoro" / "server.log").exists()
    assert fake_engine.requests == []


def test_start_when_the_child_dies_reports_failure_at_once(sandbox):
    """The spawned ``talkback server`` hits the sandbox's raising ``torch``
    fake and exits; start notices the exit instead of waiting out 30 s."""
    log = sandbox.home / ".claude" / "automation" / "kokoro" / "server.log"
    t0 = time.monotonic()
    r = _server(sandbox, "start")
    elapsed = time.monotonic() - t0
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout == f"failed to start — see {log}\n"
    assert elapsed < 5, f"start took {elapsed:.1f}s to notice the child had died"
    text = log.read_text()
    assert "sandbox: the real model is never loaded" in text  # the child's traceback landed in server.log
    assert not sandbox.server_pid.exists()


def test_start_with_a_live_pid_and_a_silent_port_does_not_spawn_another(sandbox, sleeper):
    state.write_pid(sandbox.server_pid, sleeper.pid)
    r = _server(sandbox, "start")
    assert r.returncode == 0, r.stderr
    assert r.stdout == ALREADY_UP + "\n"
    assert not (sandbox.home / ".claude" / "automation" / "kokoro" / "server.log").exists()
    assert state.read_pid(sandbox.server_pid) == sleeper.pid
    assert sleeper.poll() is None  # start never kills anything


def test_start_with_a_stale_pid_file_spawns(sandbox):
    """A pid file left by a server that died (or a reboot) is not a server."""
    state.write_pid(sandbox.server_pid, 2**22 + 1)
    r = _server(sandbox, "start")
    assert r.returncode == 1
    assert r.stdout.startswith("failed to start — see ")
    assert (sandbox.home / ".claude" / "automation" / "kokoro" / "server.log").read_text() != ""


def test_kokoro_server_shim_with_no_argument_means_start(sandbox):
    """``bin/kokoro-server`` with no argument started the server; the shim
    keeps that (``"${@:-start}"``) while every other verb passes straight through."""
    log = sandbox.home / ".claude" / "automation" / "kokoro" / "server.log"
    r = sandbox.run([sandbox.bin / "kokoro-server"])
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout == f"failed to start — see {log}\n"
    r = sandbox.run([sandbox.bin / "kokoro-server", "status"])
    assert (r.returncode, r.stdout) == (0, "not running\n")


# --------------------------------------------------------------------------
# stop
# --------------------------------------------------------------------------
def test_stop_with_no_pid_file_says_not_running(sandbox):
    r = _server(sandbox, "stop")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "not running\n"
    assert not sandbox.server_pid.exists()


def test_stop_with_a_stale_pid_file_says_not_running_and_removes_it(sandbox):
    state.write_pid(sandbox.server_pid, 2**22 + 1)
    r = _server(sandbox, "stop")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "not running\n"
    assert not sandbox.server_pid.exists()


def test_stop_terminates_the_live_pid_and_reports_stopped(sandbox, sleeper):
    state.write_pid(sandbox.server_pid, sleeper.pid)
    r = _server(sandbox, "stop")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "stopped\n"
    assert sleeper.wait(timeout=2) != 0  # gone, by signal
    assert not sandbox.server_pid.exists()


def test_stop_never_reaches_a_pid_that_is_not_in_the_file(sandbox, sleeper):
    """Only the pid in ``server.pid`` is ever signalled — the pkill-by-name
    that killed whatever matched is gone."""
    r = _server(sandbox, "stop")
    assert r.stdout == "not running\n"
    assert sleeper.poll() is None


# --------------------------------------------------------------------------
# restart
# --------------------------------------------------------------------------
def test_restart_stops_the_live_pid_then_reports_the_start(sandbox, sleeper):
    state.write_pid(sandbox.server_pid, sleeper.pid)
    log = sandbox.home / ".claude" / "automation" / "kokoro" / "server.log"
    r = _server(sandbox, "restart")
    # the old process is gone and the (doomed, fake-torch) start is reported like `start`
    assert sleeper.wait(timeout=2) != 0
    assert r.returncode == 1, r.stdout + r.stderr
    assert r.stdout == f"failed to start — see {log}\n"  # stop's own line is silent, as `"$0" stop >/dev/null` was
    assert not sandbox.server_pid.exists()


def test_restart_with_the_engine_up_stops_nothing_it_does_not_own(sandbox, fake_engine):
    """No pid file: restart cannot stop the engine (not ours to kill without a
    pid) and start sees the port answering."""
    r = _server(sandbox, "restart")
    assert r.returncode == 0, r.stderr
    assert r.stdout == "already running\n"


# --------------------------------------------------------------------------
# the process a hook sees: env and cwd do not matter
# --------------------------------------------------------------------------
def test_verbs_work_from_any_cwd_with_only_home_and_port(sandbox, tmp_path):
    """The hooks run with Claude Code's cwd and a minimal env; the verbs must
    not depend on the checkout being the cwd."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    r = subprocess.run(
        sandbox.talkback("server", "status"),
        env=sandbox.env,
        cwd=str(elsewhere),
        text=True,
        capture_output=True,
        check=False,
    )
    assert (r.returncode, r.stdout) == (0, "not running\n"), r.stderr
    assert os.listdir(elsewhere) == []
