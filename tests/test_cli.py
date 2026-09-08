"""talkback.cli — the dispatcher. Pure: every subcommand module is
monkeypatched, so nothing runs but the argument parsing."""

import io
import json
from pathlib import Path

import pytest

from talkback import cli

pytestmark = pytest.mark.pure


def test_no_argument_or_unknown_subcommand_is_usage_and_exit_2(capsys):
    assert cli.main([]) == 2
    err = capsys.readouterr().err
    assert "usage: talkback" in err
    assert cli.main(["bogus"]) == 2
    assert "usage: talkback" in capsys.readouterr().err


def test_help_lists_the_eight_subcommands(capsys):
    assert cli.main(["--help"]) == 0
    out = capsys.readouterr().out
    for name in cli.SUBCOMMANDS:
        assert name in out
    assert len(cli.SUBCOMMANDS) == 8


def test_speak_and_toggle_read_the_hook_json_from_stdin(monkeypatch):
    seen = {}
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(json.dumps({"session_id": "s1", "prompt": "tts on"}).encode())))
    import talkback.speak
    import talkback.toggle

    monkeypatch.setattr(talkback.speak, "run", lambda text, env, home=None: seen.setdefault("speak", (text, "HOME" in env or "USERPROFILE" in env)) and 0)
    assert cli.main(["speak"]) == 0
    assert json.loads(seen["speak"][0])["session_id"] == "s1"

    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b'{"session_id": "s2", "prompt": "shush"}')))
    monkeypatch.setattr(talkback.toggle, "run", lambda text, home=None, **kw: seen.setdefault("toggle", text) and 2)
    assert cli.main(["toggle"]) == 2
    assert json.loads(seen["toggle"])["prompt"] == "shush"


def test_shush_replay_recmode_dispatch(monkeypatch):
    import talkback.recmode
    import talkback.replay
    import talkback.shush

    calls = []
    monkeypatch.setattr(talkback.shush, "shush", lambda home=None, quiet=False: calls.append(("shush", quiet)) or True)
    assert cli.main(["shush"]) == 0
    assert cli.main(["shush", "quiet"]) == 0
    assert calls == [("shush", False), ("shush", True)]

    monkeypatch.setattr(talkback.replay, "run", lambda sid, env, home=None: calls.append(("replay", sid)) or 1)
    assert cli.main(["replay"]) == 1
    assert cli.main(["replay", "abc"]) == 1
    assert calls[-2:] == [("replay", None), ("replay", "abc")]

    monkeypatch.setattr(talkback.recmode, "run", lambda argv, env, home=None: calls.append(("recmode", argv)) or 0)
    assert cli.main(["recmode"]) == 0
    assert cli.main(["recmode", "on", "sid9"]) == 0
    assert calls[-2:] == [("recmode", []), ("recmode", ["on", "sid9"])]


def test_server_dispatch(monkeypatch):
    import talkback.server

    calls = []
    monkeypatch.setattr(talkback.server, "boot", lambda voice_path=None, env=None, home=None: "SRV")
    monkeypatch.setattr(talkback.server, "serve", lambda s, home=None: calls.append(("serve", s)))
    assert cli.main(["server"]) == 0
    assert calls == [("serve", "SRV")]
    for verb in ("start", "stop", "status", "restart"):
        monkeypatch.setattr(talkback.server, verb, lambda env, home=None, v=verb: calls.append(v) or 0)
        assert cli.main(["server", verb]) == 0
    assert calls[1:] == ["start", "stop", "status", "restart"]
    assert cli.main(["server", "dance"]) == 2


def test_play_dispatch(monkeypatch, capsys):
    import talkback.play

    calls = []
    monkeypatch.setattr(talkback.play, "reply", lambda engine, workdir, save, log, env, home=None: calls.append((engine, workdir, save, log)) or 0)
    monkeypatch.setattr(talkback.play, "announce_file", lambda path, log, home=None: calls.append(("file", path, log)) or 0)
    assert cli.main(["play", "kokoro", "/w", "/s.pcm", "/l.log"]) == 0
    assert calls == [("kokoro", Path("/w"), Path("/s.pcm"), Path("/l.log"))]
    assert cli.main(["play", "--file", "/a.pcm", "--log", "/l.log"]) == 0
    assert calls[-1] == ("file", Path("/a.pcm"), Path("/l.log"))
    assert cli.main(["play", "kokoro", "/w"]) == 2
    assert cli.main(["play", "--file", "/a.pcm"]) == 2
    assert "usage" in capsys.readouterr().err
    assert len(calls) == 2


def test_verify_dispatch(monkeypatch):
    import talkback.verify

    monkeypatch.setattr(talkback.verify, "verify", lambda path, engine, log, env, home=None: path.name == "good.pcm")
    assert cli.main(["verify", "/x/good.pcm", "kokoro", "/l.log"]) == 0
    assert cli.main(["verify", "/x/bad.pcm", "kokoro", "/l.log"]) == 1
    assert cli.main(["verify", "/x/bad.pcm"]) == 2
