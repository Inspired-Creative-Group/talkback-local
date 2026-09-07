#!/bin/bash
# Stop hook — speak Claude's last reply aloud, for THIS session only.
#
# Armed per session:  ~/.claude/automation/recording/<session_id>
# Toggle:             type "TTS on" / "TTS off" as the whole prompt
# Stop mid-reply:     shush            Repeat:  replay
#
# Engine:   TTS_ENGINE=kokoro (default, local, free) | elevenlabs
# Tunables: TTS_TEMPO (playback speed, both engines)
#           kokoro:     KOKORO_PORT
#           elevenlabs: TTS_MODEL TTS_SPEED TTS_STABILITY TTS_STYLE TTS_SIMILARITY
#                       TTS_NORMALIZE TTS_FORMAT TTS_LATOPT TTS_PREROLL
INPUT=$(cat)
SID=$(echo "$INPUT" | jq -r '.session_id // "unknown"' 2>/dev/null)
[ -f "$HOME/.claude/automation/recording/$SID" ] || exit 0

N="$HOME/.claude/automation/notifications"
LR="$HOME/.claude/automation/lastreply"
LOG="$N/speak.log"
mkdir -p "$LR"

TP=$(echo "$INPUT" | jq -r '.transcript_path // empty' 2>/dev/null)
if [ -z "$TP" ] || [ ! -f "$TP" ]; then
  echo "$(date '+%F %T') no transcript" >> "$LOG"; exit 0
fi

# never let two replies talk over each other
"$HOME/bin/shush" quiet 2>/dev/null

TMP=$(mktemp -d)
CHARS=$(python3 "$N/speak_last_reply.py" "$TP" "$TMP" 2>>"$LOG") || { rm -rf "$TMP"; exit 0; }

ENGINE="${TTS_ENGINE:-kokoro}"
EXT="pcm"; [ "$ENGINE" = "elevenlabs" ] && EXT="mp3"
SAVE="$LR/$SID.$EXT"

# Belt-and-braces against speaking one reply twice. The real cause of the
# 2026-09-05 double-playback was a leftover second ffplay in play_reply.sh, not
# a double hook fire — but a cheap guard here costs nothing.
HASH=$(shasum -a 1 "$TMP/text.txt" 2>/dev/null | cut -c1-16)
SEEN="$LR/.spoken-$SID"
if [ -f "$SEEN" ] && [ "$(cat "$SEEN" 2>/dev/null)" = "$HASH" ]; then
  AGE=$(( $(date +%s) - $(stat -f %m "$SEEN" 2>/dev/null || echo 0) ))
  if [ "$AGE" -lt 120 ]; then
    echo "$(date '+%F %T') duplicate reply suppressed (${AGE}s since the same text)" >> "$LOG"
    rm -rf "$TMP"; exit 0
  fi
fi
echo "$HASH" > "$SEEN"

# housekeeping: saved audio and marker files from sessions long gone
find "$LR" -type f -mtime +2 -delete 2>/dev/null

if [ "$ENGINE" = "kokoro" ]; then
  curl -sf --max-time 1 "http://127.0.0.1:${KOKORO_PORT:-8910}/" >/dev/null 2>&1 \
    || "$HOME/bin/kokoro-server" start >/dev/null 2>&1
fi

# The ElevenLabs key stays in $TMP/key — never on the command line, where every
# `ps` on the machine would show it.
# Keep the text, not just the audio. The audio file is only written when a
# reply plays to the end, and a reply cut short — by shush, by "again", or by
# the next reply's own hook — is killed before that step, so it was never on
# disk at all. `replay` re-speaks from this file, so "again" is always the
# latest reply, whole, even mid-sentence. (Found live in a demo, 2026-09-07.)
cp "$TMP/text.txt" "$LR/.$SID.txt.tmp" && mv "$LR/.$SID.txt.tmp" "$LR/$SID.txt"

nohup "$N/play_reply.sh" "$ENGINE" "$TMP" "$SAVE" "$LOG" >/dev/null 2>&1 &

echo "$(date '+%F %T') speaking ${CHARS} chars via $ENGINE (pid $!)" >> "$LOG"
exit 0
