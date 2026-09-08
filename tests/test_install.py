"""install.sh, end to end, in a throwaway HOME.

Every outside call the installer makes is faked on PATH: ``uv`` builds an
empty venv whose python answers the spacy check with exit 0, ``launchctl`` /
``espeak-ng`` / ``jq`` only log, ``ffplay`` and ``python3`` are the sandbox
shims. The repo is copied out of the checkout (tracked files only) so a test
can plant ``hooks/__pycache__/`` without touching the working tree. Nothing
here reaches the owner's install, launchd, or 127.0.0.1:8910.
"""

import filecmp
import json
import os
import plistlib
import shutil
import stat
import subprocess
import sys

import pytest

PLIST_NAME = "com.icg.talkback.plist"
COMMANDS = ("shush", "replay", "recmode", "kokoro-server")
PREREQS = ("uv", "launchctl", "espeak-ng", "jq", "ffplay", "python3")
SPACY_WHEEL = "en_core_web_sm-3.8.0-py3-none-any.whl"

_LOGGER = '''\
import os, shlex, sys, time
LOG = {log!r}
def log(name, args):
    with open(LOG, "a") as f:
        f.write(f"{{time.time()}} {{name}} {{shlex.join(args)}}\\n")
'''

# `uv venv [opts] <dir>` -> an empty venv whose python exits 0, so the
# installer's `python -c "import spacy..."` check passes. Everything else
# (`uv pip install ...`) is logged and succeeds without doing anything.
_UV = _LOGGER + '''\
args = sys.argv[1:]
log("uv", args)
if args[:1] == ["venv"]:
    d = args[-1]
    os.makedirs(os.path.join(d, "bin"), exist_ok=True)
    py = os.path.join(d, "bin", "python")
    with open(py, "w") as f:
        f.write("#!/bin/bash\\nexit 0\\n")
    os.chmod(py, 0o755)
'''

_LOG_ONLY = _LOGGER + '''\
log({name!r}, sys.argv[1:])
'''


def _write_exec(path, text):
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _shim(dirpath, impl_dir, name, source):
    impl = impl_dir / f"{name}.py"
    impl.write_text(source)
    _write_exec(dirpath / name, f'#!/bin/bash\nexec "{sys.executable}" "{impl}" "$@"\n')


def _hook_commands(settings, event):
    return [e["command"] for block in settings["hooks"][event] for e in block["hooks"]]


@pytest.fixture
def source(tmp_path, repo):
    """The tracked files of the repo, copied under tmp_path."""
    dst = tmp_path / "repo"
    out = subprocess.run(["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, check=True)
    names = [n for n in out.stdout.decode().split("\0") if n]
    assert "install.sh" in names
    for name in names:
        target = dst / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(repo / name, target)
    return dst


class Installer:
    def __init__(self, sandbox, source, tmp_path):
        self.sandbox = sandbox
        self.source = source
        self.home = sandbox.home
        self.engine = self.home / ".claude" / "automation" / "kokoro"
        self.hooks = sandbox.hooks
        self.bin = sandbox.bin
        self.settings = self.home / ".claude" / "settings.json"
        self.plist = self.home / "Library" / "LaunchAgents" / PLIST_NAME
        self.env = dict(sandbox.env)

        # A genuinely fresh HOME: the sandbox fixture pre-lays the hooks and
        # ~/bin so the hook tests can run; the installer has to create them.
        shutil.rmtree(self.home / ".claude")
        shutil.rmtree(self.bin)

        impl = tmp_path / "install_shim_impl"
        impl.mkdir()
        log = str(sandbox.calls)
        _shim(sandbox.shims, impl, "uv", _UV.format(log=log))
        for name in ("launchctl", "espeak-ng", "jq"):
            _shim(sandbox.shims, impl, name, _LOG_ONLY.format(log=log, name=name))

        # /bin/launchctl and /usr/bin/jq are real and on the sandbox PATH;
        # refuse to run anything unless every prerequisite resolves to a shim.
        probe = "for c in " + " ".join(PREREQS) + "; do command -v \"$c\"; done"
        r = subprocess.run(["/bin/bash", "-c", probe], env=self.env, text=True, capture_output=True, check=False)
        found = r.stdout.split()
        assert len(found) == len(PREREQS), r.stdout + r.stderr
        assert all(p.startswith(str(sandbox.shims) + os.sep) for p in found), found

        # For the missing-prerequisite test: a PATH with no system dirs at
        # all, holding only what install.sh touches before its checks.
        self.sysbin = tmp_path / "sysbin"
        self.sysbin.mkdir()
        for tool in ("uname", "dirname"):
            (self.sysbin / tool).symlink_to(f"/usr/bin/{tool}")

    def run(self, env=None):
        return subprocess.run(
            ["/bin/bash", "install.sh"],
            cwd=str(self.source),
            env=self.env if env is None else env,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )

    def env_without_system_dirs(self):
        return dict(self.env, PATH=os.pathsep.join([str(self.sandbox.shims), str(self.sysbin)]))

    def uv_calls(self, sub):
        return [a for a in self.sandbox.calls_to("uv") if a[:1] == [sub]]


@pytest.fixture
def installer(sandbox, source, tmp_path):
    return Installer(sandbox, source, tmp_path)


# --------------------------------------------------------------------------
# a fresh HOME
# --------------------------------------------------------------------------
def test_fresh_install_exits_zero_and_reports_installed(installer):
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Installed." in r.stdout
    assert "INCOMPLETE" not in r.stdout + r.stderr
    assert "FAILED" not in r.stdout + r.stderr


def test_engine_files_and_state_dirs_land_under_automation(installer):
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    for name in ("server.py", "icg_voice.pt"):
        assert filecmp.cmp(installer.source / "engine" / name, installer.engine / name, shallow=False), name
    auto = installer.home / ".claude" / "automation"
    assert (auto / "recording").is_dir()
    assert (auto / "lastreply").is_dir()


def test_every_hook_lands_and_shell_scripts_are_executable(installer):
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    src = sorted(p.name for p in (installer.source / "hooks").iterdir() if p.is_file())
    assert "speak_last_reply.sh" in src and "play_reply.sh" in src and "tts_toggle.sh" in src
    for name in src:
        dst = installer.hooks / name
        assert filecmp.cmp(installer.source / "hooks" / name, dst, shallow=False), name
        if name.endswith(".sh"):
            assert os.access(dst, os.X_OK), name
    assert sorted(p.name for p in installer.hooks.iterdir()) == src


def test_commands_land_executable_in_home_bin(installer):
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    for name in COMMANDS:
        dst = installer.bin / name
        assert filecmp.cmp(installer.source / "bin" / name, dst, shallow=False), name
        assert os.access(dst, os.X_OK), name


def test_python_env_is_built_with_uv_inside_the_engine_dir(installer):
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    venv = installer.engine / ".venv"
    venv_python = venv / "bin" / "python"
    assert "building the python environment" in r.stdout
    (venv_call,) = installer.uv_calls("venv")
    assert venv_call[-1] == str(venv)
    assert "--python" in venv_call
    assert os.access(venv_python, os.X_OK)
    pip_calls = installer.uv_calls("pip")
    assert pip_calls == [
        ["pip", "install", "--python", str(venv_python), "-r", str(installer.source / "engine" / "requirements.txt"), "-q"]
    ]


def test_missing_spacy_model_is_installed_from_the_wheel_url(installer):
    # An existing venv whose python fails `import spacy; spacy.load(...)`.
    venv_python = installer.engine / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    _write_exec(venv_python, "#!/bin/bash\nexit 1\n")
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert installer.uv_calls("venv") == []
    pip_calls = installer.uv_calls("pip")
    assert len(pip_calls) == 2
    wheel = pip_calls[-1]
    assert wheel[:3] == ["pip", "install", "--python"]
    assert wheel[3] == str(venv_python)
    assert wheel[-1].endswith(SPACY_WHEEL)


# --------------------------------------------------------------------------
# launch agent
# --------------------------------------------------------------------------
def test_missing_launch_agents_dir_is_created(installer):
    assert not (installer.home / "Library").exists()
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert installer.plist.parent.is_dir()
    assert installer.plist.is_file()


def test_plist_is_rendered_with_the_sandbox_home(installer):
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    text = installer.plist.read_text()
    assert "__HOME__" not in text
    assert str(installer.home) in text
    plist = plistlib.loads(installer.plist.read_bytes())
    assert plist["Label"] == "com.icg.talkback"
    assert plist["ProgramArguments"] == [str(installer.engine / ".venv" / "bin" / "python"), str(installer.engine / "server.py")]
    assert plist["WorkingDirectory"] == str(installer.engine)
    for arg in plist["ProgramArguments"]:
        assert os.path.exists(arg), arg


def test_launch_agent_is_unloaded_then_loaded(installer):
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    plist = str(installer.plist)
    assert installer.sandbox.calls_to("launchctl") == [["unload", plist], ["load", "-w", plist]]


# --------------------------------------------------------------------------
# Claude Code settings
# --------------------------------------------------------------------------
def test_settings_get_exactly_one_stop_and_one_prompt_hook(installer):
    assert not installer.settings.exists()
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "added Stop" in r.stdout
    assert "added UserPromptSubmit" in r.stdout
    settings = json.loads(installer.settings.read_text())
    assert set(settings["hooks"]) == {"Stop", "UserPromptSubmit"}
    assert _hook_commands(settings, "Stop") == [str(installer.hooks / "speak_last_reply.sh")]
    assert _hook_commands(settings, "UserPromptSubmit") == [str(installer.hooks / "tts_toggle.sh")]
    for event in ("Stop", "UserPromptSubmit"):
        for block in settings["hooks"][event]:
            for entry in block["hooks"]:
                assert entry["type"] == "command"


def test_existing_settings_are_kept_and_talkback_hooks_appended(installer):
    other_stop = {"type": "command", "command": "/usr/local/bin/other-stop-hook"}
    before = {
        "model": "opus",
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {
            "Stop": [{"matcher": "", "hooks": [other_stop]}],
            "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "/usr/local/bin/guard"}]}],
        },
    }
    installer.settings.parent.mkdir(parents=True)
    installer.settings.write_text(json.dumps(before))
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    after = json.loads(installer.settings.read_text())
    assert after["model"] == "opus"
    assert after["permissions"] == before["permissions"]
    assert after["hooks"]["PreToolUse"] == before["hooks"]["PreToolUse"]
    assert _hook_commands(after, "Stop") == ["/usr/local/bin/other-stop-hook", str(installer.hooks / "speak_last_reply.sh")]
    assert _hook_commands(after, "UserPromptSubmit") == [str(installer.hooks / "tts_toggle.sh")]


def test_settings_with_an_emptied_hook_list_still_get_registered(installer):
    # Claude Code leaves `"Stop": []` behind when every Stop hook is removed.
    installer.settings.parent.mkdir(parents=True)
    installer.settings.write_text(json.dumps({"hooks": {"Stop": []}}))
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    after = json.loads(installer.settings.read_text())
    assert _hook_commands(after, "Stop") == [str(installer.hooks / "speak_last_reply.sh")]
    assert _hook_commands(after, "UserPromptSubmit") == [str(installer.hooks / "tts_toggle.sh")]


# --------------------------------------------------------------------------
# re-running
# --------------------------------------------------------------------------
def test_second_run_is_idempotent(installer):
    first = installer.run()
    assert first.returncode == 0, first.stdout + first.stderr
    assert len(installer.uv_calls("venv")) == 1
    second = installer.run()
    assert second.returncode == 0, second.stdout + second.stderr
    assert "Installed." in second.stdout
    assert "INCOMPLETE" not in second.stdout + second.stderr
    assert "added" not in second.stdout
    assert "building the python environment" not in second.stdout
    assert len(installer.uv_calls("venv")) == 1
    settings = json.loads(installer.settings.read_text())
    assert _hook_commands(settings, "Stop") == [str(installer.hooks / "speak_last_reply.sh")]
    assert _hook_commands(settings, "UserPromptSubmit") == [str(installer.hooks / "tts_toggle.sh")]
    plist = str(installer.plist)
    assert installer.sandbox.calls_to("launchctl") == [["unload", plist], ["load", "-w", plist]] * 2


def test_pycache_in_hooks_neither_aborts_nor_gets_copied(installer):
    pyc = installer.source / "hooks" / "__pycache__" / "speak_last_reply.cpython-312.pyc"
    pyc.parent.mkdir()
    pyc.write_bytes(b"\x00junk")
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert not (installer.hooks / "__pycache__").exists()
    assert list(installer.hooks.rglob("*.pyc")) == []
    assert (installer.hooks / "speak_last_reply.py").is_file()


# --------------------------------------------------------------------------
# prerequisites
# --------------------------------------------------------------------------
def test_missing_prerequisite_aborts_before_changing_anything(installer):
    (installer.sandbox.shims / "jq").unlink()
    r = installer.run(env=installer.env_without_system_dirs())
    assert r.returncode != 0
    assert "Missing jq" in r.stdout + r.stderr
    assert not (installer.home / ".claude").exists()
    assert not installer.bin.exists()
    assert not (installer.home / "Library").exists()
    assert installer.sandbox.calls_to("launchctl") == []
    assert installer.sandbox.calls_to("uv") == []
