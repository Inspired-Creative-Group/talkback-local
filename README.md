# Talkback

**Your coding agent, out loud.**

Claude Code speaks its replies in a voice that runs entirely on your own machine.
No API key, no quota, no per-word cost, no audio ever leaving the computer.

Built because reading a terminal is impossible when your hands are busy and your
eyes are somewhere else. Talkback turns the agent into something you can listen
to while you work — and interrupt the moment it stops being useful.

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
tokens. `shush` lands in well under a second — it never waits for the agent to
finish thinking.

Speaking is armed **per session**. One terminal talks; the others stay silent.

---

## Requirements

| | |
|---|---|
| **OS** | macOS. Playback and the startup item are Apple-specific. |
| **Chip** | Apple Silicon — M1 or newer. Intel Macs are untested; the model will fall back to CPU. |
| **Memory** | **16 GB recommended.** The server holds the model resident at roughly 4 GB. It will run on 8 GB, but alongside a browser and an editor it will lean on swap. |
| **Disk** | About 1.2 GB — 900 MB Python environment, 320 MB model. |
| **Tools** | `ffmpeg`, `espeak-ng`, `jq`, and [uv](https://docs.astral.sh/uv/). |

**Speed.** On an M4 Max the model generates roughly **thirteen seconds of speech
per second of compute** on an idle machine, and about **two to three times
realtime** when the machine is genuinely busy. Anything above realtime is enough
— playback starts on the first sentence while the rest is still being made. An
M1 or M2 will be slower than the figures above and still comfortable.

---

## Install

```bash
brew install ffmpeg espeak-ng jq
curl -LsSf https://astral.sh/uv/install.sh | sh     # if you don't have uv

git clone https://github.com/Inspired-Creative-Group/icg-talkback
cd icg-talkback && ./install.sh
```

Then type `TTS on` in any Claude Code session.

### First run is slow — this is normal

The installer sets up code and dependencies but **does not download the voice
model**. The first time the server starts it fetches Kokoro-82M from Hugging Face
— about **320 MB**, once. Expect a minute or two of apparent silence on that
first start. It is cached permanently after that and never downloads again, so
every later start is instant and the whole thing works offline.

If your first `TTS on` produces nothing, give it a couple of minutes and check
`~/.claude/automation/kokoro/server.log`.

### Upgrading

Re-run `./install.sh`. It is idempotent — safe to run over an existing install.

---

## Why local

The first version of this used a cloud voice. It sounded excellent and burned a
month of credits in two days — roughly a million characters of ordinary working
sessions. Reading every reply aloud is simply a lot of speech.

[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) is 82 million parameters
and Apache-2.0 licensed. Held resident in memory it answers faster than the cloud
API it replaced, because nothing leaves the machine.

Cost per reply: zero. Quota: none. Works on a plane.

---

## How it works

**A resident server.** Loading the model costs about four seconds — per reply
that would be worse than any cloud API. A launch agent loads it once at login and
it stays in memory.

**Two hooks.** One watches for the five commands and answers them without
involving the model at all. The other fires when a turn ends, reads the session
transcript, extracts the reply, and speaks it.

**Sentence by sentence.** A long reply takes several seconds to synthesize in
full, but the first sentence is ready in well under one. Talkback plays each
sentence while the next is still being generated, so the first words land almost
immediately regardless of how long the answer is.

---

## Written for the ear, not the eye

An agent's reply is full of things nobody wants read aloud. Before synthesis:

- URLs and bare domains become **"a link"**
- email addresses become **"an email address"**
- long hashes and API keys become **"an I D"** — not forty spelled-out characters
- clock times become spoken words: **"nine o'clock"**, not "zero nine hundred"
- markdown, code blocks and file paths are stripped

Substitutions replace rather than delete, so sentences stay grammatical. "Open a
link and check it" is something you can say out loud. A gap is not.

---

## Choosing a voice

The bundled voice is a blend of two Kokoro stock voices — 65% `af_heart`, 35%
`af_sarah`. Blending gives you a voice no other Kokoro user has.

Four lines of Python make a different one; see [VOICE.md](VOICE.md). There are 28
English voices to start from and more in other languages, and any two can be
mixed at any ratio.

---

## Notes from two days of debugging

The code is 542 lines. Everything below took far longer than writing it, and is
the part worth reading if you are building something similar.

- **`ffplay` has no `-ac` flag.** It is `-ch_layout`. The wrong flag makes ffplay
  exit instantly, which SIGPIPEs the pipeline and leaves a truncated file — a
  symptom that looks exactly like a network failure.
- **`mpv` renders mono as a single channel**, and macOS puts one channel in the
  left ear only. `ffplay` asks CoreAudio for stereo and centres it. mpv's own
  `--audio-channels=stereo` declares the layout without upmixing the signal.
- **Never pipe a faster-than-realtime generator into a player.** ffplay reads a
  stream arriving faster than realtime as one it has fallen behind on, and skips
  ahead to catch up — silently eating the first several seconds. Play finished
  files instead.
- **An API error is a 200 response with a JSON body.** Without a check, a quota
  message gets written to disk as an `.mp3` and logged as a success. You get
  silence and no explanation. `verify_audio.sh` exists because of that.
- **`spacy download` silently fails inside a uv venv.** Install the model wheel
  by URL instead.
- **Python can't run the model and feed the audio device at once.** Generation
  alone: 0.9s. With playback in the same process: 28s. The player belongs in its
  own process.
- **Never pass a credential as a command-line argument.** `ps` is world-readable.
- **Instrument the output, not the code.** A leftover second playback made every
  reply play through twice while three separate traces insisted it played once —
  none of them covered that line. What found it was tagging each synthesis with a
  spoken random number: the same number twice meant one generation played twice,
  and that ended the search in a single listen.

---

## Licence

MIT — see [LICENSE](LICENSE). Kokoro-82M is Apache-2.0.

---

Built at **[Inspired Creative Group](https://github.com/Inspired-Creative-Group)**,
an AI production studio in Nova Scotia. We build tools like this because we use
them. If it is useful to you, an issue or a star is welcome.
