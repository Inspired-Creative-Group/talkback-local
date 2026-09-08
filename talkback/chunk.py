"""Split a reply into playable chunks on sentence boundaries: a small first
chunk so the first words start fast, larger ones after, since by then
playback is the bottleneck. The algorithm of ``hooks/chunk_text.py``."""

import os
import re

FIRST_LIMIT = 120
LATER_LIMIT = 400
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split(text: str) -> "list[str]":
    """Sentences never cut; the first chunk ≤120 chars, later ones ≤400
    (the joining space counts); an over-long sentence is kept whole.
    Empty or whitespace-only text → ``[]``."""
    text = text.strip()
    sents = SENTENCE_END.split(text)
    chunks, cur, first = [], "", True
    for s in sents:
        limit = FIRST_LIMIT if first else LATER_LIMIT
        if cur and len(cur) + 1 + len(s) > limit:   # +1: the joining space below
            chunks.append(cur.strip()); cur = s; first = False
        else:
            cur = (cur + " " + s).strip()
            if first and len(cur) >= limit:
                chunks.append(cur); cur = ""; first = False
    if cur.strip():
        chunks.append(cur.strip())
    return chunks


def main(argv) -> int:
    """chunk_text.py <textfile> <outdir>: writes chunk000.txt, chunk001.txt, …
    into <outdir> and prints the count."""
    with open(argv[1], encoding="utf-8") as f:
        text = f.read()
    out = argv[2]
    chunks = split(text)
    for i, c in enumerate(chunks):
        with open(os.path.join(out, f"chunk{i:03d}.txt"), "w", encoding="utf-8") as f:
            f.write(c)
    print(len(chunks))
    return 0
