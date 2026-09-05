#!/bin/bash
# Talkback installer. Idempotent — safe to re-run to upgrade.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
ENGINE="$HOME/.claude/automation/kokoro"
HOOKS="$HOME/.claude/automation/notifications"
PLIST="$HOME/Library/LaunchAgents/com.icg.talkback.plist"

say() { printf "  %s\n" "$1"; }

[ "$(uname)" = "Darwin" ] || { echo "macOS only — playback and the launch agent are Apple-specific."; exit 1; }
command -v ffplay >/dev/null || { echo "Missing ffmpeg. Run: brew install ffmpeg"; exit 1; }
command -v espeak-ng >/dev/null || { echo "Missing espeak-ng. Run: brew install espeak-ng"; exit 1; }
command -v jq >/dev/null || { echo "Missing jq. Run: brew install jq"; exit 1; }
command -v uv >/dev/null || { echo "Missing uv. See https://docs.astral.sh/uv/"; exit 1; }

say "engine -> $ENGINE"
mkdir -p "$ENGINE" "$HOOKS" "$HOME/bin" \
         "$HOME/.claude/automation/recording" "$HOME/.claude/automation/lastreply"
cp "$HERE/engine/server.py" "$HERE/engine/icg_voice.pt" "$ENGINE/"

if [ ! -d "$ENGINE/.venv" ]; then
  say "building the python environment (about a minute)"
  uv venv --python 3.12 "$ENGINE/.venv" >/dev/null
fi
uv pip install --python "$ENGINE/.venv/bin/python" -r "$HERE/engine/requirements.txt" -q
# spacy's own downloader silently no-ops inside a uv venv; install the wheel by URL
"$ENGINE/.venv/bin/python" -c "import spacy;spacy.load('en_core_web_sm')" 2>/dev/null || \
  uv pip install --python "$ENGINE/.venv/bin/python" -q \
    "https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"

say "hooks -> $HOOKS"
cp "$HERE"/hooks/* "$HOOKS/"
chmod +x "$HOOKS"/*.sh

say "commands -> ~/bin"
cp "$HERE"/bin/* "$HOME/bin/"
chmod +x "$HOME/bin/shush" "$HOME/bin/replay" "$HOME/bin/recmode" "$HOME/bin/kokoro-server"

say "launch agent -> $PLIST"
sed "s|__HOME__|$HOME|g" "$HERE/launchd/com.icg.talkback.plist.template" > "$PLIST"
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

say "registering the Claude Code hooks"
python3 - "$HOOKS" <<'PY'
import json, os, sys
hooks_dir = sys.argv[1]
p = os.path.expanduser("~/.claude/settings.json")
d = json.load(open(p)) if os.path.exists(p) else {}
h = d.setdefault("hooks", {})
def ensure(event, cmd):
    blocks = h.setdefault(event, [{"matcher": "", "hooks": []}])
    entries = blocks[0].setdefault("hooks", [])
    if not any(cmd in e.get("command", "") for e in entries):
        entries.append({"type": "command", "command": cmd})
        print(f"    added {event}")
ensure("Stop", f"{hooks_dir}/speak_last_reply.sh")
ensure("UserPromptSubmit", f"{hooks_dir}/tts_toggle.sh")
json.dump(d, open(p, "w"), indent=2)
PY

say ""
say "Installed. Wait ~10s for the model to load, then in any Claude Code session:"
say "    TTS on     start speaking      TTS off   stop"
say "    shush      cut it off          replay    hear it again"
say "    recmode    which sessions are speaking"
