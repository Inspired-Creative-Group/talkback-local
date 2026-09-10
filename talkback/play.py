"""``talkback play``: the detached reply worker — what ``hooks/play_reply.sh``
did. One process per reply, the only thing that ever touches the audio
device.

Kokoro path plays SENTENCE BY SENTENCE: a whole long reply takes seconds to
generate but the first sentence lands in well under a second, so chunk N
plays while chunk N+1 is still being made. Each chunk is a finished buffer,
never a live pipe. The fetch of the next chunk is a thread of this process,
so a stop kills it with the process — nothing is left running.

There is exactly ONE playback per reply. Owner: player.
"""

import json
import os
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

from talkback import chunk, elevenlabs, engine_client, log, paths, player, state, verify

TEMPO_LINE = "TTS_TEMPO is ignored: playback speed is set at synthesis (KOKORO_SPEED / TTS_SPEED)"

# The spoken "Voice failed" announcement (verify.say_fail) lives in a scratch
# dir with this prefix; announce_file removes it once it has played.
FAIL_DIR_PREFIX = "talkback-fail-"
FAIL_FILE = "announce.pcm"


def looks_like_error_body(data: bytes) -> bool:
    """An engine or API error is a 200 with a JSON / HTML body: first byte
    ``{`` or ``<``. Such a chunk is never sent to the speaker."""
    return bytes(data[:1]) in (b"{", b"<")


def _rm(path: Path) -> None:
    try:
        Path(path).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass


def _rmtree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)


def _read_text(path: Path, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except OSError:
        return default


def _term_to_exit():
    """Make SIGTERM (what ``shush`` sends) unwind the worker through its
    ``finally`` blocks — stream closed, ``.part`` and workdir removed — instead
    of the default instant death. Returns the previous handler, or None when
    no handler could be installed (not the main thread, or Windows where the
    kill is TerminateProcess anyway)."""
    if threading.current_thread() is not threading.main_thread():
        return None
    try:
        return signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    except (ValueError, OSError, AttributeError):
        return None


def _restore(previous) -> None:
    if previous is not None:
        try:
            signal.signal(signal.SIGTERM, previous)
        except (ValueError, OSError, AttributeError):
            pass


def _play_or_log(data: bytes, log_path: Path, pid: int, home) -> bool:
    """play_bytes, with a device failure logged rather than silently lost
    (the worker runs detached; an uncaught error would vanish)."""
    try:
        return player.play_bytes(data, home)
    except Exception as e:  # noqa: BLE001  # any device error: the reply is still filed and verified
        log.append(log_path, f"PLAY error pid={pid}: {type(e).__name__}: {e}")
        return False


def _kokoro(workdir: Path, save: Path, log_path: Path, env, home, pid: int):
    """Chunk, fetch ahead, play. Returns (present_bytes, download_failed, stopped)."""
    chunks = chunk.split(_read_text(workdir / "text.txt"))
    for i, c in enumerate(chunks):  # the chunk files, as the shell worker wrote them
        try:
            (workdir / f"chunk{i:03d}.txt").write_text(c, encoding="utf-8")
        except OSError:
            pass
    n = len(chunks)
    log.append(log_path, f"PLAY start pid={pid} chunks={n} {save.name}")

    port = paths.port(env)
    results: list[bytes | None] = [None] * n

    def fetch(i: int) -> None:
        results[i] = engine_client.synth(port, chunks[i])

    dl = 0
    stopped = False
    if n:
        fetch(0)
        if results[0] is None:
            dl = 1
    for i in range(n):
        ahead = None
        if i + 1 < n:
            ahead = threading.Thread(target=fetch, args=(i + 1,), daemon=True)
            ahead.start()
        if state.stop_requested(home):
            stopped = True
            break  # the pending fetch dies with the process
        data = results[i]
        if data and not looks_like_error_body(data) and _play_or_log(data, log_path, pid, home):
            stopped = True  # the stop flag appeared between slices
            break
        if ahead is not None:
            ahead.join()
    log.append(log_path, f"PLAY done pid={pid} stopped={int(stopped)}")
    present = b"".join(r for r in results if r)
    return present, dl, stopped


def _elevenlabs(workdir: Path, save: Path, log_path: Path, env, home, pid: int):
    """Fetch the whole reply as pcm_24000, then play it. Returns
    (bytes_or_None, download_failed, stopped)."""
    preroll = env.get("TTS_PREROLL", "0") or "0"
    if preroll != "0":
        try:
            time.sleep(float(preroll))
        except ValueError:
            pass
    key = _read_text(workdir / "key").strip()
    voice = _read_text(workdir / "voice").strip()
    try:
        payload = json.loads(_read_text(workdir / "payload.json", "{}"))
    except ValueError:
        payload = {}
    data = elevenlabs.fetch(voice, key, payload, env)
    dl = 1 if data is None else 0
    log.append(log_path, f"PLAY start pid={pid} elevenlabs {save.name}")
    stopped = False
    if data and not looks_like_error_body(data):
        stopped = _play_or_log(data, log_path, pid, home)
    log.append(log_path, f"PLAY done pid={pid}")
    return data, dl, stopped


def reply(engine: str, workdir: Path, save: Path, log_path: Path, env, home=None) -> int:
    """``play <engine> <workdir> <savepath> <logfile>``. Always 0.

    1. clear the stop flag; hold the pid file for the life of the process.
    2. ``TTS_TEMPO`` set → one log line, then ignored.
    3. kokoro: chunk, fetch chunk 0, then fetch n+1 while n plays; the stop
       flag between chunks ends the reply. elevenlabs: one fetch, one play.
    4. concatenate to ``<save>.part``; verify; publish ``<save>`` only when
       every chunk arrived and nothing stopped it; never leave ``.part``.
    5. remove the workdir.
    """
    workdir, save, log_path = Path(workdir), Path(save), Path(log_path)
    part = save.with_name(save.name + ".part")
    previous = _term_to_exit()
    published = False
    state.clear_stop(home)
    try:
        with player.hold_pid(home):
            pid = os.getpid()
            if env.get("TTS_TEMPO"):
                log.append(log_path, TEMPO_LINE)
            if engine == "kokoro":
                data, dl, stopped = _kokoro(workdir, save, log_path, env, home, pid)
                data = data or b""  # an empty part: "only 0 bytes came back", as `cat` left it
            else:
                data, dl, stopped = _elevenlabs(workdir, save, log_path, env, home, pid)
            if data is not None:
                save.parent.mkdir(parents=True, exist_ok=True)
                part.write_bytes(data)
            # An engine error is a 200 response with a JSON body, not audio — check before filing.
            if not verify.verify(part, engine, log_path, env, home):
                return 0
            # Publish only a complete reply. An interrupted one is dropped: `replay`
            # re-speaks from the saved text, so a partial recording has no reader.
            if dl == 0 and not stopped:
                os.replace(part, save)
                published = True
            else:
                _rm(save)
            return 0
    finally:
        if not published:
            _rm(part)
        _rmtree(workdir)
        _restore(previous)


def announce_file(path: Path, log_path: Path, home=None) -> int:
    """``play --file <pcm> --log <logfile>``: play a recording (a ``replay``
    fallback or the spoken "Voice failed" announcement). Nothing is written
    to the log on this path; it is passed so the process can be found."""
    path = Path(path)
    previous = _term_to_exit()
    try:
        with player.hold_pid(home):
            try:
                player.play_file(path, home)
            except Exception as e:  # noqa: BLE001  # a device error must not leave the pid file behind
                log.append(Path(log_path), f"PLAY error pid={os.getpid()}: {type(e).__name__}: {e}")
        return 0
    finally:
        if path.name == FAIL_FILE and path.parent.name.startswith(FAIL_DIR_PREFIX):
            _rmtree(path.parent)  # the announcement's scratch dir, ours to remove
        _restore(previous)
