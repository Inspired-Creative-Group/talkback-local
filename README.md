# Talkback

**Your coding agent, out loud.**

Claude Code speaks its replies in a voice running entirely on your own machine.
No API, no quota, no per-word cost.

Built because reading a terminal is impossible when your hands are busy and your
eyes are somewhere else — filming, editing, across the room. Talkback turns the
agent into something you can listen to and interrupt.

---

## Five words

Type any of these as a whole message:

| | |
|---|---|
| `TTS on` | this session starts speaking |
| `TTS off` | it stops, and goes quiet immediately |
| `shush` | cut it off mid-sentence |
| `replay` | hear the last answer again, from disk |
| `recmode` | which sessions are speaking |

They are caught before the model ever sees them, so they cost no turn and no
tokens. `shush` lands in well under a second — it never has to wait for the
agent to think.

Speaking is armed **per session**. One terminal talks; the others stay silent.

---

## Why it runs locally

The first version used a cloud voice. It sounded excellent and burned a month of
credits in two days — roughly a million characters of ordinary working sessions.
Reading every reply aloud is simply a lot of speech.

[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) is 82 million parameters,
Apache-2.0, and on an M4 it generates about **thirteen seconds of speech per
second of compute**. Held resident in memory, it answers faster than the cloud
call it replaced, because nothing leaves the machine.

Cost per reply: zero. Quota: none. Works on a plane.

---

## Install

Requires macOS on Apple Silicon.

```bash
brew install ffmpeg espeak-ng jq          # plus uv — https://docs.astral.sh/uv/
git clone https://github.com/Inspired-Creative-Group/icg-talkback
cd icg-talkback && ./install.sh
```

Then type `TTS on` in any Claude Code session.

The installer builds the Python environment, places the engine and hooks,
registers a launch agent so the model is warm before you open a terminal, and
adds the two hooks to `~/.claude/settings.json`. Re-run it to upgrade.

---

## How it works

**A resident server.** Loading the model costs 3.7 seconds — per reply that
would be worse than any cloud API. A launch agent loads it once at login and it
stays in memory.

**Two hooks.** One watches for the five commands and answers them without
involving the model. The other fires when a turn ends, reads the transcript,
extracts the reply, and speaks it.

**Sentence by sentence.** A long reply takes several seconds to synthesize in
full, but the first sentence is ready in about 0.6. Talkback plays each sentence
while the next is still being generated, so the first words land in about a
second regardless of length.

---

## Written for the ear, not the eye

An agent's reply is full of things nobody wants read aloud. Before synthesis:

- URLs and bare domains become **"a link"**
- email addresses become **"an email address"**
- long hashes and API keys become **"an I D"** — not forty spelled-out characters
- clock times become spoken words: **"nine o'clock"**, not "zero nine hundred"
- markdown, code blocks and file paths are stripped

Substitutions replace rather than delete, so sentences stay grammatical.
"Open a link and check it" is something you can say out loud. A gap is not.

---

## Your own voice

`engine/icg_voice.pt` is a blend of two Kokoro stock voices — 65% `af_heart`,
35% `af_sarah`. Blending gives you a voice no other Kokoro user has.

Four lines make a different one; see [VOICE.md](VOICE.md). There are 28 English
voices to start from, and more in other languages.

---

## Notes from two days of debugging

The code is 542 lines. The knowledge below is the part that took the time.

- **`ffplay` has no `-ac` flag.** It is `-ch_layout`. The wrong flag makes ffplay
  exit instantly, which SIGPIPEs the pipeline and leaves a truncated file —
  a symptom that looks exactly like a network failure.
- **`mpv` renders mono as a single channel**, and macOS puts one channel in the
  left ear. `ffplay` asks CoreAudio for stereo and centres it. mpv's own
  `--audio-channels=stereo` declares the layout without upmixing the signal.
- **Never pipe a faster-than-realtime generator into a player.** ffplay reads a
  stream arriving faster than realtime as one it has fallen behind on, and skips
  ahead to catch up — silently eating the first several seconds. Play finished
  files instead.
- **An API error is a 200 response with a JSON body.** Without a check, a quota
  message gets written to disk as an `.mp3` and logged as success. You get
  silence and no explanation. `verify_audio.sh` exists because of that.
- **`spacy download` silently fails inside a uv venv.** Install the model wheel
  by URL.
- **Python can't run the model and feed the audio device at once.** Generation
  alone: 0.9s. With playback in the same process: 28s. The player belongs in
  its own process.
- **Instrument the part you are not suspecting.** A leftover second playback in
  the tail of a script made every reply play through twice, while three separate
  traces insisted it played once — because none of them covered that line. What
  found it was tagging each synthesis with a spoken random number: the same
  number twice meant one generation played twice, and that ended the search in
  one listen.

---

## Licence

MIT — see [LICENSE](LICENSE). Kokoro-82M is Apache-2.0.

Built at [Inspired Creative Group](https://github.com/Inspired-Creative-Group).
