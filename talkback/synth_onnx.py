"""The ``kokoro-onnx`` backend, selected with ``TALKBACK_ENGINE=onnx``. No
PyTorch and no espeak-ng install — the Windows route. Same PCM out, same
speed, same HTTP contract as the torch backend. Owner: server."""

import os

RATE = 24000
# The ICG voice as a blend of two stock voices, computed at boot from the
# voices file, so no second binary ships. Weights match icg_voice.pt.
BLEND = (("af_heart", 0.65), ("af_sarah", 0.35))
MODEL_FILES_URL = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/"


def load(model_path, voices_path, env):
    """Build the onnx pipeline; return ``(synthesize, voice, "onnx")`` where
    ``voice`` is the af_heart/af_sarah blend (float32) and
    ``synthesize(text, speed)`` yields one audio array per call.
    ``KOKORO_DEVICE`` is ignored — onnxruntime picks its own provider.

    A missing library or model file raises ``SystemExit`` with a message that
    says what to install or download; the server never dies with an
    onnxruntime traceback for a file that is simply not there."""
    try:
        from kokoro_onnx import Kokoro
    except ImportError as e:
        raise SystemExit(
            "TALKBACK_ENGINE=onnx needs the kokoro-onnx package: "
            f"pip install 'talkback-local[onnx]' ({e})"
        ) from e
    import numpy as np

    model_path, voices_path = str(model_path), str(voices_path)
    for label, p in (("model", model_path), ("voices", voices_path)):
        if not os.path.isfile(p):
            raise SystemExit(
                f"TALKBACK_ENGINE=onnx: {label} file not found at {p} — "
                f"download {os.path.basename(p)} from {MODEL_FILES_URL} "
                f"(or point TALKBACK_ONNX_{label.upper()} at it)"
            )

    k = Kokoro(model_path, voices_path)
    voice = sum(w * np.asarray(k.get_voice_style(name), dtype=np.float32) for name, w in BLEND)
    voice = np.asarray(voice, dtype=np.float32)

    def synthesize(text, speed):
        audio, sr = k.create(text, voice=voice, speed=speed, lang="en-us")
        if int(sr) != RATE:
            raise RuntimeError(f"kokoro-onnx returned {sr} Hz audio; the player expects {RATE}")
        yield audio

    return synthesize, voice, "onnx"
