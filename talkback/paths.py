"""Every path Talkback reads or writes, in one place.

All functions take ``home`` (a Path) and fall back to :func:`home` when it is
None. Nothing here reads ``TALKBACK_HOME`` — tests pass ``home=`` explicitly.
"""

import os
import sys
from pathlib import Path

DEFAULT_PORT = 8910


def home() -> Path:
    """``HOME`` on POSIX, ``USERPROFILE`` on Windows (the directory Git Bash's
    ``HOME`` points at too)."""
    return Path(os.path.expanduser("~"))


def _h(home_: "Path | None") -> Path:
    return home() if home_ is None else Path(home_)


def automation(home=None) -> Path:
    return _h(home) / ".claude" / "automation"


def engine_dir(home=None) -> Path:
    return automation(home) / "kokoro"


def hooks_dir(home=None) -> Path:
    return automation(home) / "notifications"


def recording_dir(home=None) -> Path:
    return automation(home) / "recording"


def lastreply_dir(home=None) -> Path:
    return automation(home) / "lastreply"


def speak_log(home=None) -> Path:
    return hooks_dir(home) / "speak.log"


def server_log(home=None) -> Path:
    return engine_dir(home) / "server.log"


def requests_log(home=None) -> Path:
    return engine_dir(home) / "requests.log"


def stop_flag(home=None) -> Path:
    return automation(home) / ".tts-stop"


def player_pid(home=None) -> Path:
    return automation(home) / ".player.pid"


def server_pid(home=None) -> Path:
    return engine_dir(home) / "server.pid"


def projects_dir(home=None) -> Path:
    return _h(home) / ".claude" / "projects"


def venv_python(home=None) -> Path:
    venv = engine_dir(home) / ".venv"
    if sys.platform == "win32":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python"


def venv_pythonw(home=None) -> Path:
    """Windows only: the console-less interpreter the Startup launcher runs."""
    return engine_dir(home) / ".venv" / "Scripts" / "pythonw.exe"


def voice_path(home=None) -> Path:
    return Path(os.environ.get("TALKBACK_VOICE") or engine_dir(home) / "icg_voice.pt")


def onnx_model(home=None) -> Path:
    return Path(os.environ.get("TALKBACK_ONNX_MODEL") or engine_dir(home) / "kokoro-v1.0.onnx")


def onnx_voices(home=None) -> Path:
    return Path(os.environ.get("TALKBACK_ONNX_VOICES") or engine_dir(home) / "voices-v1.0.bin")


def port(env=None) -> int:
    """``KOKORO_PORT`` or 8910. ``env`` defaults to ``os.environ``."""
    e = os.environ if env is None else env
    raw = e.get("KOKORO_PORT")
    try:
        return int(raw) if raw else DEFAULT_PORT
    except (TypeError, ValueError):
        return DEFAULT_PORT
