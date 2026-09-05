# Talkback

Your coding agent, out loud.

Claude Code speaks its replies through a voice running entirely on your own
machine. No API, no quota, no per-word cost. Built because reading a terminal is
impossible when your hands are busy and your eyes are on a camera.

```
TTS on      start speaking          TTS off    stop
shush       cut it off mid-sentence replay     hear the last answer again
recmode     which sessions speak
```

Type those as a whole message. They are caught before the model sees them, so
they cost no turn and no tokens — `shush` lands in well under a second.

## Why local

The first version used a cloud voice. It sounded excellent and burned a month's
credits in two days — around a million characters of ordinary working sessions.
Kokoro-82M runs on Apple Silicon faster than realtime, is Apache-2.0, and costs
nothing. On an M4 it generates about thirteen seconds of speech per second of
compute.

## Install

The ElevenLabs fallback engine needs `TTS_VOICE_ID` set to a voice of your own;
the local engine ignores it entirely.

Needs macOS, Apple Silicon, and `brew install ffmpeg espeak-ng jq` plus
[uv](https://docs.astral.sh/uv/).

```bash
git clone <this repo> && cd icg-talkback && ./install.sh
```

Then `TTS on` in any Claude Code session. Re-run `install.sh` to upgrade.

## How it works

A resident server holds the model in memory — loading it per reply costs 3.7
seconds, which is worse than the cloud call it replaced. A launch agent starts
it at login, so no reply ever waits for it.

When a turn ends, a Stop hook reads the session transcript, extracts the reply,
rewrites it for the ear, splits it into sentences, and plays each one while the
next is still being generated. First words land in about a second even on a long
answer.

Speaking is armed **per session**, so one terminal talks and the others stay
silent.

## Written for the ear, not the eye

Before synthesis the text is rewritten: URLs become "a link", emails become "an
email address", long hashes become "an I D", and clock times become spoken words
— "nine o'clock", not "zero nine hundred". Markdown, code blocks and file paths
are stripped. Nobody wants a forty-character API key read aloud.

## Notes from two days of debugging

Things that cost hours and are cheap to know:

- **`ffplay` has no `-ac` flag.** It is `-ch_layout`. The wrong flag makes it
  exit instantly, which SIGPIPEs the pipeline and leaves a truncated file that
  looks exactly like a network failure.
- **`mpv` renders mono as one channel**, and macOS puts a single channel in the
  left ear. `ffplay` asks CoreAudio for stereo and centres it. Its own
  `--audio-channels=stereo` declares the layout without upmixing.
- **Don't pipe a fast generator into `ffplay`.** It reads a stream arriving
  faster than realtime as one it has fallen behind on, and skips ahead — eating
  the first several seconds. Play finished files instead.
- **An API error is a 200 response with a JSON body.** Without a check, a quota
  message gets saved as an `.mp3` and logged as success, and you get silence with
  no explanation. `verify_audio.sh` exists because of that.
- **`spacy download` silently fails inside a uv venv.** Install the model wheel
  by URL.
- **Instrument the part you are not suspecting.** A leftover second playback in
  the tail of a script made every reply play twice while three separate traces
  said it played once — because none of them covered that line.

## Licence

MIT. Kokoro-82M is Apache-2.0.
