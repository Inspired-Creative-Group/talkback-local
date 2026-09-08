"""Talkback's on-disk state: armed-session flags, the stop flag, the spoken
marker and pid files. Everything under ``paths``; ``home`` as in ``paths``."""

import hashlib
import os
from pathlib import Path

from talkback import paths


# -- armed sessions ----------------------------------------------------------
def is_armed(sid: str, home=None) -> bool:
    return (paths.recording_dir(home) / sid).is_file()


def arm(sid: str, home=None) -> None:
    d = paths.recording_dir(home)
    d.mkdir(parents=True, exist_ok=True)
    (d / sid).touch()


def disarm(sid: str, home=None) -> None:
    try:
        (paths.recording_dir(home) / sid).unlink()
    except FileNotFoundError:
        pass


def armed_sessions(home=None) -> "list[str]":
    d = paths.recording_dir(home)
    if not d.is_dir():
        return []
    return sorted(p.name for p in d.iterdir() if p.is_file())


# -- stop flag ---------------------------------------------------------------
def raise_stop(home=None) -> None:
    flag = paths.stop_flag(home)
    flag.parent.mkdir(parents=True, exist_ok=True)
    flag.touch()


def clear_stop(home=None) -> None:
    try:
        paths.stop_flag(home).unlink()
    except FileNotFoundError:
        pass


def stop_requested(home=None) -> bool:
    return paths.stop_flag(home).exists()


# -- duplicate guard ---------------------------------------------------------
def spoken_marker(sid: str, home=None) -> Path:
    return paths.lastreply_dir(home) / f".spoken-{sid}"


def text_hash(data: bytes) -> str:
    """The first 16 hex chars of sha1 — what ``shasum -a 1 | cut -c1-16`` gave."""
    return hashlib.sha1(data).hexdigest()[:16]  # not security; a short content id


# -- pid files ---------------------------------------------------------------
def write_pid(path: Path, pid: int) -> None:
    """Atomic: written to a sibling temp file, then ``os.replace``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(f"{pid}\n", encoding="ascii")
    os.replace(tmp, path)


def read_pid(path: Path) -> "int | None":
    try:
        return int(Path(path).read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return None


def clear_pid(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
