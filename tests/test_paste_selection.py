"""Tests for the sandbox-aware paste backend selection (WHISP-59).

Pure logic: `build_paste_simulator` must choose PortalPasteSimulator
whenever running inside a Flatpak sandbox, and never construct a
ydotool/xdotool-based PasteSimulator in that case -- and vice versa
outside the sandbox.
"""

from unittest.mock import MagicMock

from whisper_aloud.clipboard.paste_selection import (
    build_paste_simulator,
    reset_portal_simulator_for_tests,
)
from whisper_aloud.clipboard.paste_simulator import PasteSimulator
from whisper_aloud.clipboard.portal_paste_simulator import PortalPasteSimulator
from whisper_aloud.config import ClipboardConfig


def setup_function(_fn):
    reset_portal_simulator_for_tests()


def teardown_function(_fn):
    reset_portal_simulator_for_tests()


class TestOutsideSandbox:
    def test_returns_regular_paste_simulator(self):
        sim = build_paste_simulator("wayland", ClipboardConfig(), is_sandboxed=False)
        assert isinstance(sim, PasteSimulator)

    def test_never_touches_portal_module_state(self):
        build_paste_simulator("x11", ClipboardConfig(), is_sandboxed=False)
        # No portal singleton should have been created.
        from whisper_aloud.clipboard import paste_selection

        assert paste_selection._portal_simulator is None


class TestInsideSandbox:
    def test_returns_portal_backed_adapter_not_regular_simulator(self):
        sim = build_paste_simulator("wayland", ClipboardConfig(), is_sandboxed=True)
        assert not isinstance(sim, PasteSimulator)

    def test_adapter_delegates_to_portal_simulate_paste(self):
        config = ClipboardConfig(paste_shortcut="ctrl+shift+v")
        sim = build_paste_simulator("wayland", config, is_sandboxed=True)
        sim._portal.simulate_paste = MagicMock(return_value=True)  # type: ignore[attr-defined]

        assert sim.simulate_paste() is True
        sim._portal.simulate_paste.assert_called_once_with("ctrl+shift+v")

    def test_portal_session_reused_across_calls_same_daemon_lifetime(self):
        config = ClipboardConfig()
        sim1 = build_paste_simulator("wayland", config, is_sandboxed=True)
        sim2 = build_paste_simulator("wayland", config, is_sandboxed=True)
        assert sim1._portal is sim2._portal  # type: ignore[attr-defined]

    def test_portal_simulator_instance_is_a_real_portal_paste_simulator(self):
        sim = build_paste_simulator("wayland", ClipboardConfig(), is_sandboxed=True)
        assert isinstance(sim._portal, PortalPasteSimulator)  # type: ignore[attr-defined]


class TestAutoDetection:
    def test_defaults_to_real_sandbox_detection_when_not_overridden(self, monkeypatch):
        """With no override, the real is_flatpak_sandbox() (FLATPAK_ID unset in
        the test environment) must select the non-portal backend."""
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        sim = build_paste_simulator("wayland", ClipboardConfig())
        assert isinstance(sim, PasteSimulator)
