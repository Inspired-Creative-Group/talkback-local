"""The settings.json half of the installer, in process: the exact hook command
text, and ``merge_settings`` on every shape of file an upgrade can meet.

Pure: dicts, strings and ``tmp_path`` only."""

import json
from pathlib import Path, PurePosixPath, PureWindowsPath

import pytest

from talkback import install

pytestmark = pytest.mark.pure

VENV = Path("/Users/someone/.claude/automation/kokoro/.venv/bin/python")
STOP = install.hook_command(VENV, "speak")
PROMPT = install.hook_command(VENV, "toggle")


def _commands(settings, event):
    return [e["command"] for block in settings["hooks"][event] for e in block["hooks"]]


def _block(*entries):
    return {"matcher": "", "hooks": list(entries)}


def _cmd(text):
    return {"type": "command", "command": text}


# --------------------------------------------------------------------------
# hook_command
# --------------------------------------------------------------------------
def test_hook_command_is_the_double_quoted_interpreter_and_the_subcommand():
    assert STOP == '"/Users/someone/.claude/automation/kokoro/.venv/bin/python" -m talkback speak'
    assert PROMPT == '"/Users/someone/.claude/automation/kokoro/.venv/bin/python" -m talkback toggle'


def test_hook_command_uses_forward_slashes_on_windows_and_survives_a_space():
    win = PureWindowsPath(r"C:\Users\First Last\.claude\automation\kokoro\.venv\Scripts\python.exe")
    assert install.hook_command(win, "speak") == (
        '"C:/Users/First Last/.claude/automation/kokoro/.venv/Scripts/python.exe" -m talkback speak'
    )
    # a plain string with backslashes (what install.ps1 hands over) gets the same treatment
    assert install.hook_command(r"C:\x\python.exe", "toggle") == '"C:/x/python.exe" -m talkback toggle'
    assert install.hook_command(PurePosixPath("/a b/python"), "toggle") == '"/a b/python" -m talkback toggle'


def test_hook_command_has_nothing_shell_like_but_the_quotes():
    for cmd in (STOP, PROMPT):
        assert cmd.count('"') == 2
        assert not any(ch in cmd for ch in "$`'\\;&|<>")


# --------------------------------------------------------------------------
# merge_settings
# --------------------------------------------------------------------------
def test_empty_settings_get_one_stop_and_one_prompt_hook():
    out, messages = install.merge_settings({}, STOP, PROMPT)
    assert messages == ["added Stop", "added UserPromptSubmit"]
    assert set(out["hooks"]) == {"Stop", "UserPromptSubmit"}
    assert out["hooks"]["Stop"] == [_block(_cmd(STOP))]
    assert out["hooks"]["UserPromptSubmit"] == [_block(_cmd(PROMPT))]


def test_foreign_hooks_and_other_keys_are_kept():
    before = {
        "model": "opus",
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "Stop": [_block(_cmd("/usr/local/bin/other-stop-hook"))],
            "PreToolUse": [{"matcher": "Bash", "hooks": [_cmd("/usr/local/bin/guard")]}],
        },
    }
    out, messages = install.merge_settings(json.loads(json.dumps(before)), STOP, PROMPT)
    assert messages == ["added Stop", "added UserPromptSubmit"]
    assert out["model"] == "opus"
    assert out["permissions"] == before["permissions"]
    assert out["hooks"]["PreToolUse"] == before["hooks"]["PreToolUse"]
    assert _commands(out, "Stop") == ["/usr/local/bin/other-stop-hook", STOP]
    assert _commands(out, "UserPromptSubmit") == [PROMPT]


def test_an_emptied_hook_list_gets_a_block_first():
    # what Claude Code leaves behind when every Stop hook is removed
    out, messages = install.merge_settings({"hooks": {"Stop": []}}, STOP, PROMPT)
    assert messages == ["added Stop", "added UserPromptSubmit"]
    assert out["hooks"]["Stop"] == [_block(_cmd(STOP))]


def test_old_shell_commands_are_replaced_in_place():
    old_stop = "/Users/someone/.claude/automation/notifications/speak_last_reply.sh"
    old_prompt = "/Users/someone/.claude/automation/notifications/tts_toggle.sh"
    before = {
        "hooks": {
            "Stop": [_block(_cmd("/usr/local/bin/first"), _cmd(old_stop), _cmd("/usr/local/bin/last"))],
            "UserPromptSubmit": [_block({"type": "command", "command": old_prompt, "timeout": 5})],
        }
    }
    out, messages = install.merge_settings(before, STOP, PROMPT)
    assert messages == ["replaced Stop", "replaced UserPromptSubmit"]
    assert _commands(out, "Stop") == ["/usr/local/bin/first", STOP, "/usr/local/bin/last"]  # position kept
    (entry,) = out["hooks"]["UserPromptSubmit"][0]["hooks"]
    assert entry == {"type": "command", "command": PROMPT, "timeout": 5}  # other fields kept


def test_a_package_command_at_another_interpreter_is_replaced():
    old = '"/somewhere/else/.venv/bin/python" -m talkback speak'
    out, messages = install.merge_settings({"hooks": {"Stop": [_block(_cmd(old))]}}, STOP, PROMPT)
    assert messages == ["replaced Stop", "added UserPromptSubmit"]
    assert _commands(out, "Stop") == [STOP]


def test_a_duplicate_talkback_entry_is_collapsed_to_one():
    old = "/x/notifications/speak_last_reply.sh"
    before = {
        "hooks": {
            "Stop": [
                _block(_cmd(old), _cmd("/usr/local/bin/other"), _cmd(old)),
                _block(_cmd(STOP)),  # a second block carrying the new command too
            ]
        }
    }
    out, messages = install.merge_settings(before, STOP, PROMPT)
    assert messages == ["replaced Stop", "added UserPromptSubmit"]
    assert _commands(out, "Stop") == [STOP, "/usr/local/bin/other"]
    assert sum("talkback" in c for c in _commands(out, "Stop")) == 1


def test_merge_is_idempotent():
    once, _ = install.merge_settings({"hooks": {"Stop": [_block(_cmd("/usr/local/bin/other"))]}}, STOP, PROMPT)
    snapshot = json.dumps(once, sort_keys=True)
    twice, messages = install.merge_settings(json.loads(snapshot), STOP, PROMPT)
    assert messages == []
    assert json.dumps(twice, sort_keys=True) == snapshot


def test_malformed_shapes_are_tolerated_not_crashed_on():
    before = {"hooks": "not a dict"}
    out, messages = install.merge_settings(before, STOP, PROMPT)
    assert messages == ["added Stop", "added UserPromptSubmit"]
    assert _commands(out, "Stop") == [STOP]

    before = {"hooks": {"Stop": ["junk", {"matcher": ""}, {"matcher": "", "hooks": "junk"}]}}
    out, messages = install.merge_settings(before, STOP, PROMPT)
    assert messages == ["added Stop", "added UserPromptSubmit"]
    assert out["hooks"]["Stop"][0] == _block(_cmd(STOP))  # the unusable first block was replaced
    assert _commands(out, "UserPromptSubmit") == [PROMPT]


# --------------------------------------------------------------------------
# the settings verb, on disk
# --------------------------------------------------------------------------
def test_settings_verb_writes_the_file_under_home_and_reports(tmp_path, capsys):
    venv = tmp_path / ".claude" / "automation" / "kokoro" / ".venv" / "bin" / "python"
    assert install._settings([str(venv)], home=tmp_path) == 0
    path = tmp_path / ".claude" / "settings.json"
    settings = json.loads(path.read_text(encoding="utf-8"))
    assert _commands(settings, "Stop") == [install.hook_command(venv, "speak")]
    assert _commands(settings, "UserPromptSubmit") == [install.hook_command(venv, "toggle")]
    assert capsys.readouterr().out == "    added Stop\n    added UserPromptSubmit\n"
    assert not list(path.parent.glob(".*.tmp"))  # written atomically, nothing left behind


def test_settings_verb_refuses_invalid_json_and_changes_nothing(tmp_path, capsys):
    path = tmp_path / ".claude" / "settings.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert install._settings([str(tmp_path / "python")], home=tmp_path) == 1
    assert path.read_text(encoding="utf-8") == "{not json"
    assert "not valid JSON" in capsys.readouterr().err


# --------------------------------------------------------------------------
# render and shim
# --------------------------------------------------------------------------
def test_render_replaces_every_placeholder_and_unrendered_lists_the_rest():
    text = install.render("__HOME__/x __PORT__ __HOME__", HOME="/h", PORT=8930)
    assert text == "/h/x 8930 /h"
    assert install.unrendered("__A__ __B__ __A__ __not__ a__b") == ["__A__", "__B__"]
    assert install.unrendered(text) == []


def test_render_verb_refuses_to_leave_a_placeholder_unrendered(tmp_path, capsys):
    template = tmp_path / "t.template"
    template.write_text("<string>__HOME__</string><string>__PORT__</string>", encoding="utf-8")
    out = tmp_path / "out.plist"
    assert install.main(["render", str(template), str(out), "HOME=/h"]) == 1
    assert not out.exists()
    assert "__PORT__" in capsys.readouterr().err
    assert install.main(["render", str(template), str(out), "HOME=/h", "PORT=8930"]) == 0
    assert out.read_text(encoding="utf-8") == "<string>/h</string><string>8930</string>"


def test_shim_text_substitutes_the_interpreter_literal_only():
    src = '#!/bin/bash\n# shim\nexec "${TALKBACK_PYTHON:-python3}" -m talkback speak "$@"\n'
    assert install.shim_text(src, "/v/bin/python") == '#!/bin/bash\n# shim\nexec "/v/bin/python" -m talkback speak "$@"\n'
    assert install.shim_text("no literal here\n", "/v/bin/python") == "no literal here\n"


def test_shim_verb_writes_the_rendered_copy(tmp_path):
    src = tmp_path / "shush"
    src.write_text('#!/bin/bash\nexec "${TALKBACK_PYTHON:-python3}" -m talkback shush "$@"\n', encoding="utf-8")
    dst = tmp_path / "bin" / "shush"
    assert install.main(["shim", str(src), str(dst), "/v/bin/python"]) == 0
    assert dst.read_text(encoding="utf-8") == '#!/bin/bash\nexec "/v/bin/python" -m talkback shush "$@"\n'


def test_unknown_verb_and_bad_arity_exit_2(capsys):
    assert install.main([]) == 2
    assert install.main(["dance"]) == 2
    assert install.main(["port"]) == 2
    assert install.main(["port", "eighty"]) == 2
    assert install.main(["settings"]) == 2
    assert install.main(["render", "only-one"]) == 2
    assert install.main(["shim", "a", "b"]) == 2
    assert capsys.readouterr().out == ""
