"""install.ps1 on a Mac: what can be checked without Windows. The structural
checks are pure; the parse check runs only when ``pwsh`` (PowerShell 7) is on
PATH, parse-only through the PowerShell language parser — nothing in the
script is executed. Without pwsh it skips and says why; the windows-latest CI
job runs the same parse on every push."""

import shutil
import subprocess

import pytest


@pytest.fixture
def ps1_text(repo):
    return (repo / "install.ps1").read_text(encoding="ascii")


@pytest.mark.pure
def test_install_ps1_exists_with_the_untested_header(repo, ps1_text):
    assert (repo / "install.ps1").is_file()
    assert ps1_text.splitlines()[0].startswith("# UNTESTED")


@pytest.mark.pure
def test_install_ps1_detects_git_for_windows(ps1_text):
    assert "Get-Command git" in ps1_text
    assert "bash.exe" in ps1_text
    assert "Missing Git for Windows" in ps1_text


@pytest.mark.pure
def test_install_ps1_uses_the_startup_folder(ps1_text):
    assert '[Environment]::GetFolderPath("Startup")' in ps1_text
    assert "talkback.vbs" in ps1_text


@pytest.mark.pure
def test_install_ps1_registers_forward_slash_hook_commands(ps1_text):
    assert "$VenvPy -replace '\\\\', '/'" in ps1_text
    assert '-m talkback.install settings "$VenvPyPosix"' in ps1_text


@pytest.mark.pure
def test_install_ps1_checks_the_port(ps1_text):
    assert "-m talkback.install port $Port" in ps1_text
    assert "KOKORO_PORT" in ps1_text


def test_install_ps1_parses_under_powershell(repo):
    pwsh = shutil.which("pwsh")
    if not pwsh:
        pytest.skip("pwsh (PowerShell 7) is not on PATH; the windows-latest CI job runs this parse")
    script = (
        "$e = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{repo / 'install.ps1'}', [ref]$null, [ref]$e) | Out-Null; "
        "if ($e) { $e | ForEach-Object { Write-Output $_.Message }; exit 1 }"
    )
    r = subprocess.run([pwsh, "-NoProfile", "-Command", script], text=True, capture_output=True, timeout=60, check=False)
    assert r.returncode == 0, r.stdout + r.stderr
