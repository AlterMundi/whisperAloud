"""Daemon paste-backend wiring (WHISP-119).

The RemoteDesktop portal is opt-in: the daemon only prewarms (opens) a
RemoteDesktop session when clipboard.paste_backend == "remote-desktop".
"""

import pytest

from tests._daemon_harness import make_daemon


@pytest.fixture
def daemon():
    service = make_daemon()
    service._test_mode = False
    yield service
    service._patches.close()


class TestPrewarmGate:
    def test_no_prewarm_by_default(self, daemon):
        assert daemon.config.clipboard.paste_backend == "auto"
        assert daemon._paste_backend_needs_prewarm() is False

    @pytest.mark.parametrize("value", ["auto", "extension", "clipboard"])
    def test_no_prewarm_unless_remote_desktop(self, daemon, value):
        daemon.config.clipboard.paste_backend = value
        assert daemon._paste_backend_needs_prewarm() is False

    def test_prewarm_when_remote_desktop_opted_in(self, daemon):
        daemon.config.clipboard.paste_backend = "remote-desktop"
        assert daemon._paste_backend_needs_prewarm() is True

    def test_no_prewarm_without_auto_paste(self, daemon):
        daemon.config.clipboard.paste_backend = "remote-desktop"
        daemon.config.clipboard.auto_paste = False
        assert daemon._paste_backend_needs_prewarm() is False

    def test_no_prewarm_in_test_mode(self, daemon):
        daemon.config.clipboard.paste_backend = "remote-desktop"
        daemon._test_mode = True
        assert daemon._paste_backend_needs_prewarm() is False
