"""Tests for HotkeyManager with 3-level backend fallback."""

from unittest.mock import MagicMock, patch


class TestHotkeyManager:
    """Tests for HotkeyManager that work without actual portal/keybinder libraries."""

    def test_detects_available_backend(self):
        """Should detect which backend is available."""
        from whisper_aloud.service.hotkey import HotkeyManager

        manager = HotkeyManager()
        backend = manager.detect_backend()
        assert backend in ("portal", "keybinder", "none")

    def test_register_returns_bool(self):
        """Register should return bool."""
        from whisper_aloud.service.hotkey import HotkeyManager

        manager = HotkeyManager()
        result = manager.register("<Super><Alt>r", callback=lambda: None)
        assert isinstance(result, bool)

    def test_unregister_does_not_raise(self):
        """Unregister should never raise."""
        from whisper_aloud.service.hotkey import HotkeyManager

        manager = HotkeyManager()
        manager.unregister()  # Should not raise

    def test_backend_property(self):
        """Backend property should return detected backend."""
        from whisper_aloud.service.hotkey import HotkeyManager

        manager = HotkeyManager()
        assert manager.backend in ("portal", "keybinder", "none")

    def test_available_property(self):
        """Available should be True when portal or keybinder present."""
        from whisper_aloud.service.hotkey import HotkeyManager

        manager = HotkeyManager()
        assert isinstance(manager.available, bool)
        if manager.backend == "none":
            assert manager.available is False
        else:
            assert manager.available is True

    def test_register_with_no_backend(self):
        """Register with no backend should return False."""
        from whisper_aloud.service.hotkey import HotkeyManager

        manager = HotkeyManager()
        manager._backend = "none"
        result = manager.register("<Super><Alt>r", callback=lambda: None)
        assert result is False

    def test_unregister_idempotent(self):
        """Calling unregister multiple times should be safe."""
        from whisper_aloud.service.hotkey import HotkeyManager

        manager = HotkeyManager()
        manager.unregister()
        manager.unregister()  # Second call should also not raise

    def test_detect_backend_never_raises(self):
        """detect_backend should never raise, regardless of environment."""
        from whisper_aloud.service.hotkey import HotkeyManager

        manager = HotkeyManager()
        # Should not raise even if imports fail
        backend = manager.detect_backend()
        assert isinstance(backend, str)
        assert backend in ("portal", "keybinder", "none")


class TestPortalBackendDetection:
    """Tests for XDG Desktop Portal backend detection using mocks."""

    def test_portal_backend_detected_when_available(self):
        """Portal should be detected when Xdp is importable."""
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_xdp = MagicMock()
        mock_portal = MagicMock()
        mock_xdp.Portal.return_value = mock_portal

        with patch.dict("sys.modules", {"gi.repository.Xdp": mock_xdp}):
            with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=mock_xdp):
                manager = HotkeyManager()
                manager._xdp = mock_xdp
                manager._backend = "portal"
                assert manager.backend == "portal"
                assert manager.available is True

    def test_portal_preferred_over_keybinder(self):
        """Portal should be chosen over keybinder when both available."""
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_xdp = MagicMock()

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=mock_xdp):
            with patch("whisper_aloud.service.hotkey._try_import_keybinder", return_value=MagicMock()):
                manager = HotkeyManager()
                assert manager.backend == "portal"

    def test_portal_register_dispatches_correctly(self):
        """Register with portal backend should use portal API."""
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_xdp = MagicMock()
        mock_portal = MagicMock()
        mock_xdp.Portal.return_value = mock_portal

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=mock_xdp):
            with patch("whisper_aloud.service.hotkey._try_import_keybinder", return_value=None):
                manager = HotkeyManager()
                assert manager.backend == "portal"
                def cb():
                    return None
                result = manager.register("<Super><Alt>r", callback=cb)
                assert isinstance(result, bool)


class TestGetBoundTrigger:
    """HotkeyManager.get_bound_trigger exposes what the portal actually
    bound (e.g. GNOME's chosen accelerator), not the configured preference."""

    def test_returns_none_when_backend_is_not_portal(self):
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_kb = MagicMock()
        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch("whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb):
                manager = HotkeyManager()
                assert manager.backend == "keybinder"
                assert manager.get_bound_trigger() is None

    def test_returns_none_when_not_bound_yet(self):
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_xdp = MagicMock()
        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=mock_xdp):
            manager = HotkeyManager()
            assert manager.backend == "portal"
            # _portal_hotkeys is only set by _register_portal(); never
            # called here, so there's nothing to ask yet.
            assert manager.get_bound_trigger() is None

    def test_returns_portal_trigger_description_once_bound(self):
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_xdp = MagicMock()
        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=mock_xdp):
            manager = HotkeyManager()
            mock_portal_instance = MagicMock()
            mock_portal_instance.get_trigger_description.return_value = "Press <Control>space"
            manager._portal_hotkeys = mock_portal_instance

            assert manager.get_bound_trigger("toggle") == "Press <Control>space"
            mock_portal_instance.get_trigger_description.assert_called_once_with("toggle")

    def test_never_raises_if_portal_object_misbehaves(self):
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_xdp = MagicMock()
        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=mock_xdp):
            manager = HotkeyManager()
            mock_portal_instance = MagicMock()
            mock_portal_instance.get_trigger_description.side_effect = Exception("boom")
            manager._portal_hotkeys = mock_portal_instance

            assert manager.get_bound_trigger() is None


class TestKeybinderBackendDetection:
    """Tests for libkeybinder3 backend detection using mocks."""

    def test_keybinder_backend_detected_when_available(self):
        """Keybinder should be detected when importable and portal is not."""
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_kb = MagicMock()

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch("whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb):
                manager = HotkeyManager()
                assert manager.backend == "keybinder"
                assert manager.available is True

    def test_keybinder_register_calls_bind(self):
        """Register with keybinder should call Keybinder.bind()."""
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_kb = MagicMock()

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch("whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb):
                manager = HotkeyManager()
                def cb():
                    return None
                result = manager.register("<Super><Alt>r", callback=cb)
                assert result is True
                mock_kb.bind.assert_called_once()

    def test_keybinder_unregister_calls_unbind(self):
        """Unregister with keybinder should call Keybinder.unbind()."""
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_kb = MagicMock()

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch("whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb):
                manager = HotkeyManager()
                manager.register("<Super><Alt>r", callback=lambda: None)
                manager.unregister()
                mock_kb.unbind.assert_called_once_with("<Super><Alt>r")

    def test_keybinder_init_called_once(self):
        """Keybinder.init() should be called during setup."""
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_kb = MagicMock()

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch("whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb):
                HotkeyManager()
                mock_kb.init.assert_called_once()


class TestFlatpakSandboxGating:
    """WHISP-59: inside a Flatpak sandbox, hotkeys must be portal-only --
    never libkeybinder3, which is not available/permitted there."""

    def test_keybinder_skipped_when_sandboxed_even_if_importable(self):
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_kb = MagicMock()

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch("whisper_aloud.service.hotkey._is_sandboxed", return_value=True):
                with patch(
                    "whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb
                ) as mock_try_kb:
                    manager = HotkeyManager()
                    assert manager.backend == "none"
                    mock_try_kb.assert_not_called()
                    mock_kb.init.assert_not_called()

    def test_portal_still_used_when_sandboxed_and_available(self):
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_xdp = MagicMock()

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=mock_xdp):
            with patch("whisper_aloud.service.hotkey._is_sandboxed", return_value=True):
                manager = HotkeyManager()
                assert manager.backend == "portal"

    def test_keybinder_used_when_not_sandboxed(self):
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_kb = MagicMock()

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch("whisper_aloud.service.hotkey._is_sandboxed", return_value=False):
                with patch(
                    "whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb
                ):
                    manager = HotkeyManager()
                    assert manager.backend == "keybinder"


class TestBackendReprobeOnRegistrationFailure:
    """WHISP-90: the backend chosen once at __init__ is no longer cached
    forever -- a register() failure re-probes and retries."""

    def test_reprobes_and_retries_when_portal_registration_fails(self, caplog):
        import logging

        from whisper_aloud.service.hotkey import HotkeyManager

        mock_xdp = MagicMock()
        mock_kb = MagicMock()

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=mock_xdp):
            manager = HotkeyManager()
        assert manager.backend == "portal"

        with patch.object(manager, "_register_portal", return_value=False) as mock_reg_portal:
            with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
                with patch(
                    "whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb
                ):
                    with caplog.at_level(logging.INFO):
                        result = manager.register("<Super><Alt>r", callback=lambda: None)

        assert result is True
        assert manager.backend == "keybinder"
        mock_reg_portal.assert_called_once()
        mock_kb.bind.assert_called_once()
        assert any(
            "backend changed" in r.message and "portal -> keybinder" in r.message
            for r in caplog.records
        )

    def test_reprobes_to_none_when_everything_fails(self):
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_kb = MagicMock()
        mock_kb.bind.side_effect = Exception("bind failed")

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch(
                "whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb
            ):
                manager = HotkeyManager()
        assert manager.backend == "keybinder"

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch(
                "whisper_aloud.service.hotkey._try_import_keybinder", return_value=None
            ):
                result = manager.register("<Super><Alt>r", callback=lambda: None)

        assert result is False
        assert manager.backend == "none"

    def test_no_reprobe_when_registration_succeeds(self):
        """A successful registration must not trigger any re-probe/retry."""
        from whisper_aloud.service.hotkey import HotkeyManager

        mock_kb = MagicMock()

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch(
                "whisper_aloud.service.hotkey._try_import_keybinder", return_value=mock_kb
            ) as mock_try_kb:
                manager = HotkeyManager()
                mock_try_kb.reset_mock()
                result = manager.register("<Super><Alt>r", callback=lambda: None)

        assert result is True
        mock_try_kb.assert_not_called()  # detect_backend() was never re-run


class TestNoneBackend:
    """Tests for the fallback 'none' backend."""

    def test_none_backend_when_nothing_available(self):
        """Should fall back to 'none' when no backends available."""
        from whisper_aloud.service.hotkey import HotkeyManager

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch("whisper_aloud.service.hotkey._try_import_keybinder", return_value=None):
                manager = HotkeyManager()
                assert manager.backend == "none"
                assert manager.available is False

    def test_none_backend_register_returns_false(self):
        """Register with no backend should return False."""
        from whisper_aloud.service.hotkey import HotkeyManager

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch("whisper_aloud.service.hotkey._try_import_keybinder", return_value=None):
                manager = HotkeyManager()
                result = manager.register("<Super><Alt>r", callback=lambda: None)
                assert result is False

    def test_none_backend_unregister_safe(self):
        """Unregister with no backend should be safe."""
        from whisper_aloud.service.hotkey import HotkeyManager

        with patch("whisper_aloud.service.hotkey._try_import_portal", return_value=None):
            with patch("whisper_aloud.service.hotkey._try_import_keybinder", return_value=None):
                manager = HotkeyManager()
                manager.unregister()  # Should not raise
