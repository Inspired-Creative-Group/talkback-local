"""``talkback verify`` (reached through the kept hooks/verify_audio.sh shim)
decides whether a downloaded file is really audio.

A 200 response carrying a JSON or HTML error body, or a file too small to be
speech, must be rejected (exit 1) *loudly*: a dated line in the log, a
desktop notification, and — for a cloud engine — the failure read aloud by the
local engine. Real PCM passes silently (exit 0, nothing logged)."""

import re

from conftest import NON_SILENT_PCM

QUOTA_JSON = b'{"detail":{"message":"quota exceeded"}}'
HTML_ERROR = b"<html>Bad Gateway</html>"
LOG_LINE = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} TTS FAILED \[(?P<engine>[^\]]+)\]: (?P<msg>.*)$")


def _verify(sandbox, path, engine, env=None):
    return sandbox.run([sandbox.hooks / "verify_audio.sh", path, engine, sandbox.log], env=env)


def _log_lines(sandbox):
    if not sandbox.log.exists():
        return []
    return sandbox.log.read_text().splitlines()


def _failures(sandbox):
    """(engine, message) for every TTS FAILED line in the log."""
    out = []
    for line in _log_lines(sandbox):
        m = LOG_LINE.match(line)
        assert m, f"unexpected log line: {line!r}"
        out.append((m["engine"], m["msg"]))
    return out


def _osascript_resolves_to_the_shim(sandbox):
    r = sandbox.run(["/bin/bash", "-c", "command -v osascript"])
    return r.stdout.strip() == str(sandbox.shims / "osascript")


def _notification(msg, engine):
    return ["-e", f'display notification "{msg}" with title "Voice failed ({engine})"']


# --------------------------------------------------------------------------
# rejections
# --------------------------------------------------------------------------
def test_json_error_body_is_rejected_and_announced_via_notification(sandbox, tmp_path):
    f = tmp_path / "reply.mp3"
    f.write_bytes(QUOTA_JSON)

    r = _verify(sandbox, f, "elevenlabs")

    assert r.returncode == 1
    assert _failures(sandbox) == [("elevenlabs", "quota exceeded")]
    assert _osascript_resolves_to_the_shim(sandbox)
    assert sandbox.calls_to("osascript") == [_notification("quota exceeded", "elevenlabs")]


def test_html_error_body_is_rejected_with_its_text_in_the_log(sandbox, tmp_path):
    f = tmp_path / "reply.mp3"
    f.write_bytes(HTML_ERROR + b"\n")

    r = _verify(sandbox, f, "elevenlabs")

    assert r.returncode == 1
    assert _failures(sandbox) == [("elevenlabs", "<html>Bad Gateway</html>")]
    assert sandbox.calls_to("osascript") == [_notification("<html>Bad Gateway</html>", "elevenlabs")]


def test_file_under_2000_bytes_is_rejected_as_too_short(sandbox, tmp_path):
    f = tmp_path / "reply.pcm"
    f.write_bytes(NON_SILENT_PCM[:1999])
    assert f.stat().st_size == 1999
    assert f.read_bytes()[:1] not in (b"{", b"<")

    r = _verify(sandbox, f, "kokoro")

    assert r.returncode == 1
    failures = _failures(sandbox)
    assert len(failures) == 1
    engine, msg = failures[0]
    assert engine == "kokoro"
    assert "only 1999 bytes" in msg
    assert len(sandbox.calls_to("osascript")) == 1
    assert "only 1999 bytes" in sandbox.calls_to("osascript")[0][1]


def test_missing_file_is_rejected_as_nothing_downloaded(sandbox, tmp_path):
    f = tmp_path / "never-written.pcm"
    assert not f.exists()

    r = _verify(sandbox, f, "kokoro")

    assert r.returncode == 1
    assert _failures(sandbox) == [("kokoro", "nothing was downloaded")]
    assert sandbox.calls_to("osascript") == [_notification("nothing was downloaded", "kokoro")]


# --------------------------------------------------------------------------
# acceptances
# --------------------------------------------------------------------------
def test_2000_bytes_of_pcm_is_accepted_silently(sandbox, tmp_path):
    f = tmp_path / "reply.pcm"
    f.write_bytes(NON_SILENT_PCM[:2000])
    assert f.stat().st_size == 2000

    r = _verify(sandbox, f, "kokoro")

    assert r.returncode == 0
    assert _log_lines(sandbox) == []
    assert sandbox.read_calls() == []


def test_50kb_of_pcm_is_accepted(sandbox, tmp_path):
    f = tmp_path / "reply.pcm"
    f.write_bytes((NON_SILENT_PCM * 11)[: 50 * 1024])
    assert f.stat().st_size == 50 * 1024

    r = _verify(sandbox, f, "elevenlabs")

    assert r.returncode == 0
    assert _log_lines(sandbox) == []
    assert sandbox.read_calls() == []


# --------------------------------------------------------------------------
# spoken announcement of a cloud failure
# --------------------------------------------------------------------------
def test_cloud_failure_is_spoken_through_the_local_engine(sandbox, fake_engine, tmp_path):
    f = tmp_path / "reply.mp3"
    f.write_bytes(QUOTA_JSON)

    r = _verify(sandbox, f, "elevenlabs")

    assert r.returncode == 1
    assert sandbox.wait_for(lambda: len(fake_engine.requests) == 1)
    assert fake_engine.requests[0]["body"].startswith("Voice failed")
    assert fake_engine.requests[0]["body"] == "Voice failed. quota exceeded"

    assert sandbox.wait_for(lambda: len(sandbox.calls_to("play-done")) == 1)
    plays = sandbox.plays()
    assert len(plays) == 1
    assert plays[0] == ["24000", "2", "int16"]
    assert sandbox.writes() == [2 * len(NON_SILENT_PCM)]
    assert len(fake_engine.requests) == 1
    assert sandbox.calls_to("osascript") == [_notification("quota exceeded", "elevenlabs")]
    assert sandbox.wait_quiet()
    assert not sandbox.player_pid.exists()


def test_local_engine_failure_is_not_spoken_through_itself(sandbox, fake_engine, tmp_path):
    f = tmp_path / "reply.pcm"
    f.write_bytes(QUOTA_JSON)

    r = _verify(sandbox, f, "kokoro")

    assert r.returncode == 1
    assert _failures(sandbox) == [("kokoro", "quota exceeded")]
    assert fake_engine.requests == []
    assert sandbox.plays() == []
    assert sandbox.calls_to("osascript") == [_notification("quota exceeded", "kokoro")]


def test_cloud_failure_is_not_spoken_when_local_engine_is_down(sandbox, tmp_path):
    assert sandbox.env["KOKORO_PORT"] == "8999"
    f = tmp_path / "reply.mp3"
    f.write_bytes(QUOTA_JSON)

    r = _verify(sandbox, f, "elevenlabs")

    assert r.returncode == 1
    assert _failures(sandbox) == [("elevenlabs", "quota exceeded")]
    assert sandbox.plays() == []
    assert sandbox.calls_to("osascript") == [_notification("quota exceeded", "elevenlabs")]
