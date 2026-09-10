"""hooks/tts_toggle.sh — the UserPromptSubmit hook that turns a handful of
exact phrases into voice commands and lets every other prompt through.

Contract under test: a matching phrase exits 2 (Claude never sees it) and the
side effect lands — the per-session flag, a shush, or a replay of this
session; anything else exits 0 with no flag, no calls and no output.

The hook is the shell shim over ``talkback toggle``; shush and replay run
in-process now, so their effects are asserted directly: the stop flag, the
engine's requests, the fake player's log.
"""

import json

import pytest
from conftest import hook_payload

SID = "abcdef12-3456-7890-abcd-ef1234567890"


def _toggle(sandbox, payload):
    return sandbox.run([sandbox.hooks / "tts_toggle.sh"], stdin=payload)


def _log_lines(sandbox):
    return sandbox.log.read_text().splitlines() if sandbox.log.exists() else []


# --------------------------------------------------------------------------
# tts on
# --------------------------------------------------------------------------
def test_tts_on_arms_the_session(sandbox):
    sb = sandbox
    r = _toggle(sb, hook_payload(SID, prompt="tts on"))
    assert r.returncode == 2
    assert r.stderr.strip() == "voice on"
    assert (sb.recording / SID).exists(), "flag must carry the full session id"
    on_lines = [line for line in _log_lines(sb) if "TTS ON" in line]
    assert len(on_lines) == 1
    assert SID[:8] in on_lines[0]
    assert sb.read_calls() == [], "arming must not shush or replay anything"


@pytest.mark.parametrize("prompt", ["TTS ON", " tts on ", "tts on.", "TTS on!"])
def test_tts_on_tolerates_case_whitespace_and_punctuation(sandbox, prompt):
    sb = sandbox
    r = _toggle(sb, hook_payload(SID, prompt=prompt))
    assert r.returncode == 2
    assert r.stderr.strip() == "voice on"
    assert (sb.recording / SID).exists()


def test_tts_on_is_idempotent(sandbox):
    sb = sandbox
    sb.arm(SID)
    r = _toggle(sb, hook_payload(SID, prompt="tts on"))
    assert r.returncode == 2
    assert (sb.recording / SID).exists()


# --------------------------------------------------------------------------
# tts off
# --------------------------------------------------------------------------
def test_tts_off_disarms_and_shushes(sandbox):
    sb = sandbox
    sb.arm(SID)
    assert not sb.stopflag.exists()
    r = _toggle(sb, hook_payload(SID, prompt="tts off"))
    assert r.returncode == 2
    assert r.stderr.strip() == "voice off"
    assert not (sb.recording / SID).exists()
    assert sb.stopflag.exists(), "tts off shushes: the stop flag is raised"
    assert sb.plays() == []
    assert any("TTS OFF" in line and SID[:8] in line for line in _log_lines(sb))


def test_tts_off_only_disarms_its_own_session(sandbox):
    sb = sandbox
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
def test_stop_words_shush_but_keep_the_session_armed(sandbox, prompt):
    sb = sandbox
    sb.arm(SID)
    assert not sb.stopflag.exists()
    r = _toggle(sb, hook_payload(SID, prompt=prompt))
    assert r.returncode == 2
    assert r.stderr.strip() == "quiet"
    assert sb.stopflag.exists(), "shush raises the stop flag"
    assert sb.plays() == []
    assert (sb.recording / SID).exists(), "shush stops playback, it does not turn the voice off"
    assert any("SHUSH" in line and SID[:8] in line for line in _log_lines(sb))


# --------------------------------------------------------------------------
# again / replay
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "prompt", ["again", "replay", "repeat", "say that again", "repeat that", "one more time"]
)
def test_replay_words_replay_this_session(sandbox, fake_engine, prompt):
    sb = sandbox
    saved = "The reply that was saved for this session."
    (sb.lastreply / f"{SID}.txt").write_text(saved)
    (sb.lastreply / "other-session.txt").write_text("Another session's reply.")
    r = _toggle(sb, hook_payload(SID, prompt=prompt))
    assert r.returncode == 2
    assert r.stderr.strip() == "replaying"
    assert any("REPLAY" in line and SID[:8] in line for line in _log_lines(sb))

    assert sb.wait_for(lambda: sb.plays()), _log_lines(sb)
    assert sb.wait_quiet()
    assert [q["body"] for q in fake_engine.requests] == [saved], "replay must re-speak THIS session's text"
    assert len(sb.plays()) == 1
    assert (sb.lastreply / f"{SID}.pcm").exists()


def test_replay_with_nothing_recorded_says_so(sandbox):
    sb = sandbox
    assert list(sb.lastreply.iterdir()) == []
    r = _toggle(sb, hook_payload(SID, prompt="again"))
    assert r.returncode == 2
    assert r.stderr.strip() == "nothing recorded yet"
    assert r.stdout == ""
    assert not any("REPLAY" in line for line in _log_lines(sb))
    assert sb.plays() == []
    assert not sb.stopflag.exists(), "nothing to replay means nothing is shushed either"


# --------------------------------------------------------------------------
# everything else passes through
# --------------------------------------------------------------------------
@pytest.mark.parametrize("prompt", ["please turn tts on", "tts on now", "hello"])
def test_ordinary_prompts_pass_through_untouched(sandbox, prompt):
    sb = sandbox
    r = _toggle(sb, hook_payload(SID, prompt=prompt))
    assert r.returncode == 0
    assert r.stderr == ""
    assert r.stdout == "", "exit 0 stdout becomes context for Claude; it must stay empty"
    assert not (sb.recording / SID).exists()
    assert sb.read_calls() == []
    assert not sb.log.exists()


def test_payload_without_prompt_is_ignored(sandbox):
    sb = sandbox
    r = _toggle(sb, hook_payload(SID))
    assert r.returncode == 0
    assert r.stderr == ""
    assert r.stdout == ""
    assert not (sb.recording / SID).exists()
    assert sb.read_calls() == []


# --------------------------------------------------------------------------
# session id fallback
# --------------------------------------------------------------------------
def test_missing_session_id_uses_unknown(sandbox):
    sb = sandbox
    r = _toggle(sb, json.dumps({"prompt": "tts on"}))
    assert r.returncode == 2
    assert r.stderr.strip() == "voice on"
    assert (sb.recording / "unknown").exists()
    assert any("TTS ON" in line and "unknown" in line for line in _log_lines(sb))
