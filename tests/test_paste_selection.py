"""Tests for paste backend selection (WHISP-59, WHISP-119).

Pure logic:
- `select_paste_backend` picks how auto-paste reaches the focused app:
  the GNOME Shell extension's InsertText when it reports the capability,
  otherwise ydotool/xdotool outside the sandbox (unchanged native
  behavior) or clipboard only inside it. The RemoteDesktop portal is only
  ever used when explicitly configured (`paste_backend="remote-desktop"`).
- `build_paste_simulator` builds the keystroke or portal simulator for the
  selected backend, and never touches the portal singleton otherwise.
- `prewarm_portal_paste_backend` does nothing unless the portal is opted in,
  so the default never opens a RemoteDesktop session (no orange indicator).
"""

from unittest.mock import MagicMock, patch

import pytest

from whisper_aloud.clipboard import paste_selection
from whisper_aloud.clipboard.paste_selection import (
    CLIPBOARD,
    EXTENSION,
    KEYSTROKE,
    REMOTE_DESKTOP,
    build_paste_simulator,
    prewarm_portal_paste_backend,
    reset_portal_simulator_for_tests,
    select_paste_backend,
)
from whisper_aloud.clipboard.paste_simulator import PasteSimulator
from whisper_aloud.clipboard.portal_paste_simulator import PortalPasteSimulator
from whisper_aloud.config import PASTE_BACKENDS, ClipboardConfig


@pytest.fixture(autouse=True)
def _fresh_portal_singleton():
    reset_portal_simulator_for_tests()
    yield
    reset_portal_simulator_for_tests()


# ── select_paste_backend ───────────────────────────────────────────────────


class TestSelectAuto:
    @pytest.mark.parametrize("sandboxed", [True, False])
    def test_extension_first_when_it_can_insert(self, sandboxed):
        assert select_paste_backend(
            "auto", is_sandboxed=sandboxed, extension_can_insert=True) == EXTENSION

    def test_native_without_extension_keeps_keystroke_tools(self):
        assert select_paste_backend(
            "auto", is_sandboxed=False, extension_can_insert=False) == KEYSTROKE

    def test_flatpak_without_extension_is_clipboard_only(self):
        assert select_paste_backend(
            "auto", is_sandboxed=True, extension_can_insert=False) == CLIPBOARD

    @pytest.mark.parametrize("sandboxed", [True, False])
    @pytest.mark.parametrize("ext", [True, False])
    def test_auto_never_selects_remote_desktop(self, sandboxed, ext):
        assert select_paste_backend(
            "auto", is_sandboxed=sandboxed, extension_can_insert=ext) != REMOTE_DESKTOP


class TestSelectExplicit:
    @pytest.mark.parametrize("sandboxed", [True, False])
    def test_extension_when_capable(self, sandboxed):
        assert select_paste_backend(
            "extension", is_sandboxed=sandboxed, extension_can_insert=True) == EXTENSION

    @pytest.mark.parametrize("sandboxed", [True, False])
    def test_extension_missing_falls_back_to_clipboard(self, sandboxed):
        assert select_paste_backend(
            "extension", is_sandboxed=sandboxed, extension_can_insert=False) == CLIPBOARD

    @pytest.mark.parametrize("sandboxed", [True, False])
    @pytest.mark.parametrize("ext", [True, False])
    def test_remote_desktop_opt_in(self, sandboxed, ext):
        assert select_paste_backend(
            "remote-desktop", is_sandboxed=sandboxed, extension_can_insert=ext) == REMOTE_DESKTOP

    @pytest.mark.parametrize("sandboxed", [True, False])
    @pytest.mark.parametrize("ext", [True, False])
    def test_clipboard(self, sandboxed, ext):
        assert select_paste_backend(
            "clipboard", is_sandboxed=sandboxed, extension_can_insert=ext) == CLIPBOARD

    def test_unknown_value_behaves_like_auto(self):
        assert select_paste_backend(
            "bogus", is_sandboxed=True, extension_can_insert=False) == CLIPBOARD
        assert select_paste_backend(
            "bogus", is_sandboxed=False, extension_can_insert=False) == KEYSTROKE

    def test_every_config_value_is_handled(self):
        for value in PASTE_BACKENDS:
            assert select_paste_backend(
                value, is_sandboxed=True, extension_can_insert=True) in (
                    EXTENSION, KEYSTROKE, REMOTE_DESKTOP, CLIPBOARD)


# ── build_paste_simulator ──────────────────────────────────────────────────


class TestBuildKeystroke:
    def test_returns_regular_paste_simulator(self):
        sim = build_paste_simulator("wayland", ClipboardConfig(), backend=KEYSTROKE)
        assert isinstance(sim, PasteSimulator)

    def test_never_touches_portal_module_state(self):
        build_paste_simulator("x11", ClipboardConfig(), backend=KEYSTROKE)
        assert paste_selection._portal_simulator is None


class TestBuildRemoteDesktop:
    def test_returns_portal_backed_adapter_not_regular_simulator(self):
        sim = build_paste_simulator("wayland", ClipboardConfig(), backend=REMOTE_DESKTOP)
        assert not isinstance(sim, PasteSimulator)
        assert isinstance(sim._portal, PortalPasteSimulator)  # type: ignore[attr-defined]

    def test_adapter_delegates_to_portal_simulate_paste(self):
        config = ClipboardConfig(paste_shortcut="ctrl+shift+v")
        sim = build_paste_simulator("wayland", config, backend=REMOTE_DESKTOP)
        sim._portal.simulate_paste = MagicMock(return_value=True)  # type: ignore[attr-defined]

        assert sim.simulate_paste() is True
        sim._portal.simulate_paste.assert_called_once_with("ctrl+shift+v")

    def test_portal_session_reused_across_calls_same_daemon_lifetime(self):
        config = ClipboardConfig()
        sim1 = build_paste_simulator("wayland", config, backend=REMOTE_DESKTOP)
        sim2 = build_paste_simulator("wayland", config, backend=REMOTE_DESKTOP)
        assert sim1._portal is sim2._portal  # type: ignore[attr-defined]


class TestBuildWithoutSimulator:
    @pytest.mark.parametrize("backend", [EXTENSION, CLIPBOARD])
    def test_no_simulator_and_no_portal(self, backend):
        assert build_paste_simulator("wayland", ClipboardConfig(), backend=backend) is None
        assert paste_selection._portal_simulator is None


class TestBuildFromConfig:
    """No explicit backend: derived from config.paste_backend, as if the
    extension were unavailable."""

    def test_default_config_in_sandbox_never_builds_the_portal(self):
        sim = build_paste_simulator("wayland", ClipboardConfig(), is_sandboxed=True)
        assert sim is None
        assert paste_selection._portal_simulator is None

    def test_default_config_native_is_keystroke(self):
        sim = build_paste_simulator("wayland", ClipboardConfig(), is_sandboxed=False)
        assert isinstance(sim, PasteSimulator)

    def test_remote_desktop_config_builds_the_portal(self):
        config = ClipboardConfig(paste_backend="remote-desktop")
        sim = build_paste_simulator("wayland", config, is_sandboxed=True)
        assert isinstance(sim._portal, PortalPasteSimulator)  # type: ignore[attr-defined]

    def test_defaults_to_real_sandbox_detection(self, monkeypatch):
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        sim = build_paste_simulator("wayland", ClipboardConfig())
        assert isinstance(sim, PasteSimulator)


# ── prewarm ────────────────────────────────────────────────────────────────


class TestPrewarm:
    @pytest.mark.parametrize("value", ["auto", "extension", "clipboard"])
    def test_no_prewarm_unless_opted_in(self, value):
        with patch.object(paste_selection, "PortalPasteSimulator") as cls:
            prewarm_portal_paste_backend(ClipboardConfig(paste_backend=value))
        cls.assert_not_called()
        assert paste_selection._portal_simulator is None

    def test_prewarm_when_remote_desktop_opted_in(self):
        with patch.object(paste_selection, "PortalPasteSimulator") as cls:
            prewarm_portal_paste_backend(ClipboardConfig(paste_backend="remote-desktop"))
        cls.return_value.prewarm.assert_called_once_with()
