"""The Windows files, checked as far as macOS can: install.ps1's shape and the
promises it must keep (the spec's list), and the Startup launcher template
rendered through the same ``render`` the installer uses.

Pure: file reads and function calls. Real PowerShell parsing happens in the
windows-latest CI job (and in test_install_ps1 when ``pwsh`` is around)."""

import re

import pytest

from talkback import install

pytestmark = pytest.mark.pure

MUST_CONTAIN = (
    "Get-Command git",
    "bash.exe",
    "-m talkback.install port",
    "-m talkback.install settings",
    '[Environment]::GetFolderPath("Startup")',
    '[Environment]::SetEnvironmentVariable("TALKBACK_ENGINE"',
    "kokoro-v1.0.onnx",
    "voices-v1.0.bin",
    "[onnx]",
    "talkback-startup.vbs.template",
)


@pytest.fixture
def ps1(repo):
    return (repo / "install.ps1").read_bytes()


def test_install_ps1_exists_as_plain_ascii_with_lf_line_endings(ps1):
    assert ps1  # not empty
    assert not ps1.startswith(b"\xef\xbb\xbf"), "no UTF-8 BOM"
    assert b"\r" not in ps1, "LF line endings"
    # ASCII only: Windows PowerShell 5.1 reads a BOM-less file as ANSI, so
    # anything beyond ASCII would print as garbage in the messages.
    assert all(b < 0x80 for b in ps1), "non-ASCII byte in install.ps1"


def test_install_ps1_header_says_untested(ps1):
    first = ps1.decode("ascii").splitlines()[0]
    assert first.startswith("# UNTESTED")
    assert "Windows tester" in first
    assert "CONTRIBUTING.md" in first


def test_install_ps1_braces_are_balanced(ps1):
    text = ps1.decode("ascii")
    # strings and comments can hold braces; strip them before counting
    stripped = re.sub(r'"(?:`.|[^"`])*"|\'[^\']*\'|#[^\n]*', "", text)
    assert stripped.count("{") == stripped.count("}")
    assert stripped.count("(") == stripped.count(")")


@pytest.mark.parametrize("needle", MUST_CONTAIN)
def test_install_ps1_keeps_its_promises(ps1, needle):
    assert needle in ps1.decode("ascii"), needle


def test_install_ps1_refuses_without_git_for_windows(ps1):
    text = ps1.decode("ascii")
    assert "Missing Git for Windows. Claude Code runs its hooks under Git Bash" in text
    assert "https://git-scm.com/download/win" in text
    assert "Missing uv." in text


def test_install_ps1_writes_forward_slash_hook_commands(ps1):
    text = ps1.decode("ascii")
    assert "$VenvPy -replace '\\\\', '/'" in text
    assert "-m talkback.install settings \"$VenvPyPosix\"" in text


def test_install_ps1_checks_the_port_before_writing_anything(ps1):
    text = ps1.decode("ascii")
    port_check = text.index("-m talkback.install port")
    first_write = min(text.index("New-Item"), text.index("uv venv"), text.index("Invoke-WebRequest"))
    assert port_check < first_write


def test_install_ps1_starts_the_server_hidden_from_the_startup_folder(ps1):
    text = ps1.decode("ascii")
    assert "pythonw.exe" in text
    assert "wscript.exe" in text
    assert 'Join-Path $Startup "talkback.vbs"' in text


def test_startup_launcher_template_renders_to_a_hidden_server(repo):
    template = (repo / "windows" / "talkback-startup.vbs.template").read_text(encoding="ascii")
    assert "__VENV_PYTHONW__" in template
    pythonw = r"C:\Users\First Last\.claude\automation\kokoro\.venv\Scripts\pythonw.exe"
    rendered = install.render(template, VENV_PYTHONW=pythonw)
    assert install.unrendered(rendered) == []
    assert "__" not in rendered.replace("__main__", "")
    assert f'.Run """{pythonw}"" -m talkback server", 0, False' in rendered
    assert "\r" not in template
    assert template.splitlines()[0].startswith("'")  # a VBScript comment, and it says so
    assert "UNTESTED" in template.splitlines()[0]
