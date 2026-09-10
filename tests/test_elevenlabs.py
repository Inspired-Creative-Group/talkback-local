"""The ElevenLabs request: pcm_24000 so the one player plays it, the key in a
header and never in the URL. No network."""

import json

import pytest

from talkback import elevenlabs

pytestmark = pytest.mark.pure


def test_build_request_uses_pcm_24000_and_keeps_the_key_out_of_the_url():
    payload = {"text": "hello", "model_id": "eleven_multilingual_v2", "voice_settings": {"speed": 1.15}}
    url, headers, body = elevenlabs.build_request("voice123", "sk-secret", payload, {})

    assert url == (
        "https://api.elevenlabs.io/v1/text-to-speech/voice123/stream"
        "?optimize_streaming_latency=2&output_format=pcm_24000"
    )
    assert "sk-secret" not in url
    assert headers == {"xi-api-key": "sk-secret", "Content-Type": "application/json"}
    assert json.loads(body.decode("utf-8")) == payload
    assert "mp3" not in url


def test_latency_option_comes_from_the_environment_and_the_format_is_pinned():
    url, _, _ = elevenlabs.build_request("v", "k", {}, {"TTS_LATOPT": "4", "TTS_FORMAT": "mp3_44100_128"})
    assert "optimize_streaming_latency=4" in url
    assert "output_format=pcm_24000" in url
    assert "mp3_44100_128" not in url
    url, _, _ = elevenlabs.build_request("v", "k", {}, {"TTS_LATOPT": ""})
    assert "optimize_streaming_latency=2" in url


def test_fetch_returns_none_when_nothing_answers(monkeypatch):
    import urllib.error

    def refuse(req, timeout=None):
        raise urllib.error.URLError("sandbox: no network")

    monkeypatch.setattr(elevenlabs.urllib.request, "urlopen", refuse)
    assert elevenlabs.fetch("v", "k", {"text": "x"}, {}, timeout=1) is None
