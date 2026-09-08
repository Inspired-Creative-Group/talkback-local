"""The ElevenLabs request builder and fetch (``output_format=pcm_24000`` so
the one player plays it). Owner: player."""


def build_request(voice: str, key: str, payload: dict, env) -> "tuple[str, dict, bytes]":
    """``(url, headers, body)``; no network."""
    raise NotImplementedError("talkback.elevenlabs.build_request — player implementer")


def fetch(voice: str, key: str, payload: dict, env, timeout: float = 120) -> "bytes | None":
    raise NotImplementedError("talkback.elevenlabs.fetch — player implementer")
