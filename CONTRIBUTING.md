# Contributing

Contributions are welcome, and small ones especially.

## Useful things to send

- **A voice blend you like.** Two Kokoro voices at a ratio, and what it sounds
  good for. See [VOICE.md](VOICE.md).
- **Another platform.** Playback and the startup item are macOS-specific. Linux
  and Windows are perfectly possible and nobody has done them.
- **Text rewriting rules.** The pre-synthesis pass turns URLs and clock times
  into something sayable. There will be shapes it still reads badly — dates and
  version numbers are known gaps.
- **A bug with a recording.** If the voice does something wrong, a few seconds of
  audio says more than a paragraph.

## Before you open a PR

There is no build and no test suite. What matters:

1. `bash -n` on any shell script, and `python3 -m py_compile` on any Python.
2. Actually run it — `TTS on`, ask something, listen to the whole reply.
3. Try `shush` mid-sentence and `replay` afterwards. Both break easily.

Watch the log at `~/.claude/automation/notifications/speak.log`; it records one
playback per reply. Two means something is wrong.

## Things worth knowing first

The "Notes from two days of debugging" section of the README is not decoration —
`ffplay` flags, mono channel handling, and why the audio is never piped live into
a player are all load-bearing decisions that look arbitrary until they bite.

## Reporting something sensitive

If you find a security issue, email inspiredcreativegroupinc@gmail.com rather
than opening an issue.
