#!/bin/bash
# Fetch, play, verify and file one spoken reply. Runs detached from the hook.
#   play_reply.sh <engine> <tmpdir> <savepath> <logfile>
# The ElevenLabs key is read from <tmpdir>/key, never passed as an argument.
#
# Kokoro path plays SENTENCE BY SENTENCE: a whole long reply takes seconds to
# generate but the first sentence lands in ~0.6s, so chunk N plays while chunk
# N+1 is still being made. Each chunk is a finished file — ffplay only skips
# ahead when it thinks it is reading a live stream it has fallen behind on.
#
# ffplay, not mpv: mpv renders mono as a single channel and macOS puts that in
# the left ear. ffplay asks CoreAudio for stereo and the voice sits centred.
#
# There is exactly ONE playback per reply. A second one left over from an
# earlier revision made every reply play through twice (2026-09-05).
ENGINE="$1"; TMP="$2"; SAVE="$3"; LOG="$4"
HERE="$(cd "$(dirname "$0")" && pwd)"
PART="$SAVE.part"
STOPFLAG="$HOME/.claude/automation/.tts-stop"
TEMPO="${TTS_TEMPO:-1.0}"
STOPPED=0

AF=(); [ "$TEMPO" = "1.0" ] || AF=(-af "atempo=$TEMPO")
play_pcm() { ffplay -f s16le -ar 24000 -ch_layout mono -nodisp -autoexit -loglevel quiet "${AF[@]}" "$1"; }

rm -f "$STOPFLAG"

if [ "$ENGINE" = "kokoro" ]; then
  URL="http://127.0.0.1:${KOKORO_PORT:-8899}/"
  COUNT=$(python3 "$HERE/chunk_text.py" "$TMP/text.txt" "$TMP" 2>>"$LOG")
  [ -z "$COUNT" ] && COUNT=0
  fetch() { curl -sS -o "$TMP/a$1.pcm" -X POST "$URL" --data-binary @"$TMP/chunk$(printf %03d "$1").txt"; }

  echo "$(date '+%F %T') PLAY start pid=$$ chunks=$COUNT $(basename "$SAVE")" >> "$LOG"
  DL=0
  [ "$COUNT" -gt 0 ] && { fetch 0 || DL=1; }
  i=0
  while [ "$i" -lt "$COUNT" ]; do
    n=$((i+1))
    FPID=""
    [ "$n" -lt "$COUNT" ] && { fetch "$n" & FPID=$!; }
    if [ -f "$STOPFLAG" ]; then STOPPED=1; [ -n "$FPID" ] && kill "$FPID" 2>/dev/null; break; fi
    [ -s "$TMP/a$i.pcm" ] && play_pcm "$TMP/a$i.pcm"
    [ -n "$FPID" ] && wait "$FPID"
    i=$n
  done
  echo "$(date '+%F %T') PLAY done pid=$$ stopped=$STOPPED" >> "$LOG"
  cat "$TMP"/a*.pcm > "$PART" 2>/dev/null
else
  [ "${TTS_PREROLL:-0}" != "0" ] && sleep "${TTS_PREROLL:-0}"
  KEY=$(cat "$TMP/key" 2>/dev/null)
  VOICE=$(cat "$TMP/voice" 2>/dev/null)
  curl -sS -o "$PART" -X POST \
    "https://api.elevenlabs.io/v1/text-to-speech/$VOICE/stream?optimize_streaming_latency=${TTS_LATOPT:-2}&output_format=${TTS_FORMAT:-mp3_44100_128}" \
    -H "xi-api-key: $KEY" -H "Content-Type: application/json" \
    --data @"$TMP/payload.json"
  DL=$?
  echo "$(date '+%F %T') PLAY start pid=$$ elevenlabs $(basename "$SAVE")" >> "$LOG"
  [ -s "$PART" ] && ffplay -nodisp -autoexit -loglevel quiet "${AF[@]}" "$PART"
  echo "$(date '+%F %T') PLAY done pid=$$" >> "$LOG"
fi

# An engine error is a 200 response with a JSON body, not audio — check before filing.
if ! "$HERE/verify_audio.sh" "$PART" "$ENGINE" "$LOG"; then
  rm -f "$PART"; rm -rf "$TMP"; exit 0
fi

# Publish only a complete reply. A shush leaves a partial recording that replay
# must not present as the whole answer.
if [ "$DL" = "0" ] && [ "$STOPPED" = "0" ]; then
  mv "$PART" "$SAVE"
else
  rm -f "$SAVE" 2>/dev/null      # the .part stays; replay falls back to it and says so
fi
rm -rf "$TMP"
