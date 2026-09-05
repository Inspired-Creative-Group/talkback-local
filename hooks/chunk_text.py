import sys, re
# Split into playable chunks on sentence boundaries. Small first chunk so the
# first words start fast; larger after, since by then playback is the bottleneck.
text = open(sys.argv[1]).read().strip()
out  = sys.argv[2]
sents = re.split(r'(?<=[.!?])\s+', text)
chunks, cur, first = [], "", True
for s in sents:
    limit = 120 if first else 400
    if cur and len(cur) + len(s) > limit:
        chunks.append(cur.strip()); cur = s; first = False
    else:
        cur = (cur + " " + s).strip()
        if first and len(cur) >= limit:
            chunks.append(cur); cur = ""; first = False
if cur.strip():
    chunks.append(cur.strip())
for i, c in enumerate(chunks):
    open(f"{out}/chunk{i:03d}.txt", "w").write(c)
print(len(chunks))
