"""The engine's HTTP server (``talkback server``) and its start/stop/status/
restart verbs (what ``bin/kokoro-server`` did). Owner: server."""

import threading
from dataclasses import dataclass


@dataclass
class Server:
    handler: type
    port: int
    device: str
    backend: str
    voice: object
    pipe: object
    reqlog: str
    lock: threading.Lock


def boot(voice_path: "str | None" = None, env=None, home=None) -> Server:
    """Load the backend named by ``TALKBACK_ENGINE`` (torch default | onnx),
    warm up once, build the handler class for this Server."""
    raise NotImplementedError("talkback.server.boot — server implementer")


def serve(server: Server, home=None) -> None:
    """Write server.pid, print the ready line, serve_forever; clear the pid on exit."""
    raise NotImplementedError("talkback.server.serve — server implementer")


def start(env, home=None) -> int:
    raise NotImplementedError("talkback.server.start — server implementer")


def stop(env, home=None) -> int:
    raise NotImplementedError("talkback.server.stop — server implementer")


def status(env, home=None) -> int:
    raise NotImplementedError("talkback.server.status — server implementer")


def restart(env, home=None) -> int:
    raise NotImplementedError("talkback.server.restart — server implementer")
