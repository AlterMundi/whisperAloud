"""X11 keysyms and press/release sequencing for RemoteDesktop portal paste.

Pure logic, no Gio/D-Bus dependency: NotifyKeyboardKeysym takes a raw X11
keysym (from keysymdef.h) and a press(1)/release(0) state, and the caller
is responsible for sending a well-formed press/release sequence -- the
portal does not do this for you. Keeping this as data + a pure function
makes the exact wire sequence unit-testable without mocking D-Bus.
"""

from typing import List, Optional, Tuple

# Values from X11's keysymdef.h -- standardized, not WhisperAloud-specific.
XK_CONTROL_L = 0xFFE3
XK_SHIFT_L = 0xFFE1
XK_v = 0x0076  # noqa: N816 -- conventional X11 keysym naming (lowercase 'v')

KEY_STATE_RELEASED = 0
KEY_STATE_PRESSED = 1

_SHORTCUT_KEYS = {
    "ctrl+v": [XK_CONTROL_L, XK_v],
    "ctrl+shift+v": [XK_CONTROL_L, XK_SHIFT_L, XK_v],
}
_DEFAULT_SHORTCUT = "ctrl+v"


def keysym_sequence_for_shortcut(shortcut: Optional[str]) -> List[Tuple[int, int]]:
    """Return the ordered (keysym, state) pairs to send for `shortcut`.

    Modifiers are pressed outer-to-inner then released inner-to-outer (the
    usual well-behaved chord convention), e.g. for "ctrl+shift+v":
    press Ctrl, press Shift, press V, release V, release Shift, release Ctrl.

    Unknown/empty/None shortcuts fall back to "ctrl+v" -- this must never
    raise, since it runs on the daemon's transcription-completion path.
    """
    key = (shortcut or _DEFAULT_SHORTCUT).strip().lower()
    keys = _SHORTCUT_KEYS.get(key, _SHORTCUT_KEYS[_DEFAULT_SHORTCUT])

    presses = [(k, KEY_STATE_PRESSED) for k in keys]
    releases = [(k, KEY_STATE_RELEASED) for k in reversed(keys)]
    return presses + releases
