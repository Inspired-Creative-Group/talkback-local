"""The session modules in process, on a temporary HOME — what the
windows-latest job runs. Pure: ``tmp_path`` and function calls only; nothing
spawned, nothing on the audio device, nothing over the network (the engine
check and the player spawn are injected). The shell shims' exit codes and
stderr are pinned by test_tts_toggle.py / test_speak_last_reply_sh.py.

Covered here:
  toggle   the phrase table, normalisation, run()'s exit codes / words / lines
  speak    text and marker saved before the player is spawned, the 120 s
           duplicate guard, housekeeping, the reasons that land in speak.log
  replay   select(): text + engine wins, recording is the fallback, .mp3 gone
  recmode  prune / on / off / list output strings, RECMODE_DEAD_MIN
"""

import hashlib
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

import pytest
from conftest import (
    assistant_turn,
    hook_payload,
    tool_turn,
    user_turn,
    write_transcript,
)

from talkback import paths, recmode, replay, speak, state, toggle

pytestmark = pytest.mark.pure

SID = "abcdef12-3456-7890-abcd-ef1234567890"
TS = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d "


def _payload(prompt, sid=SID):
    return json.dumps({"session_id": sid, "prompt": prompt})


def _log_lines(home):
    p = paths.speak_log(home)
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


@pytest.fixture
def home(tmp_path):
    h = tmp_path / "home"
    h.mkdir()
    return h


# ==========================================================================
# toggle — the phrase table
# ==========================================================================
def test_classify_exact_phrases():
    assert toggle.PHRASES == {
        "on": {"tts on"},
        "shush": {"shush", "stop", "quiet", "be quiet"},
        "replay": {"replay", "again", "repeat", "say that again", "repeat that", "one more time"},
        "off": {"tts off"},
    }
    for kind, phrases in toggle.PHRASES.items():
        for phrase in phrases:
            assert toggle.classify(phrase) == kind, phrase
    groups = list(toggle.PHRASES.values())
    for i, a in enumerate(groups):
        for b in groups[i + 1 :]:
            assert not (a & b), "a phrase must mean one thing"


@pytest.mark.parametrize(
    "prompt, kind",
    [
        ("TTS ON", "on"),
        (" tts on ", "on"),
        ("tts on.", "on"),
        ("TTS on!", "on"),
        ("tts  on", "on"),
        ("tts\ton", "on"),
        ("TTS off.", "off"),
        ("SHUSH", "shush"),
        ("Stop!", "shush"),
        ("be  quiet?", "shush"),
        ("Say That Again", "replay"),
        ("again...", "replay"),
        ("One more time;", "replay"),
    ],
)
def test_classify_tolerates_case_punctuation_and_whitespace(prompt, kind):
    assert toggle.classify(prompt) == kind


@pytest.mark.parametrize(
    "prompt",
    ["please turn tts on", "tts on now", '"stop"', "'shush'", "hello", "", "   ", "ttson", "tts", "on", "again please"],
)
def test_classify_rejects_near_misses(prompt):
    assert toggle.classify(prompt) is None


def test_normalise_drops_only_the_six_punctuation_marks():
    assert toggle.normalise("  TTS,   on.!?;: ") == "tts on"
    assert toggle.normalise('"stop"') == '"stop"'
    assert toggle.normalise("it's-fine (really)") == "it's-fine (really)"


# ==========================================================================
# toggle — run()
# ==========================================================================
def test_run_exit_codes_messages_flags_and_log_lines(home, tmp_path, capsys):
    calls = []

    def replay_fn(sid):
        calls.append(("replay", sid))
        return 0 if (tmp_path / "recorded").exists() else 1

    def shush_fn():
        calls.append(("shush",))

    kw = {"replay_fn": replay_fn, "shush_fn": shush_fn}

    # on: arms the full session id, logs, says "voice on"
    assert toggle.run(_payload("TTS on!"), home, **kw) == 2
    assert capsys.readouterr() == ("", "voice on\n")
    assert state.is_armed(SID, home)
    assert calls == []
    assert re.fullmatch(TS + f"TTS ON  session {SID[:8]}", _log_lines(home)[-1])

    # shush: stops, session stays armed
    assert toggle.run(_payload("stop"), home, **kw) == 2
    assert capsys.readouterr() == ("", "quiet\n")
    assert calls == [("shush",)]
    assert state.is_armed(SID, home)
    assert re.fullmatch(TS + f"SHUSH session {SID[:8]}", _log_lines(home)[-1])

    # replay with nothing recorded: the word, no log line; the hook does not shush
    assert toggle.run(_payload("again"), home, **kw) == 2
    assert capsys.readouterr() == ("", "nothing recorded yet\n")
    assert calls == [("shush",), ("replay", SID)]
    assert not any("REPLAY" in line for line in _log_lines(home))

    # replay with something recorded: the word and the log line
    (tmp_path / "recorded").touch()
    assert toggle.run(_payload("say that again"), home, **kw) == 2
    assert capsys.readouterr() == ("", "replaying\n")
    assert calls[-1] == ("replay", SID)
    assert re.fullmatch(TS + f"REPLAY session {SID[:8]}", _log_lines(home)[-1])

    # off: disarms only this session, shushes, logs "+ shushed"
    state.arm("other-session", home)
    assert toggle.run(_payload("tts off"), home, **kw) == 2
    assert capsys.readouterr() == ("", "voice off\n")
    assert calls[-1] == ("shush",)
    assert not state.is_armed(SID, home)
    assert state.is_armed("other-session", home)
    assert re.fullmatch(TS + f"TTS OFF session {SID[:8]} \\+ shushed", _log_lines(home)[-1])

    # every handled phrase wrote exactly one line
    assert len(_log_lines(home)) == 4


def test_run_ignores_everything_else_silently(home, capsys):
    boom = {"replay_fn": lambda sid: pytest.fail("no replay"), "shush_fn": lambda: pytest.fail("no shush")}
    for text in (
        _payload("please turn tts on"),
        _payload("hello"),
        json.dumps({"session_id": SID}),
        json.dumps({"session_id": SID, "prompt": ""}),
        json.dumps({"session_id": SID, "prompt": 7}),
        "not json at all",
        "",
        "[1, 2]",
        "null",
    ):
        assert toggle.run(text, home, **boom) == 0, text
        assert capsys.readouterr() == ("", "")
    assert not paths.hooks_dir(home).exists()
    assert not state.is_armed(SID, home)


def test_missing_session_id_uses_unknown(home, capsys):
    assert toggle.run(json.dumps({"prompt": "tts on"}), home, shush_fn=lambda: None, replay_fn=lambda s: 1) == 2
    assert capsys.readouterr().err == "voice on\n"
    assert state.is_armed("unknown", home)
    assert _log_lines(home)[-1].endswith("TTS ON  session unknown")


def test_tts_on_prunes_dead_sessions_first(home):
    """Prune then touch: flags of sessions with no transcript are dropped
    before this one is armed, so arming always leaves this flag in place."""
    proj = paths.projects_dir(home) / "-Users-me-work"
    proj.mkdir(parents=True)
    (proj / f"{SID}.jsonl").write_text("{}\n")
    (proj / "live-session.jsonl").write_text("{}\n")
    state.arm("live-session", home)
    state.arm("ghost-session", home)  # no transcript anywhere
    state.arm(SID, home)
    assert toggle.run(_payload("tts on"), home, shush_fn=lambda: None, replay_fn=lambda s: 1) == 2
    assert state.armed_sessions(home) == sorted([SID, "live-session"])


def test_toggle_shush_and_off_raise_the_stop_flag_by_default(home, capsys):
    """Without injected doubles the real in-process shush runs: the stop flag
    is raised and a stale player pid file is cleared."""
    state.arm(SID, home)
    state.write_pid(paths.player_pid(home), 2**22 + 1)
    assert toggle.run(_payload("shush"), home) == 2
    assert capsys.readouterr() == ("", "quiet\n")
    assert state.stop_requested(home)
    assert not paths.player_pid(home).exists()
    assert state.is_armed(SID, home)

    state.clear_stop(home)
    assert toggle.run(_payload("tts off"), home) == 2
    assert capsys.readouterr() == ("", "voice off\n")
    assert state.stop_requested(home)
    assert not state.is_armed(SID, home)


# ==========================================================================
# speak — the Stop hook in process
# ==========================================================================
ONE_LINER = "A short reply, one sentence long."
MARKDOWN = "**Bold** start, then `shush` it.\n\n- a bullet\n- another bullet"


@pytest.fixture
def hook(monkeypatch, tmp_path, home):
    """speak.run with the two things a pure test cannot do injected: the
    engine liveness check answers "up" and the detached player is recorded
    instead of spawned. Returns a helper: hook(turns, sid=SID, env=None)."""
    spawned = []

    def fake_spawn(argv, cwd=None, env=None):
        spawned.append([str(a) for a in argv])
        return 4242

    orig_mkdtemp = tempfile.mkdtemp
    monkeypatch.setattr(speak.procs, "spawn_detached", fake_spawn)
    monkeypatch.setattr(speak.engine_client, "up", lambda port, timeout=1.0: True)
    monkeypatch.setattr(tempfile, "mkdtemp", lambda **kw: orig_mkdtemp(dir=str(tmp_path), **kw))

    tp = tmp_path / "transcript.jsonl"

    def run(turns, sid=SID, env=None, transcript=None):
        if turns is not None:
            write_transcript(tp, turns)
        payload = hook_payload(sid, transcript_path=str(tp if transcript is None else transcript))
        return speak.run(payload, {} if env is None else env, home)

    run.spawned = spawned
    return run


def test_unarmed_session_is_silent(hook, home):
    assert hook([assistant_turn(ONE_LINER)]) == 0
    assert hook.spawned == []
    assert not paths.lastreply_dir(home).exists()
    assert not paths.speak_log(home).exists()


def test_armed_reply_saves_text_and_marker_before_the_player_is_spawned(hook, home, monkeypatch):
    state.arm(SID, home)
    lastreply = paths.lastreply_dir(home)
    seen = {}

    real_spawn = speak.procs.spawn_detached

    def spawn_and_look(argv, cwd=None, env=None):
        txt = lastreply / f"{SID}.txt"
        seen["text"] = txt.read_text(encoding="utf-8") if txt.exists() else None
        seen["tmp_left"] = (lastreply / f".{SID}.txt.tmp").exists()
        seen["marker"] = state.spoken_marker(SID, home).exists()
        seen["armed"] = state.is_armed(SID, home)
        return real_spawn(argv, cwd, env)

    monkeypatch.setattr(speak.procs, "spawn_detached", spawn_and_look)
    assert hook([user_turn("hi"), assistant_turn(MARKDOWN)]) == 0

    expected = speak.rewrite.rewrite(MARKDOWN)
    assert "**" not in expected and "`" not in expected
    assert seen == {"text": expected, "tmp_left": False, "marker": True, "armed": True}

    assert len(hook.spawned) == 1
    argv = hook.spawned[0]
    assert argv[:5] == [sys.executable, "-m", "talkback", "play", "kokoro"]
    workdir, save, logfile = argv[5:]
    assert (paths.lastreply_dir(home) / f"{SID}.pcm") == paths.lastreply_dir(home) / os.path.basename(save)
    assert save == str(lastreply / f"{SID}.pcm")
    assert logfile == str(paths.speak_log(home))
    assert Path(workdir, "text.txt").read_text(encoding="utf-8") == expected
    assert os.path.isfile(os.path.join(workdir, "payload.json"))
    assert os.path.isfile(os.path.join(workdir, "key"))

    lines = _log_lines(home)
    assert len(lines) == 1
    assert re.fullmatch(TS + f"speaking {len(expected)} chars via kokoro \\(pid 4242\\)", lines[0])
    marker = state.spoken_marker(SID, home).read_text(encoding="utf-8").strip()
    assert marker == hashlib.sha1(expected.encode("utf-8")).hexdigest()[:16]
    assert len(marker) == 16


def test_previous_player_is_shushed_before_the_new_reply(hook, home):
    """The Stop hook stops whatever is still talking: the stop flag is raised
    and the player's pid file is cleared (a dead pid here) before the new
    player is spawned — which clears the flag itself when it starts."""
    state.arm(SID, home)
    state.write_pid(paths.player_pid(home), 2**22 + 1)
    assert hook([assistant_turn(ONE_LINER)]) == 0
    assert state.stop_requested(home)
    assert not paths.player_pid(home).exists()
    assert len(hook.spawned) == 1


def test_duplicate_within_two_minutes_is_suppressed_and_spoken_again_after(hook, home):
    state.arm(SID, home)
    assert hook([assistant_turn(ONE_LINER)]) == 0
    assert len(hook.spawned) == 1

    assert hook(None) == 0
    assert len(hook.spawned) == 1, "the same text within 120 s is not spoken twice"
    lines = _log_lines(home)
    assert re.fullmatch(TS + r"duplicate reply suppressed \(\d+s since the same text\)", lines[-1])
    assert sum("speaking " in line for line in lines) == 1

    _age(state.spoken_marker(SID, home), 130)
    assert hook(None) == 0
    assert len(hook.spawned) == 2, "after two minutes the same text is spoken again"
    lines = _log_lines(home)
    assert sum("speaking " in line for line in lines) == 2
    assert sum("duplicate reply suppressed" in line for line in lines) == 1


def test_changed_reply_is_not_a_duplicate(hook, home):
    state.arm(SID, home)
    assert hook([assistant_turn(ONE_LINER)]) == 0
    second = "A different reply this time."
    assert hook([assistant_turn(ONE_LINER), user_turn("more"), assistant_turn(second)]) == 0
    assert len(hook.spawned) == 2
    assert "duplicate reply suppressed" not in "\n".join(_log_lines(home))
    assert (paths.lastreply_dir(home) / f"{SID}.txt").read_text(encoding="utf-8") == second
    marker = state.spoken_marker(SID, home).read_text(encoding="utf-8").strip()
    assert marker == hashlib.sha1(second.encode("utf-8")).hexdigest()[:16]


def test_duplicate_guard_is_per_session(hook, home):
    state.arm(SID, home)
    state.arm("other-session", home)
    assert hook([assistant_turn(ONE_LINER)]) == 0
    assert hook([assistant_turn(ONE_LINER)], sid="other-session") == 0
    assert len(hook.spawned) == 2, "another session saying the same words is not a duplicate"
    assert state.spoken_marker(SID, home).is_file()
    assert state.spoken_marker("other-session", home).is_file()


def test_missing_or_nonexistent_transcript_logs_no_transcript(hook, home, tmp_path):
    state.arm(SID, home)
    assert speak.run(hook_payload(SID), {}, home) == 0
    assert hook(None, transcript=tmp_path / "gone.jsonl") == 0
    lines = _log_lines(home)
    assert len(lines) == 2
    for line in lines:
        assert re.fullmatch(TS + "no transcript", line)
    assert hook.spawned == []
    assert list(paths.lastreply_dir(home).iterdir()) == []


def test_empty_reply_and_missing_key_reasons_land_in_the_log_timestamped(hook, home):
    state.arm(SID, home)
    assert hook([user_turn("run it"), tool_turn("Bash", command="ls")]) == 0
    assert re.fullmatch(TS + "empty text", _log_lines(home)[-1])

    assert hook([assistant_turn(ONE_LINER)], env={"TTS_ENGINE": "elevenlabs"}) == 0
    assert re.fullmatch(TS + "no api key", _log_lines(home)[-1])

    assert hook.spawned == []
    assert list(paths.lastreply_dir(home).iterdir()) == []
    assert "speaking " not in "\n".join(_log_lines(home))


def test_elevenlabs_reply_is_saved_as_pcm_and_skips_the_engine_check(hook, home, monkeypatch):
    monkeypatch.setattr(speak.engine_client, "up", lambda port, timeout=1.0: pytest.fail("elevenlabs never asks the local engine"))
    state.arm(SID, home)
    env = {"TTS_ENGINE": "elevenlabs", "ELEVENLABS_API_KEY": "k-123", "TTS_VOICE_ID": "v-1"}
    assert hook([assistant_turn(ONE_LINER)], env=env) == 0
    argv = hook.spawned[0]
    assert argv[3:5] == ["play", "elevenlabs"]
    assert argv[6] == str(paths.lastreply_dir(home) / f"{SID}.pcm")
    assert not any(a.endswith(".mp3") for a in argv)
    assert "k-123" not in " ".join(argv), "the key stays in the workdir, never on the command line"
    assert Path(argv[5], "key").read_text(encoding="utf-8") == "k-123"
    assert re.fullmatch(TS + rf"speaking {len(ONE_LINER)} chars via elevenlabs \(pid 4242\)", _log_lines(home)[-1])


def test_housekeeping_drops_lastreply_files_older_than_two_days(hook, home):
    state.arm(SID, home)
    lastreply = paths.lastreply_dir(home)
    lastreply.mkdir(parents=True)
    old_txt = lastreply / "old-session.txt"
    old_pcm = lastreply / "old-session.pcm"
    old_marker = lastreply / ".spoken-old-session"
    fresh = lastreply / "fresh-session.txt"
    for p in (old_txt, old_pcm, old_marker, fresh):
        p.write_text("x", encoding="utf-8")
    for p in (old_txt, old_pcm, old_marker):
        _age(p, 3 * 86400 + 60)
    _age(fresh, 2 * 86400)  # find -mtime +2 keeps a file until it is three days old

    assert hook([assistant_turn(ONE_LINER)]) == 0
    assert not old_txt.exists() and not old_pcm.exists() and not old_marker.exists()
    assert fresh.exists()
    assert (lastreply / f"{SID}.txt").exists()


def test_engine_down_is_logged_before_a_start_is_attempted(hook, home, monkeypatch):
    import talkback.server

    order = []
    monkeypatch.setattr(speak.engine_client, "up", lambda port, timeout=1.0: order.append(("up", port)) or False)
    monkeypatch.setattr(talkback.server, "start", lambda env, home=None: order.append(("start", _log_lines(home)[-1])) or 1)
    state.arm(SID, home)
    assert hook([assistant_turn(ONE_LINER)], env={"KOKORO_PORT": "8931"}) == 0
    assert order[0] == ("up", 8931)
    assert order[1][0] == "start"
    assert re.fullmatch(TS + "engine not answering on 8931; starting it", order[1][1]), "logged before the start"
    assert len(hook.spawned) == 1, "a failed start does not stop the reply: the player makes it loud"
    assert "speaking " in _log_lines(home)[-1]


def test_invalid_hook_json_is_silent(home):
    for text in ("", "not json", "[1]", "null"):
        assert speak.run(text, {}, home) == 0
    assert not paths.speak_log(home).exists()


# ==========================================================================
# replay — select()
# ==========================================================================
def _text(d, sid, text="A saved reply.", age=0):
    p = d / f"{sid}.txt"
    p.write_text(text, encoding="utf-8")
    if age:
        _age(p, age)
    return p


def _pcm(d, sid, age=0):
    p = d / f"{sid}.pcm"
    p.write_bytes(b"\x01\x00" * 3000)
    if age:
        _age(p, age)
    return p


def test_text_with_engine_wins(tmp_path):
    txt = _text(tmp_path, "sid1")
    _pcm(tmp_path, "sid1")
    assert replay.select(tmp_path, "sid1", engine_up=True) == ("text", "sid1", txt)


def test_text_without_engine_falls_back_to_recording(tmp_path):
    _text(tmp_path, "sid1")
    pcm = _pcm(tmp_path, "sid1")
    assert replay.select(tmp_path, "sid1", engine_up=False) == ("file", "sid1", pcm)


def test_no_sid_picks_newest_text_by_mtime(tmp_path):
    newest = _text(tmp_path, "zz-latest", "newer")
    _text(tmp_path, "aa-stale", "older", age=300)
    assert replay.select(tmp_path, None, engine_up=True) == ("text", "zz-latest", newest)


def test_no_sid_and_no_text_picks_the_newest_recording(tmp_path):
    _pcm(tmp_path, "aa-old", age=300)
    newest = _pcm(tmp_path, "zz-new")
    assert replay.select(tmp_path, None, engine_up=True) == ("file", "zz-new", newest)
    assert replay.select(tmp_path, None, engine_up=False) == ("file", "zz-new", newest)


def test_no_sid_with_text_binds_the_session_so_only_its_own_recording_is_the_fallback(tmp_path):
    """bin/replay: the newest text names the session; with the engine down the
    fallback is THAT session's recording — never another session's newest."""
    _text(tmp_path, "sid1")
    _pcm(tmp_path, "aa-old", age=300)
    _pcm(tmp_path, "zz-new")
    assert replay.select(tmp_path, None, engine_up=False) is None
    own = _pcm(tmp_path, "sid1")
    assert replay.select(tmp_path, None, engine_up=False) == ("file", "sid1", own)


def test_empty_text_falls_through_to_recording(tmp_path):
    _text(tmp_path, "sid1", "")
    pcm = _pcm(tmp_path, "sid1")
    assert replay.select(tmp_path, "sid1", engine_up=True) == ("file", "sid1", pcm)


def test_engine_is_only_asked_when_there_is_text_to_respeak(tmp_path):
    asked = []

    def engine_up():
        asked.append(True)
        return True

    _pcm(tmp_path, "sid1")
    assert replay.select(tmp_path, "sid1", engine_up)[0] == "file"
    assert asked == []
    _text(tmp_path, "sid1")
    assert replay.select(tmp_path, "sid1", engine_up)[0] == "text"
    assert asked == [True]


def test_mp3_is_ignored(tmp_path):
    (tmp_path / "sid1.mp3").write_bytes(b"ID3" + b"\0" * 3000)
    assert replay.select(tmp_path, "sid1", engine_up=True) is None
    assert replay.select(tmp_path, None, engine_up=False) is None


def test_nothing_gives_none(tmp_path):
    assert replay.select(tmp_path, "ghost", engine_up=True) is None
    assert replay.select(tmp_path, None, engine_up=True) is None
    _text(tmp_path, "sid1")
    assert replay.select(tmp_path, "sid1", engine_up=False) is None
    assert replay.select(tmp_path / "missing-dir", None, engine_up=True) is None


# ==========================================================================
# recmode
# ==========================================================================
def _transcript_for(home, sid, project="-Users-me-work", age=0):
    d = paths.projects_dir(home) / project
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{sid}.jsonl"
    p.write_text("{}\n", encoding="utf-8")
    if age:
        _age(p, age)
    return p


def test_transcript_is_found_anywhere_under_projects(home):
    assert recmode.transcript("nope", home) is None
    p = _transcript_for(home, "s-1", project="-Users-me-deep/nested")
    assert recmode.transcript("s-1", home) == p


def test_prune_drops_flags_with_no_or_stale_transcript(home, capsys):
    _transcript_for(home, "live")
    _transcript_for(home, "stale", age=61 * 60)
    for sid in ("live", "stale", "ghost"):
        state.arm(sid, home)
    assert recmode.prune(home, dead_min=60) == 2
    assert state.armed_sessions(home) == ["live"]

    # RECMODE_DEAD_MIN from the environment given to run()
    state.arm("stale", home)
    assert recmode.run(["prune"], {"RECMODE_DEAD_MIN": "90"}, home) == 0
    assert capsys.readouterr().out == "pruned 0 dead session(s)\n"
    assert recmode.run(["prune"], {"RECMODE_DEAD_MIN": "30"}, home) == 0
    assert capsys.readouterr().out == "pruned 1 dead session(s)\n"
    assert state.armed_sessions(home) == ["live"]


def test_off_clears_every_flag_and_reports_the_count(home, capsys):
    for sid in ("a", "b", "c"):
        state.arm(sid, home)
    state.write_pid(paths.player_pid(home), 2**22 + 1)
    assert recmode.run(["off"], {}, home) == 0
    assert capsys.readouterr().out == "silenced 3 session(s)\n"
    assert state.armed_sessions(home) == []
    assert state.stop_requested(home), "off shushes whatever is playing"
    assert not paths.player_pid(home).exists()

    assert recmode.run(["off"], {}, home) == 0
    assert capsys.readouterr().out == "silenced 0 session(s)\n"


def test_list_output_strings(home, capsys):
    assert recmode.run([], {}, home) == 0
    assert capsys.readouterr().out == "OFF — no session is speaking\n"

    _transcript_for(home, SID, project="-Users-me-work")
    _transcript_for(home, "bbbbbbbb-2222", project="-Users-me-other", age=5 * 60)
    state.arm(SID, home)
    state.arm("bbbbbbbb-2222", home)
    state.arm("ghost-session", home)
    assert recmode.run([], {}, home) == 0
    assert capsys.readouterr().out == (
        "(pruned 1 dead)\n"
        "2 session(s) speaking:\n"
        f"  {SID[:8]}  -Users-me-work  (active 0m ago)\n"
        "  bbbbbbbb  -Users-me-other  (active 5m ago)\n"
    )
    assert state.armed_sessions(home) == sorted([SID, "bbbbbbbb-2222"])


def test_newest_session_within_two_hours(home):
    assert recmode.newest_session(home) is None
    _transcript_for(home, "too-old", age=3 * 3600)
    assert recmode.newest_session(home) is None
    _transcript_for(home, "older", age=30 * 60)
    _transcript_for(home, "newest", project="-Users-me-other", age=60)
    assert recmode.newest_session(home) == "newest"


def test_on_without_a_session_exits_1(home, capsys):
    assert recmode.run(["on"], {}, home) == 1
    assert capsys.readouterr().out == "no recent session — type 'TTS on' in the session you want\n"
    assert state.armed_sessions(home) == []


def test_on_arms_the_named_or_the_most_recent_session(home, capsys):
    assert recmode.run(["on", SID], {}, home) == 0
    assert capsys.readouterr().out == f"speaking: {SID[:8]}\n"
    assert state.is_armed(SID, home)

    _transcript_for(home, "recent-one", age=60)
    assert recmode.run(["on"], {}, home) == 0
    assert capsys.readouterr().out == "speaking: recent-o\n"
    assert state.is_armed("recent-one", home)
    assert not state.is_armed(SID, home), "on prunes first: a flag with no transcript is dropped"
