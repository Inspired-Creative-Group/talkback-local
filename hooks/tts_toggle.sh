#!/bin/bash
# UserPromptSubmit hook: flip recording-mode TTS for THIS SESSION ONLY when the
# prompt is exactly "tts on" / "tts off". Anything longer is ignored, so talking
# *about* the command never trips it.
DIR="$HOME/.claude/automation/recording"
LOG="$HOME/.claude/automation/notifications/speak.log"
mkdir -p "$DIR"
INPUT=$(cat)
SID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null)
P=$(echo "$INPUT" | jq -r '.prompt // empty' 2>/dev/null \
    | tr '[:upper:]' '[:lower:]' | tr -d '.,!?;:' | xargs)
case "$P" in
  "tts on"|"tts  on")
    "$HOME/bin/recmode" prune >/dev/null 2>&1          # drop flags for sessions that have gone quiet
    touch "$DIR/$SID"
    echo "$(date '+%F %T') TTS ON  session ${SID:0:8}" >> "$LOG"
    echo "voice on" >&2
    exit 2 ;;
  "shush"|"stop"|"quiet"|"be quiet")
    "$HOME/bin/shush" quiet 2>/dev/null
    echo "$(date '+%F %T') SHUSH session ${SID:0:8}" >> "$LOG"
    echo "quiet" >&2
    exit 2 ;;
  "replay"|"again"|"repeat"|"say that again"|"repeat that"|"one more time")
    # one implementation of replay lives in ~/bin/replay; don't fork it here
    if "$HOME/bin/replay" "$SID" >/dev/null 2>&1; then
      echo "$(date '+%F %T') REPLAY session ${SID:0:8}" >> "$LOG"
      echo "replaying" >&2
    else
      echo "nothing recorded yet" >&2
    fi
    exit 2 ;;
  "tts off"|"tts  off")
    rm -f "$DIR/$SID"; "$HOME/bin/shush" quiet 2>/dev/null
    echo "$(date '+%F %T') TTS OFF session ${SID:0:8} + shushed" >> "$LOG"
    echo "voice off" >&2
    exit 2 ;;
esac
exit 0
