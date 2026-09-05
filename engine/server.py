#!/usr/bin/env python
"""Warm Kokoro TTS server. Loads the model once; streams raw PCM per request.

POST / with the text as the body -> s16le, 24000 Hz, mono, streamed sentence by
sentence so playback starts on the first chunk instead of the last.
"""
import os, sys, warnings, threading
warnings.filterwarnings("ignore")
import numpy as np, torch
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from kokoro import KPipeline

HERE  = os.path.dirname(os.path.abspath(__file__))
PORT  = int(os.environ.get("KOKORO_PORT", "8899"))
SPEED = float(os.environ.get("KOKORO_SPEED", "1.15"))

VOICE = torch.load(os.path.join(HERE, "icg_voice.pt"), weights_only=True)
# Metal GPU by default. The M4's CPU is contended; the GPU is not, so this is
# what keeps synthesis ahead of playback when the machine is busy.
DEVICE = os.environ.get("KOKORO_DEVICE") or ("mps" if torch.backends.mps.is_available() else "cpu")
try:
    PIPE = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", device=DEVICE)
except Exception as e:
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
        text = self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8", "replace")
        try:
            logreq(text, self.client_address[0])
        except Exception:
            pass
        if not text.strip():
            self.send_response(400); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", "audio/L16")
        self.end_headers()
        try:
            with LOCK:
                for _, _, audio in PIPE(text, voice=VOICE, speed=SPEED):
                    a = audio.detach().cpu().numpy() if hasattr(audio, "detach") else np.asarray(audio)
                    pcm = (np.clip(a, -1, 1) * 32767).astype("<i2").tobytes()
                    self.wfile.write(pcm)
                    self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass                                  # client hit shush; normal

if __name__ == "__main__":
    print(f"kokoro server ready on 127.0.0.1:{PORT} (device={DEVICE})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
