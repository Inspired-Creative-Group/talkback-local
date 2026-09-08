"""The ``kokoro-onnx`` backend, ``TALKBACK_ENGINE=onnx``. Owner: server."""


def load(model_path, voices_path, env):
    """``(synthesize, voice, "onnx")`` — voice is the af_heart/af_sarah blend."""
    raise NotImplementedError("talkback.synth_onnx.load — server implementer")
