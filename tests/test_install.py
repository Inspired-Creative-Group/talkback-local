"""install.sh, end to end, in a throwaway HOME.

Every outside call the installer makes is faked on PATH: ``uv`` builds an
empty venv whose python answers the spacy check with exit 0, ``launchctl`` /
``espeak-ng`` only log, ``python3`` is the sandbox shim (the test
interpreter). The repo is copied out of the checkout (tracked and untracked
files, nothing ignored) so a test can plant ``hooks/__pycache__/`` without
touching the working tree. Nothing here reaches the owner's install, launchd,
or 127.0.0.1:8910.
"""

import filecmp
import json
import os
import plistlib
import shutil
import socket
import stat
import subprocess
import sys

import pytest

PLIST_NAME = "com.icg.talkback.plist"
COMMANDS = ("shush", "replay", "recmode", "kokoro-server", "talkback")
PREREQS = ("uv", "launchctl", "espeak-ng", "python3")
SPACY_WHEEL = "en_core_web_sm-3.8.0-py3-none-any.whl"
SHIM_LITERAL = "${TALKBACK_PYTHON:-python3}"

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
    if path.is_symlink():  # never write through a symlink to a real binary
        path.unlink()
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _shim(dirpath, impl_dir, name, source):
    impl = impl_dir / f"{name}.py"
    impl.write_text(source)
    _write_exec(dirpath / name, f'#!/bin/bash\nexec "{sys.executable}" "{impl}" "$@"\n')


def _hook_commands(settings, event):
    return [e["command"] for block in settings["hooks"][event] for e in block["hooks"]]


def hook_command(venv_python, sub):
    """What the installer writes into settings.json (the package's own
    ``talkback.install.hook_command``, restated here so the test pins the
    text and not the function)."""
    return f'"{venv_python.as_posix()}" -m talkback {sub}'


def free_port():
    """A port nothing listens on right now (bound to 0, then released)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def source(tmp_path, repo):
    """The repo's files — tracked and untracked, never ignored — copied under tmp_path."""
    dst = tmp_path / "repo"
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        capture_output=True,
        check=True,
    )
    names = [n for n in out.stdout.decode().split("\0") if n]
    assert "install.sh" in names
    for name in names:
        if not (repo / name).is_file():
            continue  # deleted in the working tree but still in the index
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
        self.venv_python = self.engine / ".venv" / "bin" / "python"
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
        for name in ("launchctl", "espeak-ng"):
            _shim(sandbox.shims, impl, name, _LOG_ONLY.format(log=log, name=name))

        # /bin/launchctl is real and on the sandbox PATH; refuse to run
        # anything unless every prerequisite resolves to a shim.
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

    def commands(self):
        """The two hook commands the installer must have written."""
        return hook_command(self.venv_python, "speak"), hook_command(self.venv_python, "toggle")

    def nothing_written(self):
        assert not (self.home / ".claude").exists()
        assert not self.bin.exists()
        assert not (self.home / "Library").exists()
        assert self.sandbox.calls_to("launchctl") == []
        assert self.sandbox.calls_to("uv") == []


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
        if name.endswith(".sh"):
            # the installed shim carries the venv interpreter, so it needs nothing on PATH
            expected = (installer.source / "hooks" / name).read_text().replace(SHIM_LITERAL, str(installer.venv_python))
            assert dst.read_text() == expected, name
            assert os.access(dst, os.X_OK), name
        else:
            assert filecmp.cmp(installer.source / "hooks" / name, dst, shallow=False), name
    assert sorted(p.name for p in installer.hooks.iterdir()) == src


def test_commands_land_executable_in_home_bin(installer):
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    for name in COMMANDS:
        dst = installer.bin / name
        expected = (installer.source / "bin" / name).read_text().replace(SHIM_LITERAL, str(installer.venv_python))
        assert dst.read_text() == expected, name
        assert os.access(dst, os.X_OK), name


def test_talkback_wrapper_lands_in_home_bin(installer):
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    wrapper = installer.bin / "talkback"
    assert os.access(wrapper, os.X_OK)
    lines = [ln for ln in wrapper.read_text().splitlines() if ln and not ln.startswith("#")]
    assert lines == [f'exec "{installer.venv_python}" -m talkback "$@"']


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
        ["pip", "install", "--python", str(venv_python), "-r", str(installer.source / "engine" / "requirements.txt"), "-q"],
        ["pip", "install", "--python", str(venv_python), "-q", str(installer.source)],
    ]


def test_onnx_extra_is_installed_only_when_asked_for_at_install_time(installer):
    r = installer.run(env=dict(installer.env, TALKBACK_ENGINE="onnx"))
    assert r.returncode == 0, r.stdout + r.stderr
    pip_calls = installer.uv_calls("pip")
    assert pip_calls[1] == ["pip", "install", "--python", str(installer.venv_python), "-q", f"{installer.source}[onnx]"]


def test_missing_spacy_model_is_installed_from_the_wheel_url(installer):
    # An existing venv whose python fails `import spacy; spacy.load(...)`.
    venv_python = installer.engine / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    _write_exec(venv_python, "#!/bin/bash\nexit 1\n")
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert installer.uv_calls("venv") == []
    pip_calls = installer.uv_calls("pip")
    assert len(pip_calls) == 3
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
    assert "__HOME__" not in text and "__PORT__" not in text
    assert str(installer.home) in text
    plist = plistlib.loads(installer.plist.read_bytes())
    assert plist["Label"] == "com.icg.talkback"
    assert plist["ProgramArguments"] == [str(installer.venv_python), "-m", "talkback", "server"]
    assert plist["EnvironmentVariables"] == {"KOKORO_PORT": installer.env["KOKORO_PORT"]}
    assert plist["WorkingDirectory"] == str(installer.engine)
    assert os.path.exists(plist["ProgramArguments"][0])


def test_plist_carries_the_port(installer):
    port = free_port()
    r = installer.run(env=dict(installer.env, KOKORO_PORT=str(port)))
    assert r.returncode == 0, r.stdout + r.stderr
    plist = plistlib.loads(installer.plist.read_bytes())
    assert plist["EnvironmentVariables"] == {"KOKORO_PORT": str(port)}


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
    stop_cmd, prompt_cmd = installer.commands()
    settings = json.loads(installer.settings.read_text())
    assert set(settings["hooks"]) == {"Stop", "UserPromptSubmit"}
    assert _hook_commands(settings, "Stop") == [stop_cmd]
    assert _hook_commands(settings, "UserPromptSubmit") == [prompt_cmd]
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
    stop_cmd, prompt_cmd = installer.commands()
    after = json.loads(installer.settings.read_text())
    assert after["model"] == "opus"
    assert after["permissions"] == before["permissions"]
    assert after["hooks"]["PreToolUse"] == before["hooks"]["PreToolUse"]
    assert _hook_commands(after, "Stop") == ["/usr/local/bin/other-stop-hook", stop_cmd]
    assert _hook_commands(after, "UserPromptSubmit") == [prompt_cmd]


def test_settings_with_an_emptied_hook_list_still_get_registered(installer):
    # Claude Code leaves `"Stop": []` behind when every Stop hook is removed.
    installer.settings.parent.mkdir(parents=True)
    installer.settings.write_text(json.dumps({"hooks": {"Stop": []}}))
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    stop_cmd, prompt_cmd = installer.commands()
    after = json.loads(installer.settings.read_text())
    assert _hook_commands(after, "Stop") == [stop_cmd]
    assert _hook_commands(after, "UserPromptSubmit") == [prompt_cmd]


def test_second_run_replaces_the_old_shell_hook_commands(installer):
    # A Stage 1 install registered the shell hooks by path. Upgrading must
    # rewrite those entries where they stand — never add a second Stop hook.
    old_hooks = installer.home / ".claude" / "automation" / "notifications"
    before = {
        "hooks": {
            "Stop": [{"matcher": "", "hooks": [{"type": "command", "command": f"{old_hooks}/speak_last_reply.sh"}]}],
            "UserPromptSubmit": [{"matcher": "", "hooks": [{"type": "command", "command": f"{old_hooks}/tts_toggle.sh"}]}],
        }
    }
    installer.settings.parent.mkdir(parents=True)
    installer.settings.write_text(json.dumps(before))
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert "replaced Stop" in r.stdout
    assert "replaced UserPromptSubmit" in r.stdout
    assert "added" not in r.stdout
    stop_cmd, prompt_cmd = installer.commands()
    after = json.loads(installer.settings.read_text())
    assert _hook_commands(after, "Stop") == [stop_cmd]
    assert _hook_commands(after, "UserPromptSubmit") == [prompt_cmd]


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
    assert "replaced" not in second.stdout
    assert "building the python environment" not in second.stdout
    assert len(installer.uv_calls("venv")) == 1
    stop_cmd, prompt_cmd = installer.commands()
    settings = json.loads(installer.settings.read_text())
    assert _hook_commands(settings, "Stop") == [stop_cmd]
    assert _hook_commands(settings, "UserPromptSubmit") == [prompt_cmd]
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
@pytest.mark.parametrize("tool,message", [("uv", "Missing uv"), ("espeak-ng", "Missing espeak-ng")])
def test_missing_prerequisite_aborts_before_changing_anything(installer, tool, message):
    (installer.sandbox.shims / tool).unlink()
    r = installer.run(env=installer.env_without_system_dirs())
    assert r.returncode != 0
    assert message in r.stdout + r.stderr
    installer.nothing_written()


def test_jq_and_ffmpeg_are_no_longer_required(installer, source):
    text = (source / "install.sh").read_text()
    assert "jq" not in text
    assert "ffplay" not in text and "ffmpeg" not in text
