"""Warm Kokoro TTS server. Loads the model once; streams raw PCM per request.

POST / with the text as the body -> s16le, 24000 Hz, mono, streamed sentence by
sentence so playback starts on the first chunk instead of the last.
"""
import os
import threading
import warnings
from urllib.parse import parse_qs, urlparse

warnings.filterwarnings("ignore")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import torch
from kokoro import KPipeline

HERE  = os.path.dirname(os.path.abspath(__file__))
PORT  = int(os.environ.get("KOKORO_PORT", "8910"))
SPEED = float(os.environ.get("KOKORO_SPEED", "1.15"))

VOICE = torch.load(os.path.join(HERE, "icg_voice.pt"), weights_only=True)
# CPU by default. Metal looked like the fast path but measured as the slow one
# (2026-09-07): PyTorch MPS compiles a fresh kernel for every new phrase length,
# ~3.5s each, and real replies never repeat a length — so nearly every first
# chunk paid it. Same sentences on the M4 CPU: 0.3s, every time. The launchd
# job runs at Interactive priority so a busy machine does not starve it.
DEVICE = os.environ.get("KOKORO_DEVICE") or "cpu"
try:
    PIPE = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device=DEVICE)
except Exception as e:  # noqa: BLE001  # whatever the device raises, fall back to cpu
    print(f"device {DEVICE} failed ({e}); falling back to cpu", flush=True)
    DEVICE = "cpu"
    PIPE = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device=DEVICE)
LOCK  = threading.Lock()          # one synthesis at a time; the model isn't reentrant

# warm the graph so the first real request is not the slow one
list(PIPE("Ready.", voice=VOICE, speed=SPEED))

REQLOG = os.path.expanduser("~/.claude/automation/kokoro/requests.log")

def logreq(text, client):
    import time as _t
    with open(REQLOG, "a") as f:
        f.write(f"{_t.strftime('%F %T')} from={client} chars={len(text)} :: {text[:60]!r}\n")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        self.send_response(200); self.end_headers(); self.wfile.write(b"ok")

    def do_POST(self):
        # ?voice=<kokoro voice name> overrides the blend for ONE request. The
        # default stays the installed voice, so nothing that omits it changes.
        requested = parse_qs(urlparse(self.path).query).get("voice", [""])[0]
        voice = requested if requested and requested.replace("_", "").isalnum() else None
        text = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
        try:
            logreq(text, self.client_address[0])
        except Exception:  # noqa: BLE001, S110  # request logging is best-effort; never break synthesis
            pass
        if not text.strip():
            self.send_response(400); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "audio/L16")
        self.end_headers()
        try:
            with LOCK:
                for _, _, audio in PIPE(text, voice=(voice or VOICE), speed=SPEED):
                    a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
                    pcm = (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()
                    self.wfile.write(pcm)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                                  # client hit shush; normal

if __name__ == "__main__":
    print(f"kokoro server ready on 127.0.0.1:{PORT} (device={DEVICE})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
