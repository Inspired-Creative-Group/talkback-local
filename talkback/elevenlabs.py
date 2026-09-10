"""The ElevenLabs request builder and fetch. The audio is asked for as
``output_format=pcm_24000`` — the same s16le mono the local engine produces,
so the one player plays it (nothing here decodes mp3). The key travels in a
header, never in the URL. Owner: player."""

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.elevenlabs.io/v1/text-to-speech"
OUTPUT_FORMAT = "pcm_24000"


def build_request(voice: str, key: str, payload: dict, env) -> "tuple[str, dict, bytes]":
    """``(url, headers, body)`` — no network. ``TTS_LATOPT`` (default 2) is
    the only tunable on the URL; the format is pinned."""
    latopt = env.get("TTS_LATOPT") or "2"
    url = (
        f"{API}/{urllib.parse.quote(str(voice), safe='')}/stream"
        f"?optimize_streaming_latency={urllib.parse.quote(str(latopt), safe='')}&output_format={OUTPUT_FORMAT}"
    )
    headers = {"xi-api-key": key, "Content-Type": "application/json"}
    return url, headers, json.dumps(payload).encode("utf-8")


def fetch(voice: str, key: str, payload: dict, env, timeout: float = 120) -> "bytes | None":
    """POST and return the raw body whatever the status (an error body comes
    back so ``verify`` can name it); None when nothing came back at all."""
    url, headers, body = build_request(voice, key, payload, env)
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:  # https, fixed host
            return r.read()
    except urllib.error.HTTPError as e:
        try:
            return e.read()
        except (OSError, http.client.HTTPException):
            return b""
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError):
        return None
