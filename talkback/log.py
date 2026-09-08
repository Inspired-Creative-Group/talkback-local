"""The speak.log line format: ``YYYY-MM-DD HH:MM:SS <line>``, appended."""

import time
from pathlib import Path


def ts() -> str:
    """Same text as ``date '+%F %T'``; the long form because Windows strftime
    has no %F / %T."""
    return time.strftime("%Y-%m-%d %H:%M:%S")


def append(path: Path, line: str) -> None:
    """Append one timestamped line, creating the directory. Never raises: a log
    that cannot be written must not silence a reply."""
    try:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(f"{ts()} {line}\n")
    except OSError:
        pass
