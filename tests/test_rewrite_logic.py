"""talkback.rewrite — the transcript reader, the ear rewriter and prepare(),
in process. Pure: tmp_path and function calls only. The CLI contract of the
hooks/speak_last_reply.py shim is pinned by test_speak_last_reply_py.py."""

import json
import os
import re

import pytest
from conftest import assistant_turn, tool_turn, user_turn, write_transcript

from talkback import rewrite as rw

pytestmark = pytest.mark.pure

CLOCK = re.compile(r"(\d{1,2}):(\d{2})")
INLINE = re.compile(r"`([^`]*)`")


def test_last_assistant_text_skips_tool_turns_joins_blocks_and_ignores_junk(tmp_path):
    tool_block = {"type": "tool_use", "id": "toolu_1", "name": "Bash", "input": {"command": "rm -rf build"}}
    tp = write_transcript(tmp_path / "t.jsonl", [
        user_turn("hello"),
        assistant_turn("First reply."),
        "{this is not json",
        "null",
        '{"type": "assistant", "message": null}',
        assistant_turn(["Second part one. ", tool_block, "Second part two."]),
        tool_turn("Read", file_path="/etc/hosts"),
        user_turn("Second part one. NOT the assistant"),
    ])
    assert rw.last_assistant_text(tp) == "Second part one. Second part two."
    empty = tmp_path / "empty.jsonl"
    empty.write_text("")
    assert rw.last_assistant_text(empty) == ""


def test_times_inline_code_links_ids_and_markdown():
    assert rw.say_time(CLOCK.match("9:05")) == "nine oh five"
    assert rw.say_time(CLOCK.match("24:00")) == "24:00"
    assert rw.say_inline(INLINE.match("`main.py`")) == "main.py"
    assert rw.say_inline(INLINE.match("`--flag=value`")) == "a command"
    assert rw.say_inline(INLINE.match("``")) == ""
    assert rw.rewrite("Run `shush` to stop — see https://example.com/docs at 9:05.") == "Run shush to stop, see a link at nine oh five."
    assert rw.rewrite("Mail me@example.com please.") == "Mail an email address please."
    assert rw.rewrite("Voice 21m00Tcm4TlvDq8ikWAM is set.") == "Voice an I D is set."
    assert rw.rewrite("one line: ```x=1```") == "one line, shown on screen."
    assert rw.rewrite("## Title\n**bold** and *it* here.\n- item") == "Title\nbold and it here.\nitem"
    assert rw.rewrite("Run speak_last_reply now.") == "Run speak last reply now."
    assert len(rw.rewrite("word " * 1000)) == 2500
    assert rw.rewrite("👍") == ""


def test_prepare_writes_text_payload_key_and_voice_in_process(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    tp = write_transcript(tmp_path / "t.jsonl", [assistant_turn("An earlier reply."), assistant_turn("A reply at 9:00.")])
    out = tmp_path / "out"
    out.mkdir()
    env = {"TTS_VOICE_ID": "voice-123", "TTS_SPEED": "1.0"}
    assert rw.prepare(str(tp), str(out), env, home=home) == len("A reply at nine o'clock.")
    assert (out / "text.txt").read_text(encoding="utf-8") == "A reply at nine o'clock."
    payload = json.loads((out / "payload.json").read_text(encoding="utf-8"))
    assert payload["text"] == "A reply at nine o'clock."
    assert payload["model_id"] == "eleven_multilingual_v2"
    assert payload["voice_settings"]["speed"] == 1.0
    assert payload["voice_settings"]["use_speaker_boost"] is True
    assert (out / "key").read_text(encoding="utf-8") == ""
    assert (out / "voice").read_text(encoding="utf-8") == "voice-123"
    if os.name != "nt":
        assert (out / "key").stat().st_mode & 0o777 == 0o600


def test_prepare_reports_empty_text_and_writes_nothing(tmp_path, capsys):
    tp = write_transcript(tmp_path / "t.jsonl", [user_turn("do it"), tool_turn("Bash", command="ls")])
    out = tmp_path / "out"
    out.mkdir()
    assert rw.prepare(str(tp), str(out), {}, home=tmp_path) == 0
    assert "empty text" in capsys.readouterr().err
    assert list(out.iterdir()) == []


def test_only_the_elevenlabs_engine_needs_a_key(tmp_path, capsys):
    home = tmp_path / "home"
    home.mkdir()
    tp = write_transcript(tmp_path / "t.jsonl", [assistant_turn("Hello there.")])
    out = tmp_path / "out"
    out.mkdir()
    # kokoro (default): no key anywhere is fine
    assert rw.prepare(str(tp), str(out), {}, home=home) == len("Hello there.")
    assert (out / "key").read_text(encoding="utf-8") == ""
    # elevenlabs without a key: refused, nothing written
    out2 = tmp_path / "out2"
    out2.mkdir()
    assert rw.prepare(str(tp), str(out2), {"TTS_ENGINE": "elevenlabs"}, home=home) == 0
    assert "no api key" in capsys.readouterr().err
    assert list(out2.iterdir()) == []
    # elevenlabs with the key in the environment
    out3 = tmp_path / "out3"
    out3.mkdir()
    assert rw.prepare(str(tp), str(out3), {"TTS_ENGINE": "elevenlabs", "ELEVENLABS_API_KEY": "sk_env"}, home=home) > 0
    assert (out3 / "key").read_text(encoding="utf-8") == "sk_env"
    # elevenlabs with the key only in ~/.zshrc of the given home (Mac behaviour, kept)
    (home / ".zshrc").write_text('export ELEVENLABS_API_KEY="sk_from_zshrc"\n', encoding="utf-8")
    out4 = tmp_path / "out4"
    out4.mkdir()
    assert rw.prepare(str(tp), str(out4), {"TTS_ENGINE": "elevenlabs"}, home=home) > 0
    assert (out4 / "key").read_text(encoding="utf-8") == "sk_from_zshrc"


def test_main_prints_the_count_or_exits_1(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    for k in list(os.environ):
        if k.startswith(("TTS_", "ELEVENLABS_", "TALKBACK_")):
            monkeypatch.delenv(k)
    tp = write_transcript(tmp_path / "t.jsonl", [assistant_turn("A reply.")])
    out = tmp_path / "out"
    out.mkdir()
    assert rw.main(["speak_last_reply.py", str(tp), str(out)]) == 0
    assert capsys.readouterr().out.strip() == "8"
    empty = write_transcript(tmp_path / "e.jsonl", [user_turn("hi")])
    assert rw.main(["speak_last_reply.py", str(empty), str(out)]) == 1
    assert "empty text" in capsys.readouterr().err
