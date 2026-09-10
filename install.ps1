# UNTESTED - written for Windows 10/11, awaiting a Windows tester; see CONTRIBUTING.md
#
# Talkback Local installer for Windows. Idempotent - safe to re-run to upgrade.
# Run from a PowerShell prompt in the checkout:
#     powershell -ExecutionPolicy Bypass -File install.ps1
#
# This file is plain ASCII on purpose: Windows PowerShell 5.1 reads a file
# without a byte-order mark as ANSI, and any non-ASCII text would print as
# garbage. PowerShell 7 reads it as UTF-8; both parse it the same way.
#
# What it does, in order (the same shape as install.sh):
#   1. refuses without Git for Windows - Claude Code runs its hooks under Git
#      Bash, so without it the hooks never fire - and without uv
#   2. checks KOKORO_PORT (default 8910) is free or already ours, before any
#      file is written
#   3. builds the engine venv under ~\.claude\automation\kokoro\.venv and
#      installs this package with the onnx extra (no PyTorch, no espeak-ng
#      install: kokoro-onnx bundles espeak through espeakng-loader)
#   4. downloads the two onnx model files when they are absent
#   5. persists TALKBACK_ENGINE=onnx (and KOKORO_PORT when non-default) as
#      user environment variables, so the hooks and the launcher agree
#   6. registers the two hooks in ~\.claude\settings.json as plain
#      "<venv python>" -m talkback speak|toggle commands, forward slashes
#   7. drops a .vbs launcher in the Startup folder that runs the server
#      hidden (pythonw.exe) at login, and starts it once now
#   8. verifies every file landed before it says so

$ErrorActionPreference = "Stop"
# Native commands (uv, python) report through $LASTEXITCODE, checked by hand
# below; keep PowerShell 7 from turning their exit codes into exceptions.
$PSNativeCommandUseErrorActionPreference = $false
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = if ($env:KOKORO_PORT) { [int]$env:KOKORO_PORT } else { 8910 }
$Automation = Join-Path $HOME ".claude\automation"
$Engine = Join-Path $Automation "kokoro"
$VenvPy = Join-Path $Engine ".venv\Scripts\python.exe"
$VenvPyw = Join-Path $Engine ".venv\Scripts\pythonw.exe"
$Startup = [Environment]::GetFolderPath("Startup")
$Launcher = Join-Path $Startup "talkback.vbs"
$Settings = Join-Path $HOME ".claude\settings.json"
$ModelBase = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"
$ModelFiles = @("kokoro-v1.0.onnx", "voices-v1.0.bin")

function Say([string]$Text) { Write-Host "  $Text" }
function Fail([string]$Text) { Write-Host $Text; exit 1 }

# -- 1. prerequisites -----------------------------------------------------
$GitMissing = "Missing Git for Windows. Claude Code runs its hooks under Git Bash - install it from https://git-scm.com/download/win and re-run."
$Git = Get-Command git -ErrorAction SilentlyContinue
if (-not $Git) { Fail $GitMissing }
# git.exe lives in <root>\cmd, <root>\bin or <root>\mingw64\bin; bash.exe is
# <root>\bin\bash.exe. Walk up from git.exe until a bin\bash.exe appears.
$Bash = $null
$Dir = Split-Path -Parent $Git.Source
while ($Dir -and -not $Bash) {
    $Candidate = Join-Path $Dir "bin\bash.exe"
    if (Test-Path $Candidate) { $Bash = $Candidate }
    $Dir = Split-Path -Parent $Dir
}
if (-not $Bash) { Fail "$GitMissing (git was found at $($Git.Source) but no bin\bash.exe beside it.)" }
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Fail "Missing uv. See https://docs.astral.sh/uv/"
}

# -- 2. the port, before anything is written -----------------------------
# The engine venv does not exist yet on a first run, so the installer's own
# Python half runs on a throwaway uv interpreter straight from the checkout.
# On a refusal the Python side has already printed why (the same text
# install.sh shows: "Port N is in use by something that is not Talkback").
$PortState = & uv run --isolated --no-project --python 3.12 --directory "$Here" python -m talkback.install port $Port
if ($LASTEXITCODE -ne 0) { exit 1 }
if ($PortState -eq "talkback") { Say "a Talkback server already answers on port $Port - upgrading in place" }

# -- 3. the engine environment and the package ---------------------------
Say "engine -> $Engine"
foreach ($d in @($Engine, (Join-Path $Automation "recording"), (Join-Path $Automation "lastreply"), (Join-Path $Automation "notifications"))) {
    New-Item -ItemType Directory -Force -Path $d | Out-Null
}
if (-not (Test-Path (Join-Path $Engine ".venv"))) {
    Say "building the python environment (about a minute)"
    & uv venv --python 3.12 (Join-Path $Engine ".venv") | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "uv venv failed" }
}
& uv pip install --python "$VenvPy" -q "${Here}[onnx]"
if ($LASTEXITCODE -ne 0) { Fail "package install failed" }

# -- 4. the onnx model files ---------------------------------------------
foreach ($name in $ModelFiles) {
    $target = Join-Path $Engine $name
    if (-not (Test-Path $target)) {
        Say "downloading $name"
        Invoke-WebRequest -Uri ($ModelBase + $name) -OutFile $target
    }
}

# -- 5. the environment the hooks and the launcher read at login ---------
[Environment]::SetEnvironmentVariable("TALKBACK_ENGINE", "onnx", "User")
if ($Port -ne 8910) {
    [Environment]::SetEnvironmentVariable("KOKORO_PORT", "$Port", "User")
}
$env:TALKBACK_ENGINE = "onnx"
$env:KOKORO_PORT = "$Port"

# -- 6. the Claude Code hooks --------------------------------------------
# Hook commands are written with forward slashes (the settings verb uses
# as_posix), which Git Bash - the shell Claude Code runs hooks under on
# Windows - reads as-is; the same conversion here keeps the two in step.
$VenvPyPosix = $VenvPy -replace '\\', '/'
Say "registering the Claude Code hooks"
& "$VenvPy" -m talkback.install settings "$VenvPyPosix"
if ($LASTEXITCODE -ne 0) { Fail "settings.json registration failed" }

# -- 7. the Startup-folder launcher --------------------------------------
Say "startup launcher -> $Launcher"
& "$VenvPy" -m talkback.install render (Join-Path $Here "windows\talkback-startup.vbs.template") "$Launcher" "VENV_PYTHONW=$VenvPyw"
if ($LASTEXITCODE -ne 0) { Fail "launcher rendering failed" }
if ($PortState -ne "talkback") {
    Start-Process -FilePath "wscript.exe" -ArgumentList "`"$Launcher`"" | Out-Null
}

# -- 8. verify rather than assume ----------------------------------------
$Missing = @()
foreach ($f in @($VenvPy, $VenvPyw, (Join-Path $Engine "kokoro-v1.0.onnx"), (Join-Path $Engine "voices-v1.0.bin"), $Launcher, $Settings)) {
    if (-not (Test-Path $f)) { $Missing += $f }
}
if ($Missing.Count -gt 0) {
    Write-Host "  INSTALL INCOMPLETE - missing:"
    foreach ($f in $Missing) { Write-Host "    $f" }
    exit 1
}

Say ""
Say "Installed. Wait ~10s for the model to load, then in any Claude Code session:"
Say "    TTS on     start speaking      TTS off   stop"
Say "    shush      cut it off          replay    hear it again"
Say "    recmode    which sessions are speaking"
Say "The terminal commands run through the venv: $VenvPy -m talkback <shush|replay|recmode|server>"
