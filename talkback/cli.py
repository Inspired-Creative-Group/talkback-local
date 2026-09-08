"""``python -m talkback <subcommand>`` — also the ``talkback`` console script.

Eight subcommands, each in its own module and imported only when chosen, so
the Stop hook never pays for the server's imports and vice versa. No
subcommand or an unknown one: usage on stderr, exit 2.
"""

import argparse
import os
import sys
from pathlib import Path

SUBCOMMANDS = ("speak", "toggle", "shush", "replay", "recmode", "server", "play", "verify")

DESCRIPTION = "Claude Code reads its replies aloud through a local voice engine."

EPILOG = """\
subcommands:
  speak                                  Stop hook: speak the last reply (hook JSON on stdin)
  toggle                                 UserPromptSubmit hook: "tts on", "shush", "again", "tts off" (hook JSON on stdin)
  shush [quiet]                          stop the reply that is playing
  replay [sid]                           say a session's last reply again
  recmode [on [sid] | off | prune]       which sessions are speaking
  server [start|stop|status|restart]     the local engine; no argument runs it in the foreground
  play <engine> <workdir> <save> <log>   the detached reply worker (internal)
  play --file <pcm> --log <log>          play a recording (internal)
  verify <file> <engine> <log>           is the file audio? exit 0 yes, 1 no (internal)
"""


def _stdin_text() -> str:
    """The hook payload: bytes decoded as UTF-8 whatever the console encoding."""
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        return sys.stdin.read() if sys.stdin else ""
    return stream.read().decode("utf-8", "replace")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="talkback",
        description=DESCRIPTION,
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", metavar="<subcommand>")
    sub.required = True

    sub.add_parser("speak", help="Stop hook (hook JSON on stdin)")
    sub.add_parser("toggle", help="UserPromptSubmit hook (hook JSON on stdin)")

    s = sub.add_parser("shush", help="stop the reply that is playing")
    s.add_argument("mode", nargs="?", help='"quiet" prints nothing')

    s = sub.add_parser("replay", help="say a session's last reply again")
    s.add_argument("sid", nargs="?")

    s = sub.add_parser("recmode", help="which sessions are speaking")
    s.add_argument("args", nargs="*")

    s = sub.add_parser("server", help="the local engine")
    s.add_argument("verb", nargs="?", choices=("start", "stop", "status", "restart"))

    s = sub.add_parser("play", help="the detached reply worker (internal)")
    s.add_argument("--file", dest="file", default=None, help="a recording to play")
    s.add_argument("--log", dest="log", default=None, help="the speak.log path")
    s.add_argument("args", nargs="*", help="<engine> <workdir> <save> <log>")

    s = sub.add_parser("verify", help="is the file audio? (internal)")
    s.add_argument("file")
    s.add_argument("engine")
    s.add_argument("logfile")
    return p


# -- one dispatcher per subcommand; the module import is the first line -----
def _speak(ns) -> int:
    from talkback import speak

    return speak.run(_stdin_text(), os.environ)


def _toggle(ns) -> int:
    from talkback import toggle

    return toggle.run(_stdin_text())


def _shush(ns) -> int:
    from talkback import shush

    shush.shush(quiet=ns.mode == "quiet")
    return 0


def _replay(ns) -> int:
    from talkback import replay

    return replay.run(ns.sid, os.environ)


def _recmode(ns) -> int:
    from talkback import recmode

    return recmode.run(list(ns.args), os.environ)


def _server(ns) -> int:
    from talkback import server

    if ns.verb is None:
        server.serve(server.boot(env=os.environ))
        return 0
    return int(getattr(server, ns.verb)(os.environ) or 0)


def _play(ns, parser) -> int:
    from talkback import play

    if ns.file is not None:
        if ns.log is None or ns.args:
            parser.error("play --file <pcm> --log <logfile>")
        return play.announce_file(Path(ns.file), Path(ns.log))
    if ns.log is not None or len(ns.args) != 4:
        parser.error("play <engine> <workdir> <savepath> <logfile>")
    engine, workdir, save, log = ns.args
    return play.reply(engine, Path(workdir), Path(save), Path(log), os.environ)


def _verify(ns) -> int:
    from talkback import verify

    return 0 if verify.verify(Path(ns.file), ns.engine, Path(ns.logfile), os.environ) else 1


def main(argv=None) -> int:
    """Parse ``argv`` (default ``sys.argv[1:]``) and run the subcommand.
    Returns the exit code; usage errors return 2 after printing to stderr."""
    parser = build_parser()
    try:
        ns = parser.parse_args(sys.argv[1:] if argv is None else list(argv))
        if ns.cmd == "speak":
            return _speak(ns)
        if ns.cmd == "toggle":
            return _toggle(ns)
        if ns.cmd == "shush":
            return _shush(ns)
        if ns.cmd == "replay":
            return _replay(ns)
        if ns.cmd == "recmode":
            return _recmode(ns)
        if ns.cmd == "server":
            return _server(ns)
        if ns.cmd == "play":
            return _play(ns, parser)
        if ns.cmd == "verify":
            return _verify(ns)
        parser.error(f"unknown subcommand {ns.cmd!r}")
    except SystemExit as e:  # argparse's usage errors (2) and --help (0)
        code = e.code
        return code if isinstance(code, int) else 2
    return 2
