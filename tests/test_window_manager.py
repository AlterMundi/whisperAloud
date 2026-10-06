"""Tests for WindowManager."""
import logging
from unittest.mock import patch, MagicMock
from whisper_aloud.clipboard.window_manager import WindowManager, WindowInfo, is_own_window


class TestWindowInfo:
    def test_dataclass_fields(self):
        info = WindowInfo(window_id=12345, title="Terminal", backend="xdotool")
        assert info.window_id == 12345
        assert info.title == "Terminal"
        assert info.backend == "xdotool"
        assert info.wm_class == ""  # default when the backend can't report one

    def test_wm_class_field(self):
        info = WindowInfo(
            window_id=1, title="x", backend="gnome-extension",
            wm_class="org.gnome.Terminal",
        )
        assert info.wm_class == "org.gnome.Terminal"


class TestIsOwnWindow:
    """Field bug fix: a WhisperAloud window must never be captured/pasted
    into as if it were an external app -- see window_manager.py's
    docstring on `_OWN_WM_CLASS_PREFIX` for the full story."""

    def test_none_is_not_own_window(self):
        assert is_own_window(None) is False

    def test_native_gtk_wm_class_is_own_window(self):
        info = WindowInfo(
            window_id=1, title="WhisperAloud", backend="gnome-extension",
            wm_class="org.fede.whisperaloud.Gui",
        )
        assert is_own_window(info) is True

    def test_flatpak_wm_class_is_own_window(self):
        info = WindowInfo(
            window_id=1, title="WhisperAloud", backend="gnome-extension",
            wm_class="org.fede.whisperaloud",
        )
        assert is_own_window(info) is True

    def test_wm_class_match_is_case_insensitive(self):
        info = WindowInfo(
            window_id=1, title="WhisperAloud", backend="gnome-extension",
            wm_class="ORG.FEDE.WHISPERALOUD.GUI",
        )
        assert is_own_window(info) is True

    def test_unrelated_wm_class_is_not_own_window(self):
        info = WindowInfo(
            window_id=2, title="Terminal", backend="gnome-extension",
            wm_class="org.gnome.Terminal",
        )
        assert is_own_window(info) is False

    def test_falls_back_to_title_when_wm_class_unavailable(self):
        """xdotool never reports a class at all."""
        info = WindowInfo(window_id=3, title="WhisperAloud", backend="xdotool")
        assert is_own_window(info) is True

    def test_title_fallback_is_case_insensitive_and_prefix_match(self):
        info = WindowInfo(
            window_id=3, title="WhisperAloud - Daemon Not Running", backend="xdotool",
        )
        assert is_own_window(info) is True

    def test_unrelated_title_without_wm_class_is_not_own_window(self):
        info = WindowInfo(window_id=4, title="My Terminal", backend="xdotool")
        assert is_own_window(info) is False

    def test_non_string_wm_class_and_title_degrade_to_false(self):
        """A loosely-typed test double (e.g. a plain MagicMock standing in
        for WindowInfo) must never be misidentified as our own window just
        because attribute access on it never raises."""
        fake = MagicMock()
        fake.wm_class = MagicMock()
        fake.title = MagicMock()
        assert is_own_window(fake) is False


class _FakeVariantDict:
    """Stands in for the GLib.Variant `a{sv}` GetFocusedWindow returns --
    just enough of `lookup_value()` for `_get_active_gnome()`."""

    def __init__(self, mapping):
        self._mapping = mapping

    def lookup_value(self, key, _type):
        return self._mapping.get(key)


class TestGnomeBackendReportsWmClass:
    def test_get_active_gnome_includes_wm_class(self):
        with patch.object(WindowManager, "_check_gnome_extension", return_value=True):
            wm = WindowManager()
        proxy = MagicMock()

        def _call_sync(_method, *_args, **_kwargs):
            res = MagicMock()
            res.get_child_value.return_value = _FakeVariantDict({
                "id": MagicMock(unpack=lambda: 777),
                "title": MagicMock(unpack=lambda: "Terminal"),
                "wm_class": MagicMock(unpack=lambda: "org.gnome.Terminal"),
            })
            return res

        proxy.call_sync.side_effect = _call_sync
        wm._gnome_proxy = proxy

        info = wm.get_active_window()
        assert info is not None
        assert info.window_id == 777
        assert info.title == "Terminal"
        assert info.wm_class == "org.gnome.Terminal"

    def test_get_active_gnome_defaults_wm_class_when_missing(self):
        """An older extension predating the wm_class field: the "id" key
        still answers, just without "wm_class" -- must default to ""
        rather than raise."""
        with patch.object(WindowManager, "_check_gnome_extension", return_value=True):
            wm = WindowManager()
        proxy = MagicMock()

        def _call_sync(_method, *_args, **_kwargs):
            res = MagicMock()
            res.get_child_value.return_value = _FakeVariantDict({
                "id": MagicMock(unpack=lambda: 777),
                "title": MagicMock(unpack=lambda: "Terminal"),
            })
            return res

        proxy.call_sync.side_effect = _call_sync
        wm._gnome_proxy = proxy

        info = wm.get_active_window()
        assert info is not None
        assert info.wm_class == ""


class TestWindowManagerBackendDetection:
    def test_fallback_to_none_when_nothing_available(self):
        with patch("whisper_aloud.clipboard.window_manager.subprocess.run",
                   side_effect=FileNotFoundError):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()
                assert wm.backend == "none"

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_xdotool_probes_getactivewindow(self, mock_run):
        """xdotool detection runs actual getactivewindow, not just 'which'."""
        mock_run.return_value = MagicMock(returncode=0, stdout=b"12345\n")
        with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
            wm = WindowManager()
            assert wm.backend == "xdotool"
            mock_run.assert_called_once_with(
                ["xdotool", "getactivewindow"],
                capture_output=True, timeout=2,
            )


class TestXdotoolBackend:
    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_get_active_window(self, mock_run):
        mock_run.side_effect = [
            MagicMock(returncode=0, stdout=b"99\n"),  # _check_xdotool during __init__
            MagicMock(returncode=0, stdout=b"12345\n"),  # getactivewindow
            MagicMock(returncode=0, stdout=b"My Terminal\n"),  # getwindowname
        ]
        with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
            wm = WindowManager()
        info = wm.get_active_window()
        assert info is not None
        assert info.window_id == 12345
        assert info.title == "My Terminal"
        assert info.backend == "xdotool"

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_activate_window(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        wm = WindowManager()
        wm.backend = "xdotool"
        info = WindowInfo(window_id=12345, title="Terminal", backend="xdotool")
        assert wm.activate_window(info) is True

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_get_active_window_fails_gracefully(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        wm = WindowManager()
        wm.backend = "xdotool"
        assert wm.get_active_window() is None

    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_activate_window_fails_gracefully(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stderr=b"error")
        wm = WindowManager()
        wm.backend = "xdotool"
        info = WindowInfo(window_id=99999, title="Gone", backend="xdotool")
        assert wm.activate_window(info) is False


class TestShellExtensionPresenceHook:
    """WHISP-90: the daemon's single ShellExtensionWatcher forwards
    NameOwnerChanged events here instead of WindowManager opening its own,
    duplicate Gio.bus_watch_name() for the same bus name."""

    def test_presence_change_reprobes_and_can_upgrade_to_gnome(self, caplog):
        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=b"99\n"),
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()
        assert wm.backend == "xdotool"

        with patch.object(WindowManager, "_check_gnome_extension", return_value=True):
            with caplog.at_level(logging.INFO):
                wm.on_shell_extension_presence_changed(True)

        assert wm.backend == "gnome-extension"
        assert any(
            "backend changed" in r.message and "xdotool -> gnome-extension" in r.message
            for r in caplog.records
        )

    def test_presence_change_falls_back_when_extension_vanishes(self):
        with patch.object(WindowManager, "_check_gnome_extension", return_value=True):
            wm = WindowManager()
        assert wm.backend == "gnome-extension"

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=b"99\n"),
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm.on_shell_extension_presence_changed(False)

        assert wm.backend == "xdotool"

    def test_presence_change_without_effect_logs_no_transition(self, caplog):
        with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
            with patch(
                "whisper_aloud.clipboard.window_manager.subprocess.run",
                side_effect=FileNotFoundError,
            ):
                wm = WindowManager()
                assert wm.backend == "none"

                with caplog.at_level(logging.INFO):
                    wm.on_shell_extension_presence_changed(False)

        assert wm.backend == "none"
        assert not any("backend changed" in r.message for r in caplog.records)


class TestBackendReprobeOnFailure:
    """WHISP-90: the backend detected at __init__ is no longer cached
    forever -- a real operation failure re-probes it."""

    def test_reprobes_and_falls_back_when_gnome_operation_fails(self, caplog):
        with patch.object(WindowManager, "_check_gnome_extension", return_value=True):
            wm = WindowManager()
        assert wm.backend == "gnome-extension"

        wm._gnome_proxy = MagicMock()
        wm._gnome_proxy.call_sync.side_effect = RuntimeError("extension gone")

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=b"12345\n"),
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                with caplog.at_level(logging.INFO):
                    result = wm.get_active_window()

        assert result is None
        assert wm.backend == "xdotool"
        assert any(
            "backend changed" in r.message and "gnome-extension -> xdotool" in r.message
            for r in caplog.records
        )

    def test_reprobes_to_none_when_xdotool_binary_disappears(self, caplog):
        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=b"99\n"),
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()
        assert wm.backend == "xdotool"

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                with caplog.at_level(logging.INFO):
                    result = wm.get_active_window()

        assert result is None
        assert wm.backend == "none"
        assert any("backend changed" in r.message for r in caplog.records)

    def test_activate_window_reprobes_on_gnome_failure(self, caplog):
        with patch.object(WindowManager, "_check_gnome_extension", return_value=True):
            wm = WindowManager()
        wm._gnome_proxy = MagicMock()
        wm._gnome_proxy.call_sync.side_effect = RuntimeError("extension gone")
        info = WindowInfo(window_id=1, title="x", backend="gnome-extension")

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            side_effect=FileNotFoundError,
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                with caplog.at_level(logging.INFO):
                    result = wm.activate_window(info)

        assert result is False
        assert wm.backend == "none"

    def test_no_reprobe_log_when_backend_unchanged(self, caplog):
        """A failure that re-probes to the SAME backend logs no transition
        (only a debug line) -- avoid noisy INFO spam when nothing changed."""
        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=b"99\n"),
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()
        assert wm.backend == "xdotool"

        call_count = {"n": 0}

        def _flaky_run(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise FileNotFoundError
            return MagicMock(returncode=0, stdout=b"42\n")

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run", side_effect=_flaky_run
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                with caplog.at_level(logging.INFO):
                    wm.get_active_window()

        assert wm.backend == "xdotool"
        assert not any("backend changed" in r.message for r in caplog.records)

    def test_no_reprobe_on_plain_no_active_window(self, caplog):
        """A non-zero exit (no active window right now) is not a backend
        failure -- it must not trigger a re-probe."""
        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=0, stdout=b"99\n"),
        ):
            with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
                wm = WindowManager()

        with patch(
            "whisper_aloud.clipboard.window_manager.subprocess.run",
            return_value=MagicMock(returncode=1),
        ) as mock_run:
            with caplog.at_level(logging.INFO):
                result = wm.get_active_window()

        assert result is None
        assert wm.backend == "xdotool"
        mock_run.assert_called_once()  # no extra _detect_backend() probing


class TestConfirmFocusXdotool:
    """WHISP-88: the xdotool backend has no focus-change signal, so
    confirm_focus falls back to a small, BOUNDED number of getactivewindow
    re-queries -- never a fixed wait used as the success criterion."""

    def _make_wm(self, mock_run):
        with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
            wm = WindowManager()
        assert wm.backend == "xdotool"
        return wm

    @patch("whisper_aloud.clipboard.window_manager.GLib")
    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_confirms_true_on_first_matching_requery(self, mock_run, mock_glib):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"99\n")
        wm = self._make_wm(mock_run)
        mock_run.return_value = MagicMock(returncode=0, stdout=b"12345\n")
        info = WindowInfo(window_id=12345, title="x", backend="xdotool")

        # Drive GLib.timeout_add's scheduled callback ourselves, standing
        # in for the real GLib main loop.
        scheduled = {}
        mock_glib.timeout_add.side_effect = lambda ms, fn: scheduled.setdefault("fn", fn)

        result = {}
        wm.confirm_focus(info, lambda ok: result.update(ok=ok))

        assert "fn" in scheduled
        keep_polling = scheduled["fn"]()
        assert keep_polling is False  # matched -- stop polling
        assert result == {"ok": True}

    @patch("whisper_aloud.clipboard.window_manager.GLib")
    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_degrades_after_bounded_retries_with_warning(self, mock_run, mock_glib, caplog):
        import logging

        mock_run.return_value = MagicMock(returncode=0, stdout=b"99\n")
        wm = self._make_wm(mock_run)
        # Every re-query reports a DIFFERENT window focused -- never matches.
        mock_run.return_value = MagicMock(returncode=0, stdout=b"1\n")
        info = WindowInfo(window_id=12345, title="x", backend="xdotool")

        scheduled = {}
        mock_glib.timeout_add.side_effect = lambda ms, fn: scheduled.setdefault("fn", fn)

        result = {}
        with caplog.at_level(logging.WARNING):
            wm.confirm_focus(info, lambda ok: result.update(ok=ok))
            fn = scheduled["fn"]
            # Bounded: keeps polling for a small number of attempts, then
            # gives up -- never an unbounded retry loop.
            outcomes = [fn() for _ in range(5)]

        assert result == {"ok": False}
        # Stops polling (returns False) once the bound is reached, well
        # before the 5 manual calls above are exhausted.
        assert False in outcomes
        assert any("WHISP-88" in r.message for r in caplog.records)

    @patch("whisper_aloud.clipboard.window_manager.GLib")
    @patch("whisper_aloud.clipboard.window_manager.subprocess.run")
    def test_degrades_immediately_on_backend_failure_and_reprobes(self, mock_run, mock_glib):
        mock_run.return_value = MagicMock(returncode=0, stdout=b"99\n")
        wm = self._make_wm(mock_run)
        info = WindowInfo(window_id=12345, title="x", backend="xdotool")

        scheduled = {}
        mock_glib.timeout_add.side_effect = lambda ms, fn: scheduled.setdefault("fn", fn)

        result = {}
        wm.confirm_focus(info, lambda ok: result.update(ok=ok))

        mock_run.side_effect = FileNotFoundError
        with patch.object(WindowManager, "_check_gnome_extension", return_value=False):
            keep_polling = scheduled["fn"]()

        assert keep_polling is False
        assert result == {"ok": False}
        assert wm.backend == "none"


class TestNoneBackend:
    def test_get_active_window_returns_none(self):
        with patch.object(WindowManager, "_detect_backend", return_value="none"):
            wm = WindowManager()
        assert wm.get_active_window() is None

    def test_activate_window_returns_false(self):
        with patch.object(WindowManager, "_detect_backend", return_value="none"):
            wm = WindowManager()
        info = WindowInfo(window_id=1, title="x", backend="none")
        assert wm.activate_window(info) is False
