"""The UserPromptSubmit hook (``talkback toggle``): what ``hooks/tts_toggle.sh``
did. A handful of exact phrases become voice commands — the flag for THIS
session only, a shush, a replay — and every other prompt passes through
untouched, so talking *about* the command never trips it.

A handled phrase: one side effect, one log line, one word on stderr, exit 2
(Claude never sees the prompt). Anything else: exit 0, nothing written.
"""

import contextlib
import io
import json
import os
import sys

from talkback import log, paths, recmode, state

PHRASES = {
    "on": {"tts on"},
    "shush": {"shush", "stop", "quiet", "be quiet"},
    "replay": {"replay", "again", "repeat", "say that again", "repeat that", "one more time"},
    "off": {"tts off"},
}

_PUNCT = ".,!?;:"


def normalise(prompt: str) -> str:
    """lower(); delete every character in ``.,!?;:``; collapse whitespace —
    the ``tr | tr | xargs`` pipeline minus xargs's quote stripping."""
    s = prompt.lower()
    s = "".join(ch for ch in s if ch not in _PUNCT)
    return " ".join(s.split())


def classify(prompt: str) -> "str | None":
    """``"on" | "shush" | "replay" | "off" | None``."""
    p = normalise(prompt)
    for kind, phrases in PHRASES.items():
        if p in phrases:
            return kind
    return None


def _default_replay(home):
    def _replay(sid: str) -> int:
        from talkback import replay

        # bin/replay was run with its output discarded; its stdout ("nothing
        # recorded yet") must not become context for Claude.
        with contextlib.redirect_stdout(io.StringIO()):
            return replay.run(sid, os.environ, home)

    return _replay


def _default_shush(home):
    def _shush() -> None:
        from talkback import shush

        shush.shush(home=home, quiet=True)

    return _shush


def run(stdin_text: str, home=None, *, replay_fn=None, shush_fn=None) -> int:
    """2 on a handled phrase (one word on stderr, one log line), 0 otherwise.
    Invalid JSON, a non-object, or no prompt → 0, silent."""
    try:
        payload = json.loads(stdin_text)
    except (TypeError, ValueError):
        return 0
    if not isinstance(payload, dict):
        return 0
    sid = str(payload.get("session_id") or "unknown")
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt:
        return 0
    kind = classify(prompt)
    if kind is None:
        return 0

    logp = paths.speak_log(home)
    short = sid[:8]
    replay_fn = replay_fn or _default_replay(home)
    shush_fn = shush_fn or _default_shush(home)

    if kind == "on":
        recmode.prune(home)  # drop flags for sessions that have gone quiet
        state.arm(sid, home)
        log.append(logp, f"TTS ON  session {short}")
        print("voice on", file=sys.stderr)
    elif kind == "shush":
        shush_fn()
        log.append(logp, f"SHUSH session {short}")
        print("quiet", file=sys.stderr)
    elif kind == "replay":
        if replay_fn(sid) == 0:
            log.append(logp, f"REPLAY session {short}")
            print("replaying", file=sys.stderr)
        else:
            print("nothing recorded yet", file=sys.stderr)
    else:  # off
        state.disarm(sid, home)
        shush_fn()
        log.append(logp, f"TTS OFF session {short} + shushed")
        print("voice off", file=sys.stderr)
    return 2
