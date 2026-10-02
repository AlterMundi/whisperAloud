"""Unit tests for the RemoteDesktop NotifyKeyboardKeysym sequence (WHISP-59).

Pure logic -- no Gio/D-Bus involved -- so these run on every ``pytest -q``.
"""

from whisper_aloud.clipboard.portal_keysyms import (
    XK_CONTROL_L,
    XK_SHIFT_L,
    XK_v,
    KEY_STATE_PRESSED,
    KEY_STATE_RELEASED,
    keysym_sequence_for_shortcut,
)


class TestCtrlV:
    def test_sequence_is_press_ctrl_press_v_release_v_release_ctrl(self):
        seq = keysym_sequence_for_shortcut("ctrl+v")
        assert seq == [
            (XK_CONTROL_L, KEY_STATE_PRESSED),
            (XK_v, KEY_STATE_PRESSED),
            (XK_v, KEY_STATE_RELEASED),
            (XK_CONTROL_L, KEY_STATE_RELEASED),
        ]

    def test_default_shortcut_is_ctrl_v(self):
        assert keysym_sequence_for_shortcut(None) == keysym_sequence_for_shortcut("ctrl+v")
        assert keysym_sequence_for_shortcut("") == keysym_sequence_for_shortcut("ctrl+v")


class TestCtrlShiftV:
    def test_sequence_is_press_ctrl_shift_v_release_v_shift_ctrl(self):
        seq = keysym_sequence_for_shortcut("ctrl+shift+v")
        assert seq == [
            (XK_CONTROL_L, KEY_STATE_PRESSED),
            (XK_SHIFT_L, KEY_STATE_PRESSED),
            (XK_v, KEY_STATE_PRESSED),
            (XK_v, KEY_STATE_RELEASED),
            (XK_SHIFT_L, KEY_STATE_RELEASED),
            (XK_CONTROL_L, KEY_STATE_RELEASED),
        ]

    def test_case_insensitive(self):
        assert keysym_sequence_for_shortcut("Ctrl+Shift+V") == keysym_sequence_for_shortcut(
            "ctrl+shift+v"
        )


class TestOrderingInvariants:
    def test_releases_are_reverse_of_presses(self):
        for shortcut in ("ctrl+v", "ctrl+shift+v"):
            seq = keysym_sequence_for_shortcut(shortcut)
            n = len(seq) // 2
            presses = seq[:n]
            releases = seq[n:]
            assert [k for k, s in presses] == list(reversed([k for k, s in releases]))
            assert all(s == KEY_STATE_PRESSED for _, s in presses)
            assert all(s == KEY_STATE_RELEASED for _, s in releases)

    def test_unknown_shortcut_falls_back_to_ctrl_v(self):
        assert keysym_sequence_for_shortcut("alt+tab") == keysym_sequence_for_shortcut("ctrl+v")


class TestKeysymValues:
    def test_known_x11_keysym_values(self):
        # Regression guard: these are standardized X11 keysym values
        # (keysymdef.h) that NotifyKeyboardKeysym expects verbatim.
        assert XK_CONTROL_L == 0xFFE3
        assert XK_SHIFT_L == 0xFFE1
        assert XK_v == 0x0076
