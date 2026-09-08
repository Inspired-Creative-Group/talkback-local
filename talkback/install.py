"""What both installers run with a plain interpreter (``PYTHONPATH=<checkout>``),
stdlib only: the settings.json merge, template/shim rendering, the port verb.
Owner: installer."""

import sys
from pathlib import Path


def hook_command(venv_python: Path, sub: str) -> str:
    """``f'"{venv_python.as_posix()}" -m talkback {sub}'``."""
    raise NotImplementedError("talkback.install.hook_command — installer implementer")


def merge_settings(settings: dict, stop_cmd: str, prompt_cmd: str) -> "tuple[dict, list[str]]":
    raise NotImplementedError("talkback.install.merge_settings — installer implementer")


def render(template: str, **values) -> str:
    """``str.replace("__KEY__", value)`` for each value."""
    raise NotImplementedError("talkback.install.render — installer implementer")


def main(argv=None) -> int:
    """Verbs: ``port <n>`` | ``settings <venv py>`` | ``render <template> <out>
    KEY=VALUE...`` | ``shim <src> <dst> <venv py>``."""
    raise NotImplementedError("talkback.install.main — installer implementer")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
