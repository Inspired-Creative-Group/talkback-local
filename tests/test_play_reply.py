"""hooks/play_reply.sh on the kokoro path, run as the foreground worker against
the fake engine and the fake ffplay: every chunk is fetched and played exactly
once, the next fetch overlaps the current playback, the stop flag ends the
reply after the chunk that is playing, and the .pcm is published only for a
reply that played to the end — never leaving a .part behind."""

import re
import subprocess


def _sentence(n):
    """A sentence of about n characters ending in a full stop."""
    body = ("lorem ipsum dolor sit amet " * 40)[: n - 1]
    return body.rstrip() + "."


# 100 + 380 + 100 chars: chunk_text.py's limits are 120 for the first chunk and
# 400 after, so each sentence lands in its own chunk (asserted in the tests).
THREE_SENTENCES = " ".join([_sentence(100), _sentence(380), _sentence(100)])
ONE_SENTENCE = "Just one short sentence here."


def _chunks_of(sandbox, tmp_path, text):
    """What chunk_text.py makes of ``text`` — the bodies play_reply.sh must POST."""
    d = tmp_path / "expected_chunks"
    d.mkdir()
    src = d / "text.txt"
    src.write_text(text)
    r = sandbox.run(["python3", sandbox.hooks / "chunk_text.py", src, d])
    assert r.returncode == 0, r.stderr
    return [(d / f"chunk{i:03d}.txt").read_text() for i in range(int(r.stdout.strip()))]


def _workdir(tmp_path, text):
    """The <tmpdir> speak_last_reply.sh hands to the worker: holds text.txt."""
    work = tmp_path / "work"
    work.mkdir()
    (work / "text.txt").write_text(text)
    return work


def _play(sandbox, work, env=None, sid="sid1"):
    save = sandbox.lastreply / f"{sid}.pcm"
    r = sandbox.run([sandbox.hooks / "play_reply.sh", "kokoro", work, save, sandbox.log], env=env)
    assert r.returncode == 0, r.stderr
    return save


def _parts_under(lastreply):
    return sorted(str(p) for p in lastreply.rglob("*.part"))


def test_three_chunks_are_fetched_and_played_once_each_in_order(sandbox, fake_engine, tmp_path):
    expected = _chunks_of(sandbox, tmp_path, THREE_SENTENCES)
    assert len(expected) == 3
    work = _workdir(tmp_path, THREE_SENTENCES)

    save = _play(sandbox, work)

    assert [r["body"] for r in fake_engine.requests] == expected
    assert [args[-1] for args in sandbox.calls_to("ffplay")] == [str(work / f"a{i}.pcm") for i in range(3)]
    assert len(sandbox.calls_to("ffplay-done")) == 3
    log = sandbox.log.read_text()
    assert re.search(r"PLAY start pid=\d+ chunks=3 sid1\.pcm", log)
    assert re.search(r"PLAY done pid=\d+ stopped=0", log)
    assert save.read_bytes() == fake_engine.pcm * 3
    assert _parts_under(sandbox.lastreply) == []
    assert not work.exists()


def test_one_sentence_is_one_fetch_one_play_and_saved(sandbox, fake_engine, tmp_path):
    assert _chunks_of(sandbox, tmp_path, ONE_SENTENCE) == [ONE_SENTENCE]
    work = _workdir(tmp_path, ONE_SENTENCE)

    save = _play(sandbox, work)

    assert [r["body"] for r in fake_engine.requests] == [ONE_SENTENCE]
    assert [args[-1] for args in sandbox.calls_to("ffplay")] == [str(work / "a0.pcm")]
    assert re.search(r"PLAY start pid=\d+ chunks=1 sid1\.pcm", sandbox.log.read_text())
    assert save.read_bytes() == fake_engine.pcm
    assert _parts_under(sandbox.lastreply) == []
    assert not work.exists()


def test_next_chunk_is_fetched_while_the_current_one_plays(sandbox, fake_engine, tmp_path):
    fake_engine.delay = 0.3
    work = _workdir(tmp_path, THREE_SENTENCES)

    _play(sandbox, work, env=dict(sandbox.env, FAKE_FFPLAY_SLEEP="0.5"))

    assert len(fake_engine.requests) == 3
    calls = sandbox.read_calls()
    first_play_done = next(t for t, name, _ in calls if name == "ffplay-done")
    # Sequential fetch-then-play would only ask for chunk 1 after chunk 0 had
    # finished playing; pipelining asks for it during chunk 0.
    assert fake_engine.requests[1]["t_start"] < first_play_done


def test_stop_flag_ends_the_reply_after_the_current_chunk_and_drops_it(sandbox, fake_engine, tmp_path):
    fake_engine.delay = 1.0
    work = _workdir(tmp_path, THREE_SENTENCES)
    save = sandbox.lastreply / "sid1.pcm"
    env = dict(sandbox.env, FAKE_FFPLAY_TOUCH_STOP="1")
    assert not sandbox.stopflag.exists()

    # Run it the way speak_last_reply.sh does (nohup ... >/dev/null 2>&1) so a
    # curl left behind cannot keep our pipes open and hide behind the wait.
    r = subprocess.run(
        [str(sandbox.hooks / "play_reply.sh"), "kokoro", str(work), str(save), str(sandbox.log)],
        env=env,
        cwd=str(sandbox.repo),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    assert r.returncode == 0

    # chunk 0 played, its player raised the flag, chunk 1 was never played
    assert [args[-1] for args in sandbox.calls_to("ffplay")] == [str(work / "a0.pcm")]
    assert re.search(r"PLAY done pid=\d+ stopped=1", sandbox.log.read_text())
    # an interrupted reply is not published
    assert not save.exists()
    assert _parts_under(sandbox.lastreply) == []
    assert not work.exists()
    # the fetch of chunk 2 was started just before the flag was seen; it must
    # not outlive the worker (the engine is still busy with it for ~1 s).
    assert 2 <= len(fake_engine.requests) <= 3

    def fetch_still_running():
        pattern = re.escape(str(work / "chunk002.txt"))
        return subprocess.run(["/usr/bin/pgrep", "-f", pattern], capture_output=True, check=False).returncode == 0

    assert sandbox.wait_for(lambda: not fetch_still_running(), timeout=0.4), (
        "curl for chunk 2 is still running after play_reply.sh exited: the pending fetch was not killed"
    )


def test_engine_error_body_is_never_published_and_the_failure_is_loud(sandbox, fake_engine, tmp_path):
    fake_engine.fail_with = b'{"detail": "model not loaded"}'
    work = _workdir(tmp_path, ONE_SENTENCE)

    save = _play(sandbox, work)

    assert not save.exists()
    assert _parts_under(sandbox.lastreply) == []
    assert not work.exists()
    log = sandbox.log.read_text()
    assert "TTS FAILED" in log
    assert "model not loaded" in log
    assert sandbox.calls_to("osascript"), "a failed reply must raise a notification, not fail silently"


def test_tts_tempo_is_applied_to_every_chunk(sandbox, fake_engine, tmp_path):
    work = _workdir(tmp_path, THREE_SENTENCES)

    _play(sandbox, work, env=dict(sandbox.env, TTS_TEMPO="1.25"))

    calls = sandbox.calls_to("ffplay")
    assert len(calls) == 3
    for args in calls:
        assert args[args.index("-af") + 1] == "atempo=1.25"


def test_no_tempo_filter_when_tts_tempo_is_unset(sandbox, fake_engine, tmp_path):
    assert "TTS_TEMPO" not in sandbox.env
    work = _workdir(tmp_path, THREE_SENTENCES)

    _play(sandbox, work)

    calls = sandbox.calls_to("ffplay")
    assert len(calls) == 3
    assert all("-af" not in args for args in calls)


def test_stale_stop_flag_is_cleared_so_the_reply_plays_in_full(sandbox, fake_engine, tmp_path):
    sandbox.stopflag.touch()
    work = _workdir(tmp_path, THREE_SENTENCES)

    save = _play(sandbox, work)

    assert len(sandbox.calls_to("ffplay")) == 3
    assert re.search(r"PLAY done pid=\d+ stopped=0", sandbox.log.read_text())
    assert save.read_bytes() == fake_engine.pcm * 3
    assert not sandbox.stopflag.exists()
