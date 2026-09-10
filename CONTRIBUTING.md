# Contributing

Contributions are welcome, and small ones especially.

## Useful things to send

- **A voice blend you like.** Two Kokoro voices at a ratio, and what it sounds
  good for. See [VOICE.md](VOICE.md).
- **A Windows test run.** The hooks and the player are plain Python now and the
  installer for Windows exists, but nobody has run it on Windows yet. See
  "Testing on Windows" below — it is the most useful thing a Windows user can
  send.
- **Linux.** Nothing is macOS-specific any more except the launch agent and the
  desktop notification; a systemd user unit would complete it.
- **Text rewriting rules.** The pre-synthesis pass turns URLs and clock times
  into something sayable. There will be shapes it still reads badly — dates and
  version numbers are known gaps.
- **A bug with a recording.** If the voice does something wrong, a few seconds of
  audio says more than a paragraph.

## Before you open a PR

There is no build. There is a test suite, and CI runs it on every pull request:

1. `pip install -r requirements-dev.txt && pytest -q` — or, with `uv`,
   `uv run --isolated --no-project --python 3.12 --with-requirements requirements-dev.txt -- pytest -q`.
   The tests never touch your live install: they run under a throwaway `HOME`
   with a fake engine and a fake `sounddevice`. `ruff check .`, `shellcheck` and
   `bash -n` on the shell scripts should also be clean; CI checks all four on
   macOS, and runs the pure-Python tests (`pytest -q -m pure`) plus a parse of
   `install.ps1` on Windows.
2. Actually run it — `TTS on`, ask something, listen to the whole reply. The
   suite proves the plumbing, not the sound.
3. Try `shush` mid-sentence and `replay` afterwards. Both break easily.

Watch the log at `~/.claude/automation/notifications/speak.log`; it records one
playback per reply. Two means something is wrong.

## Things worth knowing first

The "Notes from two days of debugging" section of the README is not decoration —
stereo channel handling, why playback is one detached process per reply, and why
the audio is never piped live into a player are all load-bearing decisions that
look arbitrary until they bite.

## Testing on Windows

`install.ps1` is written but **untested** — it has never run on a Windows
machine. CI proves only that it parses and that the pure-Python logic passes on
`windows-latest`. If you have Windows 10 or 11, this is what to run and what to
send back. Budget half an hour; the model download is about 300 MB.

Before you start you need [Git for Windows](https://git-scm.com/download/win)
(Claude Code runs its hooks under Git Bash — without it nothing fires),
[uv](https://docs.astral.sh/uv/), and Claude Code itself. No PyTorch, no
espeak-ng: the Windows route uses the `kokoro-onnx` engine, which bundles both.

1. Clone the repo and, in PowerShell, run
   `powershell -ExecutionPolicy Bypass -File install.ps1`. Copy everything it
   prints, including a failure — the message and the line it fails on are the
   report.
2. Open a new terminal (the installer sets user environment variables that a
   running shell does not see) and run
   `%USERPROFILE%\.claude\automation\kokoro\.venv\Scripts\python.exe -m talkback server status`.
   It should say `running on 8910`. If it says `not running`, send
   `%USERPROFILE%\.claude\automation\kokoro\server.log`.
3. Start Claude Code, type `TTS on` as the whole message, then ask it anything.
   Listen for three things: does it speak at all; does the voice come from both
   speakers or just one ear; does the whole reply play.
4. Type `shush` while it is talking, then `replay` after it stops.
5. Send `%USERPROFILE%\.claude\automation\notifications\speak.log` — it records
   one `PLAY start` / `PLAY done` pair per reply, and every failure — together
   with your Windows version, the PowerShell version (`$PSVersionTable.PSVersion`)
   and whether Claude Code ran in Windows Terminal, PowerShell or Git Bash.

Also worth a line: does the Startup-folder launcher (`talkback.vbs`) bring the
engine back after a reboot, and did `install.ps1` behave when run a second time.
Open an issue with the "Something is wrong with the voice" template, or a PR if
you fixed it.

## Reporting something sensitive

If you find a security issue, email inspiredcreativegroupinc@gmail.com rather
than opening an issue.
