"""Upgrading a Stage 1 install: the settings.json it left behind carries the
shell hooks by path. After install.sh there is exactly one Stop and one
UserPromptSubmit entry, both the venv-python command, everything foreign
untouched — and a second run changes nothing. A double Stop hook would make
every reply play twice, which is the one bug this project has already had."""

import json

import test_install

# The install harness lives in test_install.py (its fixtures need the
# sandbox, so they cannot sit in the frozen conftest). Re-binding them here
# makes pytest collect them for this module under their own names.
installer = test_install.installer
source = test_install.source

OTHER_STOP = {"type": "command", "command": "/usr/local/bin/other-stop-hook"}
GUARD = {"type": "command", "command": "/usr/local/bin/guard", "timeout": 5}


def _entries(settings, event):
    return [e for block in settings["hooks"][event] for e in block["hooks"]]


def _old_settings(home):
    hooks = home / ".claude" / "automation" / "notifications"
    return {
        "model": "opus",
        "hooks": {
            "Stop": [
                {
                    "matcher": "",
                    "hooks": [
                        {"type": "command", "command": f"{hooks}/speak_last_reply.sh"},
                        OTHER_STOP,
                        # a second copy, as a hand-edited file might carry
                        {"type": "command", "command": f"{hooks}/speak_last_reply.sh"},
                    ],
                }
            ],
            "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": f"{hooks}/tts_toggle.sh"}]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [GUARD]}],
        },
    }


def test_old_shell_hooks_become_one_stop_and_one_prompt_hook(installer):
    installer.settings.parent.mkdir(parents=True)
    installer.settings.write_text(json.dumps(_old_settings(installer.home), indent=2))
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "replaced Stop" in r.stdout
    assert "replaced UserPromptSubmit" in r.stdout
    stop_cmd, prompt_cmd = installer.commands()
    after = json.loads(installer.settings.read_text())

    stop = _entries(after, "Stop")
    assert [e["command"] for e in stop] == [stop_cmd, OTHER_STOP["command"]]  # rewritten in place, duplicate gone
    assert sum(1 for e in stop if "talkback" in e["command"]) == 1
    assert [e["command"] for e in _entries(after, "UserPromptSubmit")] == [prompt_cmd]
    assert all(e["type"] == "command" for e in stop)
    for cmd in (stop_cmd, prompt_cmd):
        assert cmd.startswith(f'"{installer.venv_python.as_posix()}" -m talkback ')
        assert ".sh" not in cmd

    # nothing else moved
    assert after["model"] == "opus"
    assert after["hooks"]["PreToolUse"] == [{"matcher": "Bash", "hooks": [GUARD]}]
    assert len(after["hooks"]["Stop"]) == 1


def test_a_previous_package_install_at_another_interpreter_is_replaced(installer):
    old = '"/somewhere/else/.venv/bin/python" -m talkback speak'
    installer.settings.parent.mkdir(parents=True)
    installer.settings.write_text(json.dumps({"hooks": {"Stop": [{"matcher": "", "hooks": [{"type": "command", "command": old}]}]}}))
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "replaced Stop" in r.stdout
    assert "added UserPromptSubmit" in r.stdout
    stop_cmd, prompt_cmd = installer.commands()
    after = json.loads(installer.settings.read_text())
    assert [e["command"] for e in _entries(after, "Stop")] == [stop_cmd]
    assert [e["command"] for e in _entries(after, "UserPromptSubmit")] == [prompt_cmd]


def test_second_run_after_the_upgrade_is_idempotent(installer):
    installer.settings.parent.mkdir(parents=True)
    installer.settings.write_text(json.dumps(_old_settings(installer.home)))
    first = installer.run()
    assert first.returncode == 0, first.stdout + first.stderr
    upgraded = installer.settings.read_bytes()

    second = installer.run()
    assert second.returncode == 0, second.stdout + second.stderr
    assert "added" not in second.stdout
    assert "replaced" not in second.stdout
    assert installer.settings.read_bytes() == upgraded
    after = json.loads(upgraded)
    assert sum(1 for e in _entries(after, "Stop") if "talkback" in e["command"]) == 1
    assert len(_entries(after, "UserPromptSubmit")) == 1
