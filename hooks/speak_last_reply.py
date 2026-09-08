import json
import os
import re
import sys

ONES = ["zero","one","two","three","four","five","six","seven","eight","nine",
        "ten","eleven","twelve","thirteen","fourteen","fifteen","sixteen",
        "seventeen","eighteen","nineteen"]
TENS = {2:"twenty",3:"thirty",4:"forty",5:"fifty"}

def n2w(n):
    if n < 20:
        return ONES[n]
    t, o = divmod(n, 10)
    return TENS[t] + ("-" + ONES[o] if o else "")

def say_time(m):
    h, mi = int(m.group(1)), int(m.group(2))
    if h > 23 or mi > 59:
        return m.group(0)
    h12 = h % 12 or 12
    if mi == 0:
        return n2w(h12) + " o'clock"
    if mi < 10:
        return n2w(h12) + " oh " + ONES[mi]
    return n2w(h12) + " " + n2w(mi)


SAYABLE = re.compile(r"^[A-Za-z][A-Za-z0-9 ._-]{0,24}$")

def say_inline(m):
    """Inline code is often the most important word in the sentence — `shush`,
    `main.py`, `TTS on`. Speak those. Only replace the unlistenable ones: long,
    or full of flags and symbols. Deleting them outright left broken sentences
    ("Run  first")."""
    t = m.group(1).strip()
    if not t:
        return ""
    if SAYABLE.match(t) and t.count(".") <= 1:
        return t
    return "a command"

# ElevenLabs fallback engine only — the local Kokoro path ignores this.
# Set TTS_VOICE_ID to your own voice; there is deliberately no default, so a
# borrowed voice can never ship in a public checkout.
VOICE = os.environ.get("TTS_VOICE_ID", "")
tp, tmp = sys.argv[1], sys.argv[2]

last = ""
with open(tp, errors="ignore") as f:
    for line in f:
        try:
            o = json.loads(line)
        except Exception:  # noqa: BLE001, S112  # a corrupt transcript line must never abort the hook
            continue
        if o.get("type") != "assistant":
            continue
        c = o.get("message", {}).get("content")
        if not isinstance(c, list):
            continue
        t = "".join(b.get("text", "") for b in c
                    if isinstance(b, dict) and b.get("type") == "text").strip()
        if t:
            last = t

s = last
# A code block becomes a pointer, not a gap: you are listening, so the useful
# thing is being told where to look.
s = re.sub(r"```.*?```", " shown on screen. ", s, flags=re.DOTALL)
s = re.sub(r"`([^`]*)`", say_inline, s)
s = re.sub(r"^\s*#{1,6}\s*", "", s, flags=re.MULTILINE)
s = re.sub(r"\*\*|\*", "", s)
s = re.sub(r"__(\S.*?)__", r"\1", s)
s = re.sub(r"^\s*[-*]\s+", "", s, flags=re.MULTILINE)
s = re.sub(r"\[([^\]]*)\]\(([^)]*)\)", r"\1", s)
s = re.sub(r"(\w)_(\w)", r"\1 \2", s)
s = re.sub(r"(\w)_(\w)", r"\1 \2", s)
s = re.sub(r"\S+@\S+\.\w+", " an email address ", s)               # emails
s = re.sub(r"(?:https?://|ftp://|www\.)\S+", " a link ", s)          # full URLs
s = re.sub(r"\b[\w.-]+\.(?:com|org|net|io|ai|co|dev|app|gg|tv|ca|uk)\b(?:/\S*)?",
           " a link ", s)                                            # bare domains
s = re.sub(r"\b(?=[A-Za-z0-9-]*[A-Za-z])(?=[A-Za-z0-9-]*\d)[A-Za-z0-9-]{12,}\b",
           " an I D ", s)                                            # hashes, UUIDs, voice IDs
s = re.sub(r"\S*/\S*", "", s)
s = re.sub(r"\b(\d{1,2}):(\d{2})\b", say_time, s)   # 9:00 -> nine o'clock
s = re.sub(r"[_]+", " ", s)                      # snake_case -> words, no "underscore" slurs
s = re.sub(r"\b([a-z]+)([0-9])", r"\1 \2", s)      # v2_5 / mp3_44100 -> spaced
s = re.sub(r"[—–]", ", ", s)                     # em/en dash -> a real pause
s = re.sub(r"\.{2,}", ".", s)
s = re.sub(r"\n{2,}", ". ", s)
s = re.sub(r"\.\s*\.", ".", s)                    # no ".." from joined lines
s = re.sub(r"[^\w\s.,;:!?'()%$-]", " ", s)        # drop stray symbols entirely
s = re.sub(r"[:,]\s*(shown on screen)", r", \1", s)   # "one line: shown on screen" -> "one line, shown on screen"
s = re.sub(r"\s+([.,;:!?])", r"\1", s)
s = re.sub(r"[ \t]{2,}", " ", s).strip()[:2500]

if not s:
    print("empty text", file=sys.stderr)
    sys.exit(1)

key = os.environ.get("ELEVENLABS_API_KEY", "")
if not key:
    try:
        txt = open(os.path.expanduser("~/.zshrc"), errors="ignore").read()  # noqa: SIM115  # kept single-line; this module is restructured in a later stage
        m = re.search(r"ELEVENLABS_API_KEY=[\"']?([A-Za-z0-9_\-]+)", txt)
        key = m.group(1) if m else ""
    except Exception:  # noqa: BLE001  # an unreadable ~/.zshrc just means no key
        key = ""
if not key:
    print("no api key", file=sys.stderr)
    sys.exit(1)

payload = {
    "text": s,
    "model_id": os.environ.get("TTS_MODEL", "eleven_multilingual_v2"),
    "apply_text_normalization": os.environ.get("TTS_NORMALIZE", "on"),
    "voice_settings": {
        "stability": float(os.environ.get("TTS_STABILITY", "0.50")),
        "similarity_boost": float(os.environ.get("TTS_SIMILARITY", "0.75")),
        "style": float(os.environ.get("TTS_STYLE", "0.25")),
        "use_speaker_boost": True,
        "speed": float(os.environ.get("TTS_SPEED", "1.15")),
    },
}
with open(os.path.join(tmp, "text.txt"), "w") as f:
    f.write(s)
with open(os.path.join(tmp, "payload.json"), "w") as f:
    json.dump(payload, f)
kp = os.path.join(tmp, "key")
with open(kp, "w") as f:
    f.write(key)
os.chmod(kp, 0o600)
with open(os.path.join(tmp, "voice"), "w") as f:
    f.write(VOICE)
print(len(s))
