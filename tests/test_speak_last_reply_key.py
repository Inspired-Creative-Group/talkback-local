"""The ElevenLabs key is only needed by the ElevenLabs engine. README promises
the local engine needs no API key at all."""

from conftest import assistant_turn, write_transcript


def _run(sandbox, tmp_path, env):
    tp = write_transcript(tmp_path / "t.jsonl", [assistant_turn("Hello there, this is a reply.")])
    out = tmp_path / "out"
    out.mkdir()
    r = sandbox.run(["python3", sandbox.hooks / "speak_last_reply.py", tp, out], env=env)
    return r, out


def test_kokoro_default_speaks_without_a_key(sandbox, tmp_path):
    assert "TTS_ENGINE" not in sandbox.env
    assert "ELEVENLABS_API_KEY" not in sandbox.env
    r, out = _run(sandbox, tmp_path, sandbox.env)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(len("Hello there, this is a reply."))
    assert (out / "text.txt").read_text() == "Hello there, this is a reply."
    assert (out / "key").read_text() == ""


def test_elevenlabs_still_requires_a_key(sandbox, tmp_path):
    env = dict(sandbox.env, TTS_ENGINE="elevenlabs")
    r, out = _run(sandbox, tmp_path, env)
    assert r.returncode == 1
    assert "no api key" in r.stderr
    assert not (out / "text.txt").exists()
