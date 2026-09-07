#!/bin/bash
# Decide whether a downloaded file is actually audio, and make a failure loud.
# Usage: verify_audio.sh <file> <engine> <logfile>
# Exit 0 = real audio (caller publishes it). Exit 1 = not audio (caller discards).
F="$1"; ENGINE="$2"; LOG="$3"
say_fail() {   # a failure must never be silent — that is the whole bug
  local msg="$1"
  echo "$(date '+%F %T') TTS FAILED [$ENGINE]: $msg" >> "$LOG"
  osascript -e "display notification \"${msg//\"/}\" with title \"Voice failed ($ENGINE)\"" >/dev/null 2>&1
  # the local engine is free and independent — use it to announce a cloud failure
  if [ "$ENGINE" != "kokoro" ] && curl -sf --max-time 1 "http://127.0.0.1:${KOKORO_PORT:-8910}/" >/dev/null 2>&1; then
    curl -sS -X POST "http://127.0.0.1:${KOKORO_PORT:-8910}/" \
      --data-binary "Voice failed. $msg" 2>/dev/null \
    | ffplay -f s16le -ar 24000 -ch_layout mono -nodisp -autoexit -loglevel quiet -infbuf - 2>/dev/null &
  fi
}

[ -f "$F" ] || { say_fail "nothing was downloaded"; exit 1; }
SIZE=$(stat -f %z "$F" 2>/dev/null || echo 0)

# an API error is a 200 response with a JSON or HTML body, not audio
HEAD=$(head -c 1 "$F" 2>/dev/null)
case "$HEAD" in
  "{"|"<")
    DETAIL=$(python3 - "$F" <<'PY' 2>/dev/null
import json,sys
raw=open(sys.argv[1],errors="replace").read()[:2000]
try:
    d=json.loads(raw)
    while isinstance(d,dict):
        for k in ("message","detail","error"):
            if k in d: d=d[k]; break
        else: break
    print(str(d)[:180])
except Exception:
    print(raw.strip().replace("\n"," ")[:180])
PY
)
    say_fail "${DETAIL:-the service returned an error instead of audio}"
    exit 1 ;;
esac

# too small to be speech: 2000 bytes is well under a second in every format we use
if [ "$SIZE" -lt 2000 ]; then
  say_fail "only ${SIZE} bytes came back — not enough to be speech"
  exit 1
fi
exit 0
