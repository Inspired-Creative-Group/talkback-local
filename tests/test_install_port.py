"""The port the hooks will talk to is checked before install.sh writes a
single file: held by a stranger, the installer refuses; free, it proceeds;
held by a Talkback server already answering, it is an upgrade."""

import socket

import pytest
import test_install

# The install harness lives in test_install.py (its fixtures need the
# sandbox, so they cannot sit in the frozen conftest). Re-binding them here
# makes pytest collect them for this module under their own names.
installer = test_install.installer
source = test_install.source

REFUSAL = "is in use by something that is not Talkback"


@pytest.fixture
def silent_listener():
    """A socket that accepts the TCP connection and never answers, on the
    first free port in the test range (never 8910)."""
    for port in range(8930, 8950):
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            s.close()
            continue
        s.listen(1)
        yield port
        s.close()
        return
    pytest.fail("no free port in 8930-8949")


def test_busy_port_aborts_before_changing_anything(installer, silent_listener):
    port = silent_listener
    r = installer.run(env=dict(installer.env, KOKORO_PORT=str(port)))
    assert r.returncode != 0
    assert f"Port {port} {REFUSAL}" in r.stdout + r.stderr
    assert "Set KOKORO_PORT to a free port and re-run." in r.stdout + r.stderr
    assert not installer.settings.exists()
    installer.nothing_written()


def test_free_port_proceeds(installer):
    # the sandbox's KOKORO_PORT is a dead port: nothing listens there
    assert installer.env["KOKORO_PORT"] == "8999"
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert REFUSAL not in r.stdout + r.stderr
    assert "Installed." in r.stdout


def test_port_held_by_a_running_talkback_server_is_accepted(fake_engine, installer):
    # fake_engine is requested first so the installer's environment carries its port
    assert installer.env["KOKORO_PORT"] == str(fake_engine.port)
    r = installer.run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert REFUSAL not in r.stdout + r.stderr
    assert f"a Talkback server already answers on port {fake_engine.port}" in r.stdout
    assert "Installed." in r.stdout
    assert f"<string>{fake_engine.port}</string>" in installer.plist.read_text()
