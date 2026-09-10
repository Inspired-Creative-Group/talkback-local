"""What both installers run with a plain interpreter (``PYTHONPATH=<checkout>``),
stdlib only: the settings.json merge, template/shim rendering, the port verb.
Owner: installer.

Verbs (``python -m talkback.install <verb> ...``):

    port <n>                      stdout ``free`` | ``talkback`` | ``busy: <why>``;
                                  exit 0 / 0 / 1 (the refusal is also printed
                                  to stderr, so both installers share the text)
    settings <venv python>        merge the two hook commands into
                                  ~/.claude/settings.json; prints
                                  ``    added <event>`` / ``    replaced <event>``
    render <template> <out> KEY=VALUE...
                                  ``__KEY__`` -> value; refuses to leave a
                                  placeholder unrendered
    shim <src> <dst> <venv python>
                                  copy a shell shim with the literal
                                  ``${TALKBACK_PYTHON:-python3}`` replaced by
                                  the interpreter's absolute path; chmod +x
"""

import json
import os
import re
import stat
import sys
from pathlib import Path, PurePath

from talkback import paths

SHIM_LITERAL = "${TALKBACK_PYTHON:-python3}"
PLACEHOLDER = re.compile(r"__[A-Z][A-Z0-9_]*__")

# What marks an entry as ours, whichever release wrote it: the Stage 1 shell
# hooks by file name, the package by its module invocation.
TALKBACK_MARKERS = ("speak_last_reply.sh", "tts_toggle.sh", "-m talkback speak", "-m talkback toggle")
EVENTS = (("Stop", "speak"), ("UserPromptSubmit", "toggle"))


def hook_command(venv_python, sub: str) -> str:
    """``"<venv python, forward slashes>" -m talkback <sub>`` — the double
    quotes are the one thing bash, Git Bash and cmd.exe all read the same way,
    and the only way a ``C:/Users/First Last/...`` path survives."""
    p = venv_python.as_posix() if isinstance(venv_python, PurePath) else str(venv_python).replace("\\", "/")
    return f'"{p}" -m talkback {sub}'


def is_talkback_command(command) -> bool:
    return isinstance(command, str) and any(m in command for m in TALKBACK_MARKERS)


def merge_settings(settings: dict, stop_cmd: str, prompt_cmd: str) -> "tuple[dict, list[str]]":
    """Register the two hook commands, replacing any earlier Talkback entry in
    place. Per event: an entry whose command already equals the new one is
    kept; the first entry that carries an old Talkback command is rewritten
    where it stands (``replaced <event>``); any further Talkback entry is
    dropped, so there is never a second Stop hook; foreign entries and every
    other key of the file are untouched. No Talkback entry at all: appended
    to the first block (``added <event>``); an emptied list — what Claude
    Code leaves behind when every hook is removed — gets a block first."""
    if not isinstance(settings, dict):
        settings = {}
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        hooks = settings["hooks"] = {}
    messages = []
    for (event, _), cmd in zip(EVENTS, (stop_cmd, prompt_cmd)):
        blocks = hooks.get(event)
        if not isinstance(blocks, list):
            blocks = hooks[event] = []
        if not blocks:
            blocks.append({"matcher": "", "hooks": []})
        placed = False
        changed = False
        for block in blocks:
            if not isinstance(block, dict):
                continue
            entries = block.get("hooks")
            if not isinstance(entries, list):
                continue
            kept = []
            for entry in entries:
                command = entry.get("command") if isinstance(entry, dict) else None
                if command == cmd:
                    if placed:
                        changed = True  # a duplicate of ours: collapsed
                        continue
                    placed = True
                    kept.append(entry)
                elif is_talkback_command(command):
                    changed = True
                    if placed:
                        continue  # an extra Talkback entry: dropped
                    placed = True
                    kept.append(dict(entry, command=cmd))
                else:
                    kept.append(entry)
            block["hooks"] = kept
        if not placed:
            first = blocks[0]
            if not isinstance(first, dict):
                first = blocks[0] = {"matcher": "", "hooks": []}
            if not isinstance(first.get("hooks"), list):
                first["hooks"] = []
            first["hooks"].append({"type": "command", "command": cmd})
            messages.append(f"added {event}")
        elif changed:
            messages.append(f"replaced {event}")
    return settings, messages


def render(template: str, **values) -> str:
    """``str.replace("__KEY__", value)`` for each value, in the order given."""
    out = template
    for key, value in values.items():
        out = out.replace(f"__{key}__", str(value))
    return out


def unrendered(text: str) -> "list[str]":
    """Every ``__PLACEHOLDER__`` still present, in order of first appearance."""
    seen = []
    for m in PLACEHOLDER.findall(text):
        if m not in seen:
            seen.append(m)
    return seen


def shim_text(source: str, venv_python) -> str:
    """The installed copy of a shell shim: the literal
    ``${TALKBACK_PYTHON:-python3}`` becomes the interpreter's absolute path,
    so the hooks need nothing on PATH. A file without the literal is copied
    as it is."""
    return source.replace(SHIM_LITERAL, str(venv_python))


def _chmod_exec(path: Path) -> None:
    try:
        path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass  # Windows has no execute bit; the file is run through the interpreter anyway


def _write_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def settings_path(home=None) -> Path:
    base = paths.home() if home is None else Path(home)
    return base / ".claude" / "settings.json"


# -- verbs ------------------------------------------------------------------
def _port(args) -> int:
    from talkback import ports

    if len(args) != 1:
        print("usage: talkback.install port <n>", file=sys.stderr)
        return 2
    try:
        port = int(args[0])
    except ValueError:
        print(f"not a port number: {args[0]!r}", file=sys.stderr)
        return 2
    state = ports.check_port(port)
    if state == ports.BUSY:
        print(f"busy: {ports.explain(state, port)}")
        print(ports.explain(state, port), file=sys.stderr)
        return 1
    print(state)
    return 0


def _settings(args, home=None) -> int:
    if len(args) != 1:
        print("usage: talkback.install settings <venv python>", file=sys.stderr)
        return 2
    venv_python = Path(args[0])
    path = settings_path(home)
    settings = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8") or "{}")
        except ValueError as e:
            print(f"{path} is not valid JSON ({e}); fix it and re-run — nothing was changed", file=sys.stderr)
            return 1
    settings, messages = merge_settings(
        settings, hook_command(venv_python, "speak"), hook_command(venv_python, "toggle")
    )
    _write_atomic(path, json.dumps(settings, indent=2) + "\n")
    for m in messages:
        print(f"    {m}")
    return 0


def _render(args) -> int:
    if len(args) < 2:
        print("usage: talkback.install render <template> <out> KEY=VALUE...", file=sys.stderr)
        return 2
    template, out = Path(args[0]), Path(args[1])
    values = {}
    for pair in args[2:]:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            print(f"expected KEY=VALUE, got {pair!r}", file=sys.stderr)
            return 2
        values[key] = value
    text = render(template.read_text(encoding="utf-8"), **values)
    left = unrendered(text)
    if left:
        print(f"{template}: no value given for {', '.join(left)}", file=sys.stderr)
        return 1
    _write_atomic(out, text)
    return 0


def _shim(args) -> int:
    if len(args) != 3:
        print("usage: talkback.install shim <src> <dst> <venv python>", file=sys.stderr)
        return 2
    src, dst, venv_python = Path(args[0]), Path(args[1]), args[2]
    _write_atomic(dst, shim_text(src.read_text(encoding="utf-8"), venv_python))
    _chmod_exec(dst)
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    verb, rest = args[0], args[1:]
    if verb == "port":
        return _port(rest)
    if verb == "settings":
        return _settings(rest)
    if verb == "render":
        return _render(rest)
    if verb == "shim":
        return _shim(rest)
    print(f"unknown verb {verb!r}\n{__doc__}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
