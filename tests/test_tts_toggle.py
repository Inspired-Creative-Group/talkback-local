"""hooks/tts_toggle.sh — the UserPromptSubmit hook that turns a handful of
exact phrases into voice commands and lets every other prompt through.

Contract under test: a matching phrase exits 2 (Claude never sees it) and the
side effect lands — the per-session flag, a ``shush quiet``, or a ``replay``
of this session; anything else exits 0 with no flag, no calls and no output.

``bin/replay`` is replaced with a recording fake here: the real one spawns a
player and is covered by its own tests.
"""

import json
import sys

import pytest
from conftest import hook_payload

SID = "abcdef12-3456-7890-abcd-ef1234567890"

_FAKE_REPLAY = """\
import shlex, sys, time
with open({log!r}, "a") as f:
    f.write(f"{{time.time()}} replay {{shlex.join(sys.argv[1:])}}\\n")
sys.exit({code})
"""


def _install_fake_replay(sandbox, code=0):
    """Overwrite the sandbox's bin/replay with a fake that logs its args to the
    calls log (same line format as the harness shims) and exits ``code``."""
    impl = sandbox.home.parent / "fake_replay_impl.py"
    impl.write_text(_FAKE_REPLAY.format(log=str(sandbox.calls), code=code))
    target = sandbox.bin / "replay"
    target.write_text(f'#!/bin/bash\nexec "{sys.executable}" "{impl}" "$@"\n')
    target.chmod(0o755)


@pytest.fixture
def fake_replay(sandbox):
    _install_fake_replay(sandbox, code=0)
    return sandbox


@pytest.fixture
def failing_replay(sandbox):
    _install_fake_replay(sandbox, code=1)
    return sandbox


def _toggle(sandbox, payload):
    return sandbox.run([sandbox.hooks / "tts_toggle.sh"], stdin=payload)


def _log_lines(sandbox):
    return sandbox.log.read_text().splitlines() if sandbox.log.exists() else []


# --------------------------------------------------------------------------
# tts on
# --------------------------------------------------------------------------
def test_tts_on_arms_the_session(fake_replay):
    sb = fake_replay
    r = _toggle(sb, hook_payload(SID, prompt="tts on"))
    assert r.returncode == 2
    assert r.stderr.strip() == "voice on"
    assert (sb.recording / SID).exists(), "flag must carry the full session id"
    on_lines = [line for line in _log_lines(sb) if "TTS ON" in line]
    assert len(on_lines) == 1
    assert SID[:8] in on_lines[0]
    assert sb.read_calls() == [], "arming must not shush or replay anything"


@pytest.mark.parametrize("prompt", ["TTS ON", " tts on ", "tts on.", "TTS on!"])
def test_tts_on_tolerates_case_whitespace_and_punctuation(fake_replay, prompt):
    sb = fake_replay
    r = _toggle(sb, hook_payload(SID, prompt=prompt))
    assert r.returncode == 2
    assert r.stderr.strip() == "voice on"
    assert (sb.recording / SID).exists()


def test_tts_on_is_idempotent(fake_replay):
    sb = fake_replay
    sb.arm(SID)
    r = _toggle(sb, hook_payload(SID, prompt="tts on"))
    assert r.returncode == 2
    assert (sb.recording / SID).exists()


# --------------------------------------------------------------------------
# tts off
# --------------------------------------------------------------------------
def test_tts_off_disarms_and_shushes(fake_replay):
    sb = fake_replay
    sb.arm(SID)
    r = _toggle(sb, hook_payload(SID, prompt="tts off"))
    assert r.returncode == 2
    assert r.stderr.strip() == "voice off"
    assert not (sb.recording / SID).exists()
    assert sb.calls_to("shush") == [["quiet"]]
    assert sb.calls_to("replay") == []
    assert any("TTS OFF" in line and SID[:8] in line for line in _log_lines(sb))


def test_tts_off_only_disarms_its_own_session(fake_replay):
    sb = fake_replay
    sb.arm(SID)
    sb.arm("other-session")
    r = _toggle(sb, hook_payload(SID, prompt="TTS off."))
    assert r.returncode == 2
    assert not (sb.recording / SID).exists()
    assert (sb.recording / "other-session").exists()


# --------------------------------------------------------------------------
# shush / stop / quiet
# --------------------------------------------------------------------------
@pytest.mark.parametrize("prompt", ["shush", "stop", "quiet", "be quiet"])
def test_stop_words_shush_but_keep_the_session_armed(fake_replay, prompt):
    sb = fake_replay
    sb.arm(SID)
    r = _toggle(sb, hook_payload(SID, prompt=prompt))
    assert r.returncode == 2
    assert r.stderr.strip() == "quiet"
    assert sb.calls_to("shush") == [["quiet"]]
    assert sb.calls_to("replay") == []
    assert (sb.recording / SID).exists(), "shush stops playback, it does not turn the voice off"
    assert any("SHUSH" in line and SID[:8] in line for line in _log_lines(sb))


# --------------------------------------------------------------------------
# again / replay
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "prompt", ["again", "replay", "repeat", "say that again", "repeat that", "one more time"]
)
def test_replay_words_replay_this_session(fake_replay, prompt):
    sb = fake_replay
    r = _toggle(sb, hook_payload(SID, prompt=prompt))
    assert r.returncode == 2
    assert r.stderr.strip() == "replaying"
    assert sb.calls_to("replay") == [[SID]], "replay must get the full session id"
    assert sb.calls_to("shush") == [], "the hook leaves stopping the current reply to replay itself"
    assert any("REPLAY" in line and SID[:8] in line for line in _log_lines(sb))


def test_replay_with_nothing_recorded_says_so(failing_replay):
    sb = failing_replay
    r = _toggle(sb, hook_payload(SID, prompt="again"))
    assert r.returncode == 2
    assert r.stderr.strip() == "nothing recorded yet"
    assert sb.calls_to("replay") == [[SID]]
    assert not any("REPLAY" in line for line in _log_lines(sb))


# --------------------------------------------------------------------------
# everything else passes through
# --------------------------------------------------------------------------
@pytest.mark.parametrize("prompt", ["please turn tts on", "tts on now", "hello"])
def test_ordinary_prompts_pass_through_untouched(fake_replay, prompt):
    sb = fake_replay
    r = _toggle(sb, hook_payload(SID, prompt=prompt))
    assert r.returncode == 0
    assert r.stderr == ""
    assert r.stdout == "", "exit 0 stdout becomes context for Claude; it must stay empty"
    assert not (sb.recording / SID).exists()
    assert sb.read_calls() == []
    assert not sb.log.exists()


def test_payload_without_prompt_is_ignored(fake_replay):
    sb = fake_replay
    r = _toggle(sb, hook_payload(SID))
    assert r.returncode == 0
    assert r.stderr == ""
    assert r.stdout == ""
    assert not (sb.recording / SID).exists()
    assert sb.read_calls() == []


# --------------------------------------------------------------------------
# session id fallback
# --------------------------------------------------------------------------
def test_missing_session_id_uses_unknown(fake_replay):
    sb = fake_replay
    r = _toggle(sb, json.dumps({"prompt": "tts on"}))
    assert r.returncode == 2
    assert r.stderr.strip() == "voice on"
    assert (sb.recording / "unknown").exists()
    assert any("TTS ON" in line and "unknown" in line for line in _log_lines(sb))
