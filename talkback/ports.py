"""Is the port free, held by a Talkback server, or busy? Owner: installer."""


def check_port(port: int, timeout: float = 1.5) -> str:
    """``"free"`` | ``"talkback"`` | ``"busy"``."""
    raise NotImplementedError("talkback.ports.check_port — installer implementer")
