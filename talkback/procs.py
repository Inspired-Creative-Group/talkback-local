"""Processes: how the hook spawns the detached player and the server, and how
``shush`` / ``server stop`` find out whether a pid is still alive and end it.
Windows never gets ``os.kill(pid, 0)`` — there it is TerminateProcess."""

import os
import signal
import subprocess
import sys
from pathlib import Path

_WINDOWS = sys.platform == "win32"


def talkback_argv(*args: str) -> "list[str]":
    """``[sys.executable, "-m", "talkback", *args]`` — the interpreter that is
    running is the one the package is installed in."""
    return [sys.executable, "-m", "talkback", *args]


def _detached_kwargs():
    kw = {
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if _WINDOWS:
        kw["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
    return kw


def spawn_detached(argv, cwd=None, env=None) -> int:
    """Start ``argv`` with no terminal, no inherited stdio and its own session /
    process group; return its pid without waiting."""
    p = subprocess.Popen(  # argv is built by this package, never from user text
        [str(a) for a in argv],
        cwd=cwd,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **_detached_kwargs(),
    )
    return p.pid


def spawn_logged(argv, log: Path, env=None) -> subprocess.Popen:
    """Like :func:`spawn_detached`, with stdout and stderr appended to ``log``
    (the server start). Returns the Popen so the caller can poll it."""
    log = Path(log)
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("ab") as f:
        return subprocess.Popen(
            [str(a) for a in argv],
            env=env,
            stdout=f,
            stderr=subprocess.STDOUT,
            **_detached_kwargs(),
        )


def _alive_windows(pid: int) -> bool:
    import ctypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    STILL_ACTIVE = 259
    k32 = ctypes.windll.kernel32
    handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not k32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        k32.CloseHandle(handle)


def alive(pid: int) -> bool:
    """Is there a live process with this pid? A finished child of *this*
    process is reaped first, so a zombie never reads as alive."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if _WINDOWS:
        return _alive_windows(pid)
    try:
        done, _ = os.waitpid(pid, os.WNOHANG)
        if done == pid:
            return False
    except ChildProcessError:
        pass
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def terminate(pid: int) -> bool:
    """SIGTERM (TerminateProcess on Windows). True if a signal was delivered,
    False if the process was already gone."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True
