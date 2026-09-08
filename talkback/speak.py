"""The Stop hook (``talkback speak``): what ``hooks/speak_last_reply.sh`` did.
Owner: session. Flow in the Stage 2 spec §2 ``speak``."""


def run(stdin_text: str, env, home=None) -> int:
    """Hook JSON on ``stdin_text`` (``session_id``, ``transcript_path``);
    always returns 0 so Claude Code is never blocked."""
    raise NotImplementedError("talkback.speak.run — session implementer")
