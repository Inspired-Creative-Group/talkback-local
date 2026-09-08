"""talkback.chunk.split() — the sentence splitter as a function. Pure: no
sandbox, no subprocess; the CLI contract is pinned by test_chunk_text.py."""

import re

import pytest

from talkback import chunk

pytestmark = pytest.mark.pure

SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
WORDS = ("the", "voice", "reads", "each", "reply", "aloud", "without", "cutting", "a", "sentence", "in", "half")


def make_sentences(n):
    out = []
    for i in range(n):
        count = (i * 7) % 15 + 3
        words = [WORDS[(i + 3 * j) % len(WORDS)] for j in range(count)]
        out.append(" ".join(words).capitalize() + ".!?"[i % 3])
    return out


def exact(n, ch):
    return ch * (n - 1) + "."


def test_limits_are_120_then_400():
    assert (chunk.FIRST_LIMIT, chunk.LATER_LIMIT) == (120, 400)
    sents = make_sentences(60)
    chunks = chunk.split(" ".join(sents))
    assert len(chunks) >= 3
    assert len(chunks[0]) <= 120
    for c in chunks[1:]:
        assert len(c) <= 400, c


def test_sentences_are_never_cut_and_all_come_back():
    sents = make_sentences(40)
    seps = (" ", "\n", "\n\n", "  ")
    text = "".join(s + seps[i % len(seps)] for i, s in enumerate(sents))
    chunks = chunk.split(text)
    assert len(chunks) > 1
    for c in chunks:
        assert c[-1] in ".!?"
        assert c == c.strip()
    assert [s for c in chunks for s in SENTENCE_END.split(c)] == sents


@pytest.mark.parametrize("text", ["Hello there, this is a reply.", "Is the recording on?", "Done!", "A fragment with no full stop at all"])
def test_one_sentence_is_one_chunk(text):
    assert chunk.split(text) == [text]


def test_over_long_sentence_is_kept_whole():
    long = exact(201, "x")
    assert chunk.split(long) == [long]
    assert chunk.split(long + " Then a short one.") == [long, "Then a short one."]


@pytest.mark.parametrize("text", ["", "   \n\n\t \n"], ids=["empty", "whitespace-only"])
def test_empty_gives_no_chunks(text):
    assert chunk.split(text) == []


@pytest.mark.parametrize(
    "lead, a, b",
    [("", 100, 19), ("", 100, 20), ("First. ", 380, 19), ("First. ", 380, 20)],
    ids=["first-at-120", "first-over-by-the-space", "later-at-400", "later-over-by-the-space"],
)
def test_exact_sum_boundary_counts_the_joining_space(lead, a, b):
    chunks = chunk.split(lead + exact(a, "a") + " " + exact(b, "b") + " Tail.")
    assert len(chunks[0]) <= 120
    for c in chunks[1:]:
        assert len(c) <= 400
    if not lead and b == 19:
        assert chunks[0] == exact(100, "a") + " " + exact(19, "b")  # 120 exactly: joined
    if not lead and b == 20:
        assert chunks[0] == exact(100, "a")  # 121: the second sentence starts chunk two


def test_main_writes_numbered_files_and_prints_the_count(tmp_path, capsys):
    src = tmp_path / "text.txt"
    src.write_text(" ".join(make_sentences(20)), encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    assert chunk.main(["chunk_text.py", str(src), str(out)]) == 0
    n = int(capsys.readouterr().out.strip())
    files = sorted(p.name for p in out.iterdir())
    assert files == [f"chunk{i:03d}.txt" for i in range(n)]
    assert [(out / f).read_text(encoding="utf-8") for f in files] == chunk.split(src.read_text(encoding="utf-8"))
