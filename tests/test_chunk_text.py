"""hooks/chunk_text.py cuts a reply into playable chunks: a small first chunk
so the voice starts fast, larger ones after, never splitting a sentence.

Every test runs the installed copy exactly as play_reply.sh does:
``python3 <hooks>/chunk_text.py <text file> <dir>``; it prints the chunk
count and writes chunk000.txt, chunk001.txt, ... into <dir>."""

import re
import tempfile
from pathlib import Path

import pytest

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")  # where a sentence boundary is
FIRST_LIMIT = 120
LATER_LIMIT = 400

WORDS = ("the", "voice", "reads", "each", "reply", "aloud", "without", "cutting", "a", "sentence", "in", "half")


def make_sentences(n):
    """n deterministic sentences of varied length (3 to 17 words), ending in a cycle of . ! ?"""
    out = []
    for i in range(n):
        count = (i * 7) % 15 + 3
        words = [WORDS[(i + 3 * j) % len(WORDS)] for j in range(count)]
        out.append(" ".join(words).capitalize() + ".!?"[i % 3])
    return out


def exact(n, ch):
    """A sentence of exactly n characters: n-1 letters and a full stop."""
    return ch * (n - 1) + "."


def chunk(sandbox, tmp_path, text):
    """Run chunk_text.py on ``text``. Returns (proc, chunk files in name order, their contents)."""
    work = Path(tempfile.mkdtemp(prefix="chunk-", dir=tmp_path))
    src = work / "text.txt"
    src.write_text(text)
    out = work / "out"
    out.mkdir()
    r = sandbox.run(["python3", sandbox.hooks / "chunk_text.py", src, out])
    assert r.returncode == 0, r.stderr
    files = sorted(out.iterdir())
    return r, files, [f.read_text() for f in files]


def test_first_chunk_fits_120_chars_and_later_chunks_fit_400(sandbox, tmp_path):
    sents = make_sentences(60)
    assert max(len(s) for s in sents) <= FIRST_LIMIT  # test-data guard: no single sentence forces a bigger chunk
    _, _, chunks = chunk(sandbox, tmp_path, " ".join(sents))
    assert len(chunks) >= 3  # the later limit is actually exercised
    assert len(chunks[0]) <= FIRST_LIMIT
    for c in chunks[1:]:
        assert len(c) <= LATER_LIMIT, c


def test_chunks_end_on_sentence_boundaries_and_keep_every_sentence_whole(sandbox, tmp_path):
    sents = make_sentences(40)
    # sentences separated by single spaces, newlines and blank lines, as a real reply is
    seps = (" ", "\n", "\n\n", "  ")
    text = "".join(s + seps[i % len(seps)] for i, s in enumerate(sents))
    _, _, chunks = chunk(sandbox, tmp_path, text)
    assert len(chunks) > 1
    for c in chunks:
        assert c[-1] in ".!?", c
    # walking the chunks sentence by sentence gives back the original sentences, none cut
    assert [s for c in chunks for s in SENTENCE_END.split(c)] == sents
    assert " ".join(chunks) == " ".join(SENTENCE_END.split(text.strip()))


@pytest.mark.parametrize(
    "text",
    [
        "Hello there, this is a reply.",
        "Is the recording on?",
        "Done!",
        "A fragment with no full stop at all",
    ],
)
def test_one_sentence_reply_is_exactly_one_chunk(sandbox, tmp_path, text):
    r, files, chunks = chunk(sandbox, tmp_path, text)
    assert r.stdout.strip() == "1"
    assert [f.name for f in files] == ["chunk000.txt"]
    assert chunks == [text]


LONG = exact(201, "x")


@pytest.mark.parametrize(
    "text, expected",
    [
        (LONG, [LONG]),
        (LONG + " Then a short one.", [LONG, "Then a short one."]),
    ],
    ids=["alone", "followed-by-another"],
)
def test_single_sentence_over_120_chars_is_kept_whole(sandbox, tmp_path, text, expected):
    # Observed behaviour: an over-long sentence is not cut. It becomes one whole
    # chunk (longer than the first-chunk limit) and the next sentence starts chunk two.
    assert len(LONG) > FIRST_LIMIT
    _, _, chunks = chunk(sandbox, tmp_path, text)
    assert chunks == expected


def test_no_chunk_file_is_empty_or_padded(sandbox, tmp_path):
    sents = make_sentences(30)
    text = "\n\n" + "\n\n\n".join(sents) + "\n\n"
    _, files, chunks = chunk(sandbox, tmp_path, text)
    assert files
    for f, c in zip(files, chunks):
        assert c.strip(), f"{f.name} is empty"
        assert c == c.strip(), f"{f.name} carries surrounding whitespace"


@pytest.mark.parametrize("text", ["", "   \n\n\t \n"], ids=["empty", "whitespace-only"])
def test_empty_input_prints_zero_and_writes_nothing(sandbox, tmp_path, text):
    r, files, _ = chunk(sandbox, tmp_path, text)
    assert r.stdout.strip() == "0"
    assert files == []


@pytest.mark.parametrize(
    "text",
    ["", "Just one.", " ".join(make_sentences(60))],
    ids=["none", "one", "many"],
)
def test_printed_count_matches_the_chunk_files_written(sandbox, tmp_path, text):
    r, files, _ = chunk(sandbox, tmp_path, text)
    n = int(r.stdout.strip())
    assert n == len(files)
    assert [f.name for f in files] == [f"chunk{i:03d}.txt" for i in range(n)]


@pytest.mark.parametrize(
    "lead, a, b",
    [
        ("", 100, 19),  # joined: 100 + space + 19 = 120, exactly the first limit
        ("", 100, 20),  # joined: 100 + space + 20 = 121
        ("First. ", 380, 19),  # after chunk one; joined: 380 + space + 19 = 400
        ("First. ", 380, 20),  # after chunk one; joined: 380 + space + 20 = 401
    ],
    ids=["first-at-120", "first-over-by-the-space", "later-at-400", "later-over-by-the-space"],
)
def test_chunk_limits_hold_when_two_sentences_sum_exactly_to_the_limit(sandbox, tmp_path, lead, a, b):
    text = lead + exact(a, "a") + " " + exact(b, "b") + " Tail."
    _, _, chunks = chunk(sandbox, tmp_path, text)
    assert len(chunks[0]) <= FIRST_LIMIT, len(chunks[0])
    for c in chunks[1:]:
        assert len(c) <= LATER_LIMIT, len(c)
