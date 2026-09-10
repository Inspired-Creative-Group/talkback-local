"""The kept shell scripts are shims: a shebang, one comment, one ``exec`` of
``python -m talkback <subcommand>`` — nothing else, so nothing but the
package can change behaviour. The two ``.py`` hooks import from ``talkback``.
Pure: file reads only."""

import re

import pytest

pytestmark = pytest.mark.pure

SHIMS = {
    "hooks/speak_last_reply.sh": 'speak "$@"',
    "hooks/tts_toggle.sh": 'toggle "$@"',
    "hooks/play_reply.sh": 'play "$@"',
    "hooks/verify_audio.sh": 'verify "$@"',
    "bin/shush": 'shush "$@"',
    "bin/replay": 'replay "$@"',
    "bin/recmode": 'recmode "$@"',
    "bin/kokoro-server": 'server "${@:-start}"',
    "bin/talkback": '"$@"',
}

EXEC = re.compile(r'^exec "\$\{TALKBACK_PYTHON:-python3\}" -m talkback (.+)$')


@pytest.mark.parametrize("rel, tail", sorted(SHIMS.items()))
def test_shell_file_is_shebang_comment_and_one_exec(repo, rel, tail):
    lines = (repo / rel).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3, f"{rel}: a shim is exactly three lines"
    assert lines[0] == "#!/bin/bash"
    assert lines[1].startswith("# Talkback Local")
    m = EXEC.match(lines[2])
    assert m, f"{rel}: {lines[2]!r}"
    assert m.group(1) == tail


def test_every_shell_file_in_hooks_and_bin_is_listed(repo):
    found = sorted(
        [f"hooks/{p.name}" for p in (repo / "hooks").glob("*.sh")]
        + [f"bin/{p.name}" for p in (repo / "bin").iterdir() if p.is_file()]
    )
    assert found == sorted(SHIMS)


def test_python_hooks_import_from_the_package(repo):
    slr = (repo / "hooks" / "speak_last_reply.py").read_text(encoding="utf-8")
    chunk = (repo / "hooks" / "chunk_text.py").read_text(encoding="utf-8")
    assert "from talkback.rewrite import" in slr
    assert "from talkback.chunk import main" in chunk
    for text in (slr, chunk):
        assert "def rewrite" not in text and "def split" not in text, "no logic in a shim"
        assert 'if __name__ == "__main__":' in text
