"""hooks/speak_last_reply.py — what the Stop hook reads from the transcript and
how it rewrites the reply for the ear.

Pure functions are imported and called directly; the CLI contract
(``python3 speak_last_reply.py <transcript> <tmpdir>``) runs as a subprocess in
the sandbox HOME with no TTS_* / ELEVENLABS_* keys in the environment.
"""

import json
import os
import re

import pytest
import speak_last_reply as slr
from conftest import assistant_turn, tool_turn, user_turn, write_transcript

INLINE = re.compile(r"`([^`]*)`")
CLOCK = re.compile(r"(\d{1,2}):(\d{2})")


def _transcript(tmp_path, turns):
    return write_transcript(tmp_path / "t.jsonl", turns)


def _cli(sandbox, tmp_path, turns, env=None):
    """Run the hook's Python step the way play_reply.sh does and return
    (CompletedProcess, out_dir)."""
    tp = _transcript(tmp_path, turns)
    out = tmp_path / "out"
    out.mkdir()
    r = sandbox.run(["python3", sandbox.hooks / "speak_last_reply.py", tp, out], env=env)
    return r, out


# --------------------------------------------------------------------------
# last_assistant_text
# --------------------------------------------------------------------------
def test_last_assistant_text_picks_the_last_turn_with_text(tmp_path):
    tp = _transcript(tmp_path, [
        user_turn("hello"),
        assistant_turn("First reply."),
        user_turn("and again"),
        assistant_turn("Second reply."),
    ])
    assert slr.last_assistant_text(tp) == "Second reply."


def test_tool_only_turns_are_skipped_so_the_previous_text_wins(tmp_path):
    tp = _transcript(tmp_path, [
        assistant_turn("I will check that."),
        tool_turn("Bash", command="ls -la"),
        tool_turn("Read", file_path="/etc/hosts"),
    ])
    text = slr.last_assistant_text(tp)
    assert text == "I will check that."
    assert "ls -la" not in text


def test_multi_block_content_is_joined_and_tool_blocks_are_dropped(tmp_path):
    tool_block = {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "rm -rf build"}}
    tp = _transcript(tmp_path, [assistant_turn(["First part. ", tool_block, "Second part."])])
    text = slr.last_assistant_text(tp)
    assert text == "First part. Second part."
    assert "rm -rf" not in text


def test_non_assistant_and_malformed_lines_are_ignored(tmp_path):
    tp = _transcript(tmp_path, [
        assistant_turn("Real reply."),
        "{this is not json",
        "",
        user_turn("Real reply. NOT — this is the user speaking"),
        {"type": "assistant", "message": {"role": "assistant", "content": "a string, not a block list"}},
        {"type": "assistant"},
        {"type": "system", "message": {"content": [{"type": "text", "text": "system text"}]}},
    ])
    assert slr.last_assistant_text(tp) == "Real reply."


def test_a_non_object_json_line_does_not_abort_the_hook(tmp_path):
    # "a corrupt transcript line must never abort the hook" — a line that parses
    # as JSON but is not an object is exactly as corrupt as one that does not parse.
    for junk in ("null", "[]", '"just a string"'):
        tp = write_transcript(tmp_path / f"t-{len(junk)}.jsonl", [assistant_turn("Real reply."), junk])
        assert slr.last_assistant_text(tp) == "Real reply."


def test_no_assistant_text_gives_an_empty_string(tmp_path):
    only_tools = _transcript(tmp_path, [user_turn("hi"), tool_turn("Bash", command="ls")])
    assert slr.last_assistant_text(only_tools) == ""
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert slr.last_assistant_text(empty) == ""


# --------------------------------------------------------------------------
# say_time
# --------------------------------------------------------------------------
def test_clock_times_are_spoken_as_words():
    assert slr.say_time(CLOCK.match("9:00")) == "nine o'clock"
    assert slr.say_time(CLOCK.match("9:05")) == "nine oh five"
    assert slr.say_time(CLOCK.match("13:45")) == "one forty-five"
    assert slr.rewrite("Meet at 9:00.") == "Meet at nine o'clock."
    assert slr.rewrite("Meet at 9:05.") == "Meet at nine oh five."
    assert slr.rewrite("Meet at 13:45.") == "Meet at one forty-five."


def test_impossible_clock_times_are_left_alone():
    assert slr.say_time(CLOCK.match("24:00")) == "24:00"
    assert slr.say_time(CLOCK.match("9:60")) == "9:60"
    assert slr.rewrite("Meet at 24:00.") == "Meet at 24:00."
    assert slr.rewrite("Meet at 9:60.") == "Meet at 9:60."


# --------------------------------------------------------------------------
# say_inline / SAYABLE
# --------------------------------------------------------------------------
def test_short_inline_code_is_spoken_as_written():
    assert slr.say_inline(INLINE.match("`shush`")) == "shush"
    assert slr.say_inline(INLINE.match("`main.py`")) == "main.py"
    assert slr.say_inline(INLINE.match("`TTS on`")) == "TTS on"
    assert slr.rewrite("Run `shush` now.") == "Run shush now."
    assert slr.rewrite("Open `main.py` first.") == "Open main.py first."
    assert slr.rewrite("Type `TTS on` as the whole message.") == "Type TTS on as the whole message."


def test_unlistenable_inline_code_becomes_a_command():
    assert slr.say_inline(INLINE.match("`--flag=value`")) == "a command"
    assert slr.say_inline(INLINE.match("`" + "x" * 26 + "`")) == "a command"
    assert slr.say_inline(INLINE.match("`" + "x" * 25 + "`")) == "x" * 25
    assert slr.say_inline(INLINE.match("`a.b.c`")) == "a command"
    assert slr.rewrite("Run `--flag=value` to start.") == "Run a command to start."
    assert slr.rewrite("Run `abcdefghijklmnopqrstuvwxyz` to start.") == "Run a command to start."
    assert slr.rewrite("See `a.b.c` there.") == "See a command there."


def test_empty_backticks_vanish_without_breaking_the_sentence():
    assert slr.say_inline(INLINE.match("``")) == ""
    assert slr.say_inline(INLINE.match("`  `")) == ""
    assert slr.rewrite("Run `` now.") == "Run now."


def test_sayable_pattern_accepts_short_plain_tokens_only():
    for ok in ("shush", "main.py", "TTS on", "x" * 25):
        assert slr.SAYABLE.match(ok), ok
    for bad in ("--flag=value", "x" * 26, "", "-x", "$HOME"):
        assert not slr.SAYABLE.match(bad), bad


# --------------------------------------------------------------------------
# rewrite
# --------------------------------------------------------------------------
def test_fenced_code_becomes_shown_on_screen():
    out = slr.rewrite("Here:\n```py\nprint(1)\n```\nDone.")
    assert "shown on screen" in out
    assert "print" not in out
    assert "```" not in out
    assert out.endswith("Done.")
    assert slr.rewrite("one line: ```x=1```") == "one line, shown on screen."


def test_urls_and_domains_become_a_link():
    assert slr.rewrite("See https://example.com/path now.") == "See a link now."
    assert slr.rewrite("Go to www.example.com today.") == "Go to a link today."
    assert slr.rewrite("Go to example.com today.") == "Go to a link today."


def test_emails_become_an_email_address():
    assert slr.rewrite("Mail me@example.com please.") == "Mail an email address please."


def test_hash_like_tokens_become_an_id():
    assert slr.rewrite("Voice 21m00Tcm4TlvDq8ikWAM is set.") == "Voice an I D is set."
    assert slr.rewrite("id 123e4567-e89b-12d3-a456-426614174000 done") == "id an I D done"
    assert slr.rewrite("token abc123def456 here") == "token an I D here"
    # digits alone are a number, not an id
    assert slr.rewrite("number 123456789012 here") == "number 123456789012 here"


def test_markdown_decoration_is_stripped():
    assert slr.rewrite("## Title\nText.") == "Title\nText."
    assert slr.rewrite("**bold** and *it* here.") == "bold and it here."
    assert slr.rewrite("- item one\n- item two") == "item one\nitem two"
    assert slr.rewrite("Read the [docs](https://example.com/docs) now.") == "Read the docs now."


def test_snake_case_is_spaced_and_dashes_become_pauses():
    assert slr.rewrite("Run speak_last_reply now.") == "Run speak last reply now."
    assert slr.rewrite("Fast — really fast.") == "Fast, really fast."
    assert slr.rewrite("Fast – really fast.") == "Fast, really fast."


def test_output_is_capped_at_2500_chars():
    assert len(slr.rewrite("x" * 2600)) == 2500
    assert len(slr.rewrite("x" * 2500)) == 2500
    assert len(slr.rewrite("x" * 100)) == 100
    assert len(slr.rewrite("word " * 1000)) == 2500


# --------------------------------------------------------------------------
# CLI contract: python3 speak_last_reply.py <transcript> <tmpdir>
# --------------------------------------------------------------------------
def test_cli_exits_1_with_empty_text_when_only_tool_turns(sandbox, tmp_path):
    r, out = _cli(sandbox, tmp_path, [user_turn("do it"), tool_turn("Bash", command="ls")])
    assert r.returncode == 1
    assert "empty text" in r.stderr
    assert not (out / "text.txt").exists()


def test_cli_exits_1_with_empty_text_when_no_assistant_turn(sandbox, tmp_path):
    r, out = _cli(sandbox, tmp_path, [user_turn("hello?")])
    assert r.returncode == 1
    assert "empty text" in r.stderr
    assert not (out / "text.txt").exists()


def test_cli_treats_a_symbol_only_reply_as_empty(sandbox, tmp_path):
    r, out = _cli(sandbox, tmp_path, [assistant_turn("👍")])
    assert r.returncode == 1
    assert "empty text" in r.stderr
    assert not (out / "text.txt").exists()


def test_cli_writes_the_rewritten_text_and_prints_its_length(sandbox, tmp_path):
    last = "Run `shush` to stop — see https://example.com/docs at 9:05."
    r, out = _cli(sandbox, tmp_path, [
        assistant_turn("An earlier reply."),
        tool_turn("Bash", command="ls"),
        assistant_turn(last),
        tool_turn("Read", file_path="x"),
    ])
    assert r.returncode == 0, r.stderr
    expected = slr.rewrite(last)
    assert expected == "Run shush to stop, see a link at nine oh five."
    assert r.stdout.strip() == str(len(expected))
    assert (out / "text.txt").read_text() == expected
    assert json.loads((out / "payload.json").read_text())["text"] == expected


def test_cli_writes_the_voice_id_from_the_environment(sandbox, tmp_path):
    r, out = _cli(sandbox, tmp_path, [assistant_turn("A reply.")], env=dict(sandbox.env, TTS_VOICE_ID="voice-123"))
    assert r.returncode == 0, r.stderr
    assert (out / "voice").read_text() == "voice-123"


@pytest.fixture
def clean_home(monkeypatch, tmp_path):
    """In-process main(): HOME is a throwaway (no ~/.zshrc, so no key can leak
    in) and no TTS_* / ELEVENLABS_* variable from the pytest process survives."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    for k in list(os.environ):
        if k.startswith(("TTS_", "ELEVENLABS_")):
            monkeypatch.delenv(k)
    return home


def test_main_reads_the_voice_id_at_call_time(clean_home, monkeypatch, tmp_path, capsys):
    tp = _transcript(tmp_path, [assistant_turn("A reply.")])
    out = tmp_path / "out"
    out.mkdir()
    monkeypatch.setenv("TTS_VOICE_ID", "set-after-import")
    assert slr.main(["speak_last_reply.py", str(tp), str(out)]) == 0
    assert capsys.readouterr().out.strip() == str(len("A reply."))
    assert (out / "voice").read_text() == "set-after-import"
    assert (out / "key").read_text() == ""
    assert (out / "text.txt").read_text() == "A reply."
