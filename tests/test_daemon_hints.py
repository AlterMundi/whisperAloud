"""Unit tests for daemon_hints.py (field bugs 3 & 5: Flatpak-aware hints)."""

from whisper_aloud.daemon_hints import (
    FLATPAK_RUN_DAEMON_CMD,
    cli_daemon_unavailable_hint,
    gui_daemon_unavailable_hint,
)


class TestGuiDaemonUnavailableHint:
    def test_flatpak_hint_mentions_flatpak_run_command(self):
        hint = gui_daemon_unavailable_hint(is_flatpak=True, has_user_service=False)
        assert FLATPAK_RUN_DAEMON_CMD in hint
        assert "systemctl" not in hint
        assert "install.sh" not in hint

    def test_flatpak_hint_ignores_has_user_service(self):
        """systemd user units are never relevant inside the sandbox."""
        with_service = gui_daemon_unavailable_hint(is_flatpak=True, has_user_service=True)
        without_service = gui_daemon_unavailable_hint(is_flatpak=True, has_user_service=False)
        assert with_service == without_service

    def test_non_flatpak_with_user_service_mentions_systemctl(self):
        hint = gui_daemon_unavailable_hint(is_flatpak=False, has_user_service=True)
        assert "systemctl --user start whisper-aloud" in hint
        assert FLATPAK_RUN_DAEMON_CMD not in hint

    def test_non_flatpak_without_user_service_mentions_install_sh(self):
        hint = gui_daemon_unavailable_hint(is_flatpak=False, has_user_service=False)
        assert "install.sh" in hint
        assert FLATPAK_RUN_DAEMON_CMD not in hint


class TestCliDaemonUnavailableHint:
    def test_flatpak_hint_mentions_flatpak_run_command(self):
        hint = cli_daemon_unavailable_hint(is_flatpak=True)
        assert FLATPAK_RUN_DAEMON_CMD in hint
        assert "systemctl" not in hint

    def test_non_flatpak_hint_mentions_systemctl_and_daemon_flag(self):
        hint = cli_daemon_unavailable_hint(is_flatpak=False)
        assert "whisper-aloud --daemon" in hint
        assert "systemctl --user start whisper-aloud" in hint
        assert FLATPAK_RUN_DAEMON_CMD not in hint
