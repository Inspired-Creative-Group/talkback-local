"""The PyTorch ``kokoro`` backend (the macOS default). All heavy imports live
inside :func:`load` so ``sys.modules`` fakes apply. Owner: server."""


def load(voice_path, env):
    """``(synthesize, voice, device)`` — ``synthesize(text, speed)`` yields the
    audio array of each pipeline chunk."""
    raise NotImplementedError("talkback.synth_torch.load — server implementer")
