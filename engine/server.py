"""Compatibility entry (one release): the server lives in talkback.server.

Keeps ``python server.py`` and the import-time surface (H, PORT, DEVICE, VOICE,
PIPE, REQLOG, LOCK) working: importing this file boots the engine exactly as
launchd did — load the voice beside this file, build the pipeline, warm up
with "Ready." — and ``__main__`` serves it. New installs run
``python -m talkback server`` instead.
"""
import os

from talkback.server import boot, serve

HERE = os.path.dirname(os.path.abspath(__file__))
_S = boot(voice_path=os.path.join(HERE, "icg_voice.pt"))
H, PORT, DEVICE, VOICE, PIPE, REQLOG, LOCK = _S.handler, _S.port, _S.device, _S.voice, _S.pipe, _S.reqlog, _S.lock

if __name__ == "__main__":
    serve(_S)
