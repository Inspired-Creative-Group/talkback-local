# Talkback Local

**Local voice for Claude Code. Your coding agent, out loud.**

Offline text-to-speech for Claude Code on macOS — Claude Code reads its replies
aloud in a voice running entirely on your own Apple Silicon Mac. No API key, no
quota, no per-word cost, no audio ever leaving the computer. Hands-free by
design, and a natural pair with voice dictation.

## Why this exists

**A terminal can't talk to you.** You watch it, or you miss it. That's fine when
you're sitting still, and useless the moment your hands are busy, your eyes are on
a camera, or you've walked to the other side of the room.

**The desktop app can read a reply aloud, but you have to ask it to — with a
mouse.** Click a button, wait, listen. It's slow, it's inconsistent, the voice is
whatever you're given, and clicking is exactly the thing you can't do when your
hands are elsewhere. Talkback removes the click: the agent simply speaks, every
time, and you can stop it with a word.

**Pair it with voice input and you never touch the keyboard.** With a dictation
tool — [Wispr Flow](https://wisprflow.ai) or [Sotto](https://sotto.to), neither
of them ours, [compared below](#dictation--separate-tools-not-part-of-talkback) —
you talk to the agent; with Talkback it talks back, and you can cut it off, or
ask it to repeat, by saying so. That is a genuine conversation with your work,
in either the terminal or the desktop app, hands free from end to end.

Everything happens locally. Nothing to click, nothing to configure mid-flow,
nothing sent anywhere.

---

## Five words

Type — or dictate — any of these as a whole message:

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

Because they are single words, they work just as well spoken through a dictation
tool as typed. `shush` said out loud stops the voice in under a second.

---

## Dictation — separate tools, not part of Talkback

**Talkback does not do dictation.** It only speaks; it never listens. To talk to
the agent you need a separate dictation app, installed and paid for on its own.
Two are worth knowing about. Neither is ours, we are not affiliated with either,
and nothing here installs them — we use both, and this is what we found.

| | [Wispr Flow](https://wisprflow.ai) | [Sotto](https://sotto.to) |
|---|---|---|
| **Cost** | Subscription | One-time, up to three Macs |
| **Speech recognition** | Cloud | On-device — Whisper or NVIDIA Parakeet, on the Neural Engine |
| **Cleanup pass** | Cloud, built in | Optional: Apple Intelligence on-device, a local Ollama model, or a cloud provider you supply |
| **Works offline** | No | Yes |
| **Platforms** | macOS, Windows, iOS | macOS |

Wispr Flow is the more finished product and the shorter path if you would rather
not think about any of this. Sotto is the one that matches Talkback's premise —
nothing leaves the machine — and with Apple Intelligence doing the cleanup pass,
the round trip stays short enough to feel immediate.

Either one, plus Talkback, closes the loop: speak, listen, interrupt by saying
`shush`. No keyboard, no mouse.

---

## Requirements

| | |
|---|---|
| **OS** | macOS. Playback and the startup item are Apple-specific. |
| **Chip** | **Apple Silicon** — M1 or newer; measured on an M4. Synthesis runs on the CPU on purpose: Metal was measured and is *slower* for this job (see below). Intel Macs are untested. |
| **Memory** | **16 GB recommended.** The server holds the model resident at roughly 4 GB. 8 GB works in principle but leaves little room beside a browser and an editor. |
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

git clone https://github.com/Inspired-Creative-Group/talkback-local
cd talkback-local && ./install.sh
```

Then type `TTS on` in any Claude Code session.

The installer stops with a clear error rather than half-finishing, and verifies
every file landed before it reports success. It has been tested from scratch
against a clean home directory, including the re-run upgrade path.

### First run is slow — this is normal

The installer sets up code and dependencies but **does not download the voice
model**. The first time the server starts it fetches Kokoro-82M from Hugging Face
— **312 MB**, once.

Measured from a completely cold start on an M4 with an empty cache: the installer
finishes in **29 seconds**, and the server is answering **20 seconds** after that,
download included. Slower connections will take longer, and it is cached
permanently afterwards — every later start is instant, and the whole thing works
offline from then on.

If your first `TTS on` produces nothing, give it a couple of minutes and check
`~/.claude/automation/kokoro/server.log`.

### Upgrading

Re-run `./install.sh`. It is idempotent — safe to run over an existing install.

---

## Why local

**Everything your agent says would have to be sent somewhere.**

A cloud voice works by uploading the text to be spoken. That text is your agent's
replies — which means your file paths, your architecture, your client names, your
credentials when they appear in a diagnosis, your unreleased work. Every answer,
all day, to a third party with its own retention policy and its own breaches to
come.

You have already accepted one such relationship, deliberately, because the model
is the thing you cannot run yourself. That is a considered trade. Adding a second
vendor — for the comparatively simple job of turning text into sound — is not a
trade, it is a leak with no upside.

**So the voice runs on your machine.**
[Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) is 82 million parameters,
Apache-2.0 licensed, and genuinely good — natural prosody, 28 English voices plus
other languages, and any two can be blended into a voice that is yours alone.
After the one-time model download, nothing leaves the computer: no account, no
API key, no telemetry, no log of what your agent told you sitting on someone
else's disk.

What that buys you, in order:

- **Privacy.** The one thing said out loud in your studio stays in your studio.
- **No cost, ever.** Zero per word, per hour, per month. Reading every reply aloud
  is a lot of speech — enough that any metered service becomes a decision you have
  to keep making.
- **Consistency.** A cloud voice varies with network conditions and service load.
  This one behaves identically at nine in the morning and at midnight, on wifi or
  off it. When you are recording, that matters more than raw quality.
- **It cannot be taken away.** No pricing change, no deprecated model, no outage
  during a take, no terms you have to re-read.

It is also faster. Held resident in memory, it answers more quickly than the
network round trip it replaces — roughly thirteen seconds of speech per second of
compute on an idle Apple Silicon machine.

**The principle, not just the feature.** Send out only what genuinely has to
leave. Everything else — your voice, your notes, your memory of the work — stays
where you can see it. A local voice is a small thing on its own, and it is the
right default for every part of a system you would rather own than rent.

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

**On the CPU, not the GPU.** The obvious choice on Apple Silicon is Metal, and
that is what the first release shipped. It was the wrong choice, and it took a
listener to notice: every reply started three to five seconds late, while the
logs insisted on half a second. The logs were timing the warm case. PyTorch's
Metal backend compiles a fresh kernel for every new phrase *length*, about 3.5s
each, and caches it — so a repeated test sentence is fast and a real reply, which
never repeats a length, never is. The same sentences on the M4's CPU take 0.35s,
every time. The server runs on the CPU and the launch agent runs it at
interactive priority so a busy machine does not starve it.

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

**Code is handled, not skipped.** A code block becomes the words *"shown on
screen"* — because you are listening, so the useful thing is being told where to
look, not hearing a bash one-liner spelled out character by character. Inline
code is spoken when it is sayable (`shush`, `main.py`, `TTS on` are often the
most important word in the sentence) and becomes *"a command"* only when it is
genuinely unlistenable, like a string of flags.

So *"The fix is one line, shown on screen. It goes at the end of line 66"* — you
get the meaning and a pointer, and your eyes do the rest.

---

## Choosing a voice

The bundled voice is a blend of two Kokoro stock voices — 65% `af_heart`, 35%
`af_sarah`. Blending gives you a voice no other Kokoro user has.

Four lines of Python make a different one; see [VOICE.md](VOICE.md). There are 28
English voices to start from and more in other languages, and any two can be
mixed at any ratio.

---

## What days of debugging tell us

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

## Questions people actually ask

**Can Claude Code read its answers out loud?**
Not on its own. Talkback Local adds it — the agent speaks every reply
automatically, with no button to press.

**Is there a free text-to-speech option for Claude Code?**
This is one. It runs Kokoro-82M locally, so there is no API key and no usage
cost at all, however much you use it.

**Does it work offline?**
Yes. After the one-time model download, nothing leaves your machine — no network
call, no account, no telemetry.

**Is it private? Does my agent's output get sent anywhere?**
No. A cloud voice would have to upload every reply — your paths, your
architecture, your client names — to a third party. Talkback Local synthesises on
your own machine, so none of it leaves. You are already trusting one vendor with
the model; there is no reason to add a second for turning text into sound.

**How is this different from the Claude desktop app's read-aloud button?**
That needs a mouse click each time, uses a voice you cannot change, and depends
on their service. This speaks automatically, in a voice you choose or blend
yourself, entirely locally.

**What happens to code in a reply?**
A code block is spoken as "shown on screen" — a pointer to where you should look
rather than a gap. Short inline code is read normally; long strings of flags
become "a command".

**Can I stop it while it is talking?**
Say or type `shush`. It stops in well under a second, without waiting for the
agent.

**Can I use it hands-free with voice dictation?**
That is the intended use. Dictate to the agent, hear it answer, and interrupt or
ask for a repeat by speaking. No keyboard, no mouse. Two dictation tools are
compared under [Dictation](#dictation--separate-tools-not-part-of-talkback).

**Does it work in the terminal and in the Claude Code desktop app?**
Both. It hooks into Claude Code itself, not into any one interface.

**Will it slow my machine down?**
It holds about 4 GB of memory while resident and is idle otherwise. 16 GB of RAM
is comfortable.

**Can I use my own voice or a different one?**
Yes — 28 English voices ship with the model, and any two can be blended into
something nobody else has. See [VOICE.md](VOICE.md).

---

## Measured, and on what

Every number here was taken on one machine: **M4 Max, 16 cores, 128 GB, macOS
26.3**. Your mileage will differ, and the honest list of what has *not* been
tested is below.

| | |
|---|---|
| Installer, start to finish | **29s** |
| Cold first start (312 MB model download + load) | **20s** after the installer |
| Warm start, model already cached | **~4s**, once, at login |
| Time to first word of a reply | **~1s**, regardless of reply length — true since the CPU switch; the Metal build was 3–5s and the log hid it |
| Synthesis of a new sentence, CPU | **0.35s** for ~5s of audio (~13x realtime) |
| Synthesis of a new sentence, Metal (old default) | **3.3–3.6s** — kernel compile per phrase length; 0.5s only on repeats |
| Synthesis, machine genuinely busy | **~2-3x realtime** — measured on the old Metal path, not yet re-measured on the CPU |
| First synthesis after a cold start | 1.1s for 2.6s of audio |
| Resident memory while running | ~4 GB |
| Disk | 312 MB model + ~900 MB Python environment |

Anything above realtime is enough — playback starts on the first sentence while
the rest is still being generated.

### What has been tested

A full install from a clean home directory with an empty model cache, the re-run
upgrade path, and the failure paths — an interrupted reply, a stopped server, an
engine returning an error instead of audio.

### What has not

- **Any Mac other than an M4.** M1, M2 and M3 should be slower and comfortable;
  nobody has measured them.
- **Older macOS.** Built and run on macOS 26.
- **A machine missing the prerequisites.** The installer checks for them and
  stops with a message, but those paths have never actually failed for real.
- **Intel Macs.** The engine no longer needs Metal, so nothing rules them out in
  principle — but none has been measured, and an old dual-core will likely run
  below realtime, which is worse than useless for speech.

If it behaves differently on your hardware, that is worth an issue — those gaps
are the ones that need other people's machines to close.

## Contributing

Yes, please — see [CONTRIBUTING.md](CONTRIBUTING.md). Voice blends, another
platform, and text-rewriting rules for shapes it still reads badly are the most
useful things to send. Issues and pull requests are open.

---

## Credit

The voice is [**Kokoro-82M**](https://huggingface.co/hexgrad/Kokoro-82M) by
[hexgrad](https://github.com/hexgrad) — 82 million parameters, Apache-2.0, and
good enough that a local voice stopped being a compromise. Talkback Local is a
thin layer of plumbing around it; the hard part is theirs.

## Licence

MIT — see [LICENSE](LICENSE) and [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

Kokoro-82M is Apache-2.0. Talkback Local does not redistribute it — the model is
downloaded from Hugging Face at install time. The bundled voice blend in
`engine/icg_voice.pt` is derived from Kokoro's weights and carries Kokoro's
Apache-2.0 terms.

---

Built at **[Inspired Creative Group](https://github.com/Inspired-Creative-Group)**,
an AI production studio in Nova Scotia. We build tools like this because we use
them. If it is useful to you, an issue or a star is welcome.
