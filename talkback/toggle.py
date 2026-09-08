"""The UserPromptSubmit hook (``talkback toggle``): what ``hooks/tts_toggle.sh``
did. Owner: session. Contract in the Stage 2 spec §1.2 / §2 ``toggle``."""

PHRASES = {
    "on": {"tts on"},
    "shush": {"shush", "stop", "quiet", "be quiet"},
    "replay": {"replay", "again", "repeat", "say that again", "repeat that", "one more time"},
    "off": {"tts off"},
}


def normalise(prompt: str) -> str:
    """lower(); delete every character in ``.,!?;:``; collapse whitespace."""
    raise NotImplementedError("talkback.toggle.normalise — session implementer")


def classify(prompt: str) -> "str | None":
    """``"on" | "shush" | "replay" | "off" | None``."""
    raise NotImplementedError("talkback.toggle.classify — session implementer")


def run(stdin_text: str, home=None, *, replay_fn=None, shush_fn=None) -> int:
    """2 on a handled phrase (one word on stderr, one log line), 0 otherwise.
    Invalid JSON → 0, silent."""
    raise NotImplementedError("talkback.toggle.run — session implementer")
