"""The PyTorch ``kokoro`` backend — the macOS default, what ``engine/server.py``
has always run. Every heavy import lives inside :func:`load`, so a
``sys.modules`` fake of ``torch`` / ``kokoro`` is what gets imported in tests
and ``import talkback.synth_torch`` costs nothing. Owner: server."""

_REPO_ID = "hexgrad/Kokoro-82M"


def load(voice_path, env):
    """Load the voice tensor and build the pipeline; return
    ``(synthesize, voice, device)`` where ``synthesize(text, speed)`` yields the
    audio array of each pipeline chunk.

    ``device`` is ``KOKORO_DEVICE`` or ``cpu``. CPU by default: Metal looked
    like the fast path but measured as the slow one (2026-09-07) — PyTorch MPS
    compiles a fresh kernel for every new phrase length, ~3.5 s each, and real
    replies never repeat a length. A device that fails to construct falls back
    to cpu with the same printed line as before.
    """
    import warnings

    warnings.filterwarnings("ignore")  # before torch is imported, as engine/server.py did
    import torch
    from kokoro import KPipeline

    voice = torch.load(str(voice_path), weights_only=True)
    device = env.get("KOKORO_DEVICE") or "cpu"
    try:
        pipe = KPipeline(lang_code="a", repo_id=_REPO_ID, device=device)
    except Exception as e:  # noqa: BLE001  # whatever the device raises, fall back to cpu
        print(f"device {device} failed ({e}); falling back to cpu", flush=True)
        device = "cpu"
        pipe = KPipeline(lang_code="a", repo_id=_REPO_ID, device=device)

    def synthesize(text, speed):
        for _, _, audio in pipe(text, voice=voice, speed=speed):
            yield audio

    return synthesize, voice, device
