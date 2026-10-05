"""Daemon paste-backend wiring (WHISP-119).

- The RemoteDesktop portal is opt-in: the daemon only prewarms (opens) a
  RemoteDesktop session when clipboard.paste_backend == "remote-desktop",
  and switching away from it closes the session.
- After the clipboard copy completes, auto-paste goes to the GNOME Shell
  extension's InsertText when it reports the capability (with the captured
  window id for the atomic WHISP-88 focus check), else ydotool/xdotool
  natively, else clipboard only with a "press Ctrl+V" notice.
- InsertText outcomes map onto the existing notifications.
"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from tests._daemon_harness import make_daemon
from whisper_aloud.clipboard import text_insertion as ti


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


# ── auto-paste dispatch after the clipboard copy completes ─────────────────


def _result(text="hello world"):
    result = MagicMock()
    result.text = text
    result.confidence = 0.9
    result.duration = 1.0
    result.language = "en"
    return result


def _install_clipboard(daemon_obj):
    captured = {}
    clipboard = MagicMock()
    clipboard._session_type = "wayland"

    def _copy(text, on_complete=None):
        captured["on_complete"] = on_complete
        return True

    clipboard.copy.side_effect = _copy
    daemon_obj.clipboard_manager = clipboard
    return captured


@pytest.fixture
def paste_daemon(daemon):
    daemon.config.clipboard.auto_copy = True
    daemon.config.clipboard.auto_paste = True
    daemon.config.clipboard.paste_to_captured_window = True
    daemon.config.clipboard.paste_delay_ms = 0
    daemon.notifications = MagicMock()
    daemon._text_inserter = MagicMock()
    daemon._text_inserter.can_insert = False
    daemon.window_manager = MagicMock()
    return daemon


def _emit(daemon_obj, text="hello world", job="job-1"):
    daemon_obj._emit_transcription_success(
        _result(text), np.zeros(16000, dtype=np.float32), job, "sess-1", 0, token=None
    )


def _transcribe(daemon_obj, text="hello world", sandboxed=False, paste_ok=True):
    captured = _install_clipboard(daemon_obj)
    with patch("whisper_aloud.clipboard.build_paste_simulator") as build_sim, \
            patch("whisper_aloud.sandbox.is_flatpak_sandbox", return_value=sandboxed):
        sim = MagicMock()
        sim.simulate_paste.return_value = paste_ok
        build_sim.return_value = sim
        _emit(daemon_obj, text)
        captured["on_complete"](True)
    return build_sim, sim


class TestBackendDispatch:
    def test_extension_preferred_when_capable(self, paste_daemon):
        paste_daemon._text_inserter.can_insert = True
        build_sim, _ = _transcribe(paste_daemon, "hola mundo")
        build_sim.assert_not_called()
        paste_daemon._text_inserter.insert.assert_called_once()
        args, kwargs = paste_daemon._text_inserter.insert.call_args
        assert args == ("hola mundo",)
        assert kwargs["window_id"] is None

    def test_extension_preferred_in_flatpak(self, paste_daemon):
        paste_daemon._text_inserter.can_insert = True
        build_sim, _ = _transcribe(paste_daemon, sandboxed=True)
        build_sim.assert_not_called()
        paste_daemon._text_inserter.insert.assert_called_once()

    def test_native_without_extension_uses_keystroke_tools(self, paste_daemon):
        build_sim, sim = _transcribe(paste_daemon)
        assert build_sim.call_args.kwargs["backend"] == "keystroke"
        sim.simulate_paste.assert_called_once()
        paste_daemon._text_inserter.insert.assert_not_called()

    def test_flatpak_without_extension_is_clipboard_only_with_notice(self, paste_daemon):
        build_sim, _ = _transcribe(paste_daemon, sandboxed=True)
        build_sim.assert_not_called()
        paste_daemon._text_inserter.insert.assert_not_called()
        paste_daemon.notifications.show_clipboard_notice.assert_called_once_with(
            ti.NOTICE_MESSAGES[ti.NOTICE_COPIED])

    def test_flatpak_auto_never_builds_the_portal(self, paste_daemon):
        """Regression guard for the orange indicator: in auto, no
        RemoteDesktop simulator is ever built."""
        from whisper_aloud.clipboard import paste_selection

        paste_selection.reset_portal_simulator_for_tests()
        captured = _install_clipboard(paste_daemon)
        with patch("whisper_aloud.sandbox.is_flatpak_sandbox", return_value=True), \
                patch.object(paste_selection, "PortalPasteSimulator") as portal_cls:
            _emit(paste_daemon)
            captured["on_complete"](True)
        portal_cls.assert_not_called()

    def test_remote_desktop_opt_in(self, paste_daemon):
        paste_daemon.config.clipboard.paste_backend = "remote-desktop"
        paste_daemon._text_inserter.can_insert = True
        build_sim, sim = _transcribe(paste_daemon, sandboxed=True)
        assert build_sim.call_args.kwargs["backend"] == "remote-desktop"
        sim.simulate_paste.assert_called_once()
        paste_daemon._text_inserter.insert.assert_not_called()

    def test_clipboard_setting_never_pastes(self, paste_daemon):
        paste_daemon.config.clipboard.paste_backend = "clipboard"
        paste_daemon._text_inserter.can_insert = True
        build_sim, _ = _transcribe(paste_daemon)
        build_sim.assert_not_called()
        paste_daemon._text_inserter.insert.assert_not_called()
        paste_daemon.notifications.show_clipboard_notice.assert_called_once()

    def test_keystroke_failure_tells_user_to_press_ctrl_v(self, paste_daemon):
        _transcribe(paste_daemon, paste_ok=False)
        paste_daemon.notifications.show_clipboard_notice.assert_called_once_with(
            ti.NOTICE_MESSAGES[ti.NOTICE_COPIED])

    def test_missing_inserter_still_pastes_natively(self, paste_daemon):
        paste_daemon._text_inserter = None
        _, sim = _transcribe(paste_daemon)
        sim.simulate_paste.assert_called_once()


class TestExtensionWithCapturedWindow:
    def _captured(self, daemon_obj, backend="gnome-extension", window_id=4242):
        from whisper_aloud.clipboard.window_manager import WindowInfo

        info = WindowInfo(window_id=window_id, title="t", backend=backend)
        daemon_obj._captured_by_job["job-1"] = info
        return info

    def test_window_id_passed_after_focus_confirmed(self, paste_daemon):
        paste_daemon._text_inserter.can_insert = True
        info = self._captured(paste_daemon)
        _transcribe(paste_daemon)
        paste_daemon.window_manager.activate_window.assert_called_once_with(info)
        paste_daemon._text_inserter.insert.assert_not_called()
        confirm_cb = paste_daemon.window_manager.confirm_focus.call_args[0][1]
        confirm_cb(True)
        assert paste_daemon._text_inserter.insert.call_args.kwargs["window_id"] == 4242

    def test_focus_not_confirmed_skips_insertion(self, paste_daemon):
        paste_daemon._text_inserter.can_insert = True
        self._captured(paste_daemon)
        _transcribe(paste_daemon)
        paste_daemon.window_manager.confirm_focus.call_args[0][1](False)
        paste_daemon._text_inserter.insert.assert_not_called()
        paste_daemon.notifications.show_paste_skipped_focus_changed.assert_called_once()

    def test_xdotool_window_ids_are_not_passed_to_the_extension(self, paste_daemon):
        paste_daemon._text_inserter.can_insert = True
        self._captured(paste_daemon, backend="xdotool")
        _transcribe(paste_daemon)
        paste_daemon.window_manager.confirm_focus.call_args[0][1](True)
        assert paste_daemon._text_inserter.insert.call_args.kwargs["window_id"] is None


class TestInsertResultNotices:
    def _on_done(self, paste_daemon):
        paste_daemon._text_inserter.can_insert = True
        _transcribe(paste_daemon)
        return paste_daemon._text_inserter.insert.call_args.kwargs["on_done"]

    @pytest.mark.parametrize("method", ["commit", "paste"])
    def test_success_is_silent(self, paste_daemon, method):
        self._on_done(paste_daemon)(ti.InsertResult(method, ""))
        paste_daemon.notifications.show_clipboard_notice.assert_not_called()
        paste_daemon.notifications.show_paste_skipped_focus_changed.assert_not_called()

    def test_window_mismatch_is_focus_changed(self, paste_daemon):
        self._on_done(paste_daemon)(ti.InsertResult("none", "window-mismatch"))
        paste_daemon.notifications.show_paste_skipped_focus_changed.assert_called_once()

    def test_locked(self, paste_daemon):
        self._on_done(paste_daemon)(ti.InsertResult("none", "locked"))
        paste_daemon.notifications.show_clipboard_notice.assert_called_once_with(
            ti.NOTICE_MESSAGES[ti.NOTICE_LOCKED])

    def test_password(self, paste_daemon):
        self._on_done(paste_daemon)(ti.InsertResult("none", "password-field"))
        paste_daemon.notifications.show_clipboard_notice.assert_called_once_with(
            ti.NOTICE_MESSAGES[ti.NOTICE_PASSWORD])

    def test_call_failed_says_press_ctrl_v(self, paste_daemon):
        self._on_done(paste_daemon)(ti.InsertResult("none", ti.CALL_FAILED))
        paste_daemon.notifications.show_clipboard_notice.assert_called_once_with(
            ti.NOTICE_MESSAGES[ti.NOTICE_COPIED])


class TestShellExtensionWatcherFeedsInserter:
    def test_presence_forwarded_to_inserter(self):
        from tests.test_daemon import _make_daemon

        with patch("whisper_aloud.service.shell_extension_watch.ShellExtensionWatcher") as cls:
            d = _make_daemon()
        d._text_inserter = MagicMock()
        on_change = cls.call_args.args[0]
        on_change(True)
        d._text_inserter.on_extension_presence_changed.assert_called_with(True)
        on_change(False)
        d._text_inserter.on_extension_presence_changed.assert_called_with(False)

    def test_watcher_exists_without_tray_indicator(self):
        """The paste backend needs the extension's presence even when there
        is no tray icon to hide."""
        from tests.test_daemon import _make_daemon

        with patch("whisper_aloud.service.shell_extension_watch.ShellExtensionWatcher") as cls:
            d = _make_daemon(indicator_fails=True)
        assert d.indicator is None
        cls.assert_called_once()
        cls.call_args.args[0](True)  # no tray to hide: must not raise


class TestConfigChangeClosesPortal:
    def test_switching_away_from_remote_desktop_closes_the_session(self, daemon):
        from whisper_aloud.config import WhisperAloudConfig

        daemon.config.clipboard.paste_backend = "remote-desktop"
        new = WhisperAloudConfig()
        new.clipboard.paste_backend = "auto"
        with patch("whisper_aloud.clipboard.paste_selection.close_portal_simulator") as close:
            daemon._apply_config_changes(new)
        close.assert_called_once()

    def test_unrelated_change_keeps_the_session(self, daemon):
        from whisper_aloud.config import WhisperAloudConfig

        daemon.config.clipboard.paste_backend = "remote-desktop"
        new = WhisperAloudConfig()
        new.clipboard.paste_backend = "remote-desktop"
        with patch("whisper_aloud.clipboard.paste_selection.close_portal_simulator") as close:
            daemon._apply_config_changes(new)
        close.assert_not_called()
