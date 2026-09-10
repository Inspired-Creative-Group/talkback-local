#!/bin/bash
# Talkback installer. Idempotent — safe to re-run to upgrade.
set -e
trap 'echo "  INSTALL FAILED at line $LINENO. Nothing further was changed." >&2; exit 1' ERR
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$HOME/.claude/automation/kokoro"
HOOKS="$HOME/.claude/automation/notifications"
PLIST="$HOME/Library/LaunchAgents/com.icg.talkback.plist"
VENV_PY="$ENGINE/.venv/bin/python"
PORT="${KOKORO_PORT:-8910}"

say() { printf "  %s\n" "$1"; }
# The Python half of the installer (settings.json merge, template and shim
# rendering, the port check) runs on the system python3 straight from the
# checkout — the engine venv does not exist yet on a first run.
tb() { PYTHONPATH="$HERE" python3 -m talkback.install "$@"; }

[ "$(uname)" = "Darwin" ] || { echo "macOS only — the launch agent is Apple-specific. Windows: see install.ps1."; exit 1; }
command -v espeak-ng >/dev/null || { echo "Missing espeak-ng. Run: brew install espeak-ng"; exit 1; }
command -v uv >/dev/null || { echo "Missing uv. See https://docs.astral.sh/uv/"; exit 1; }
command -v python3 >/dev/null || { echo "Missing python3. Run: xcode-select --install"; exit 1; }

# The port the hooks will talk to must be ours or nobody's — checked before a
# single file is written. A Talkback server already answering means an upgrade.
PORT_STATE=$(tb port "$PORT") || exit 1
[ "$PORT_STATE" = "talkback" ] && say "a Talkback server already answers on port $PORT — upgrading in place"

say "engine -> $ENGINE"
mkdir -p "$ENGINE" "$HOOKS" "$HOME/bin" "$HOME/Library/LaunchAgents" \
         "$HOME/.claude/automation/recording" "$HOME/.claude/automation/lastreply"
cp "$HERE/engine/server.py" "$HERE/engine/icg_voice.pt" "$ENGINE/"

if [ ! -d "$ENGINE/.venv" ]; then
  say "building the python environment (about a minute)"
  uv venv --python 3.12 "$ENGINE/.venv" >/dev/null
fi
uv pip install --python "$VENV_PY" -r "$HERE/engine/requirements.txt" -q
# the package itself (hooks, CLI, player — sounddevice comes with it); the
# onnx backend only when asked for at install time
PKG="$HERE"; [ "${TALKBACK_ENGINE:-}" = "onnx" ] && PKG="${HERE}[onnx]"
uv pip install --python "$VENV_PY" -q "$PKG"
# spacy's own downloader silently no-ops inside a uv venv; install the wheel by URL
"$VENV_PY" -c "import spacy;spacy.load('en_core_web_sm')" 2>/dev/null || \
  uv pip install --python "$VENV_PY" -q \
    "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

# Shell files are shims around the package; the installed copies carry the
# venv interpreter's absolute path, so the hooks need nothing on PATH.
say "hooks -> $HOOKS"
for f in "$HERE"/hooks/*; do
  [ -f "$f" ] || continue          # skips __pycache__ and the like
  case "$f" in
    *.sh) tb shim "$f" "$HOOKS/$(basename "$f")" "$VENV_PY" ;;
    *)    cp "$f" "$HOOKS/" ;;
  esac
done

say "commands -> ~/bin"
for f in "$HERE"/bin/*; do
  [ -f "$f" ] || continue
  tb shim "$f" "$HOME/bin/$(basename "$f")" "$VENV_PY"
done

say "launch agent -> $PLIST"
tb render "$HERE/launchd/com.icg.talkback.plist.template" "$PLIST" "HOME=$HOME" "PORT=$PORT"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

say "registering the Claude Code hooks"
tb settings "$VENV_PY"

# verify rather than assume
MISSING=""
for f in "$ENGINE/server.py" "$ENGINE/icg_voice.pt" "$VENV_PY" \
         "$HOOKS/speak_last_reply.sh" "$HOOKS/tts_toggle.sh" "$HOOKS/play_reply.sh" "$HOOKS/verify_audio.sh" \
         "$HOME/bin/talkback" "$HOME/bin/shush" "$HOME/bin/replay" \
         "$HOME/bin/recmode" "$HOME/bin/kokoro-server" "$PLIST"; do
  [ -e "$f" ] || MISSING="$MISSING\n    $f"
done
if [ -n "$MISSING" ]; then
  printf "  INSTALL INCOMPLETE — missing:%b\n" "$MISSING" >&2
  exit 1
fi

say ""
say "Installed. Wait ~10s for the model to load, then in any Claude Code session:"
say "    TTS on     start speaking      TTS off   stop"
say "    shush      cut it off          replay    hear it again"
say "    recmode    which sessions are speaking"
