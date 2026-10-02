# Flatpak notes (WHISP-59 / for WHISP-61)

This document records what the Flatpak manifest (WHISP-61, written
separately) needs so that portal-based text insertion (WHISP-59) and the
existing portal-based hotkey (WHISP-64) work inside the sandbox. It is
notes only -- no manifest is written here.

## Required `finish-args`

- `--talk-name=org.freedesktop.portal.Desktop`

  Needed for every portal call this app makes from inside the sandbox:
  `RemoteDesktop` (CreateSession/SelectDevices/Start/NotifyKeyboardKeysym,
  for paste -- WHISP-59) and `GlobalShortcuts` (for the hotkey --
  WHISP-64). Both interfaces live on the same well-known bus name
  `org.freedesktop.portal.Desktop`, so a single `--talk-name` line covers
  both; no separate interface-level permission exists at the finish-args
  level (the portal itself gates access per-interface via its own
  consent dialogs at runtime).

- No `--socket=x11` or `--socket=fallback-x11` is needed for paste.

  Text insertion goes entirely through `NotifyKeyboardKeysym` over the
  `RemoteDesktop` portal D-Bus interface -- it does not touch X11 or
  Wayland input sockets directly, and the sandboxed app never runs
  ydotool/xdotool/wtype (see `src/whisper_aloud/sandbox.py` and
  `src/whisper_aloud/clipboard/paste_selection.py`, which refuse to
  select those backends whenever `is_flatpak_sandbox()` is true).

## Not required for paste, but likely needed elsewhere (out of scope here)

- `--socket=wayland` and/or `--socket=fallback-x11` would still be needed
  if/when the GTK4 GUI itself needs to *display* a window inside the
  sandbox -- that is a windowing concern, unrelated to paste.
- Clipboard *copy* (not paste) still uses `wl-copy`/`xclip` outside the
  sandbox; inside the sandbox this ticket does not change clipboard copy
  behavior -- only paste/text-insertion. A future ticket may want to
  route copy through the Clipboard portal's `RequestClipboard` (see
  below) for full sandbox-cleanliness, but it is not required for
  WHISP-59 since `wl-copy`/`xclip` already degrade to the existing
  fallback-file path on failure (`ClipboardManager._copy_fallback`), and
  a sandboxed WhisperAloud with `--talk-name=org.freedesktop.portal.Desktop`
  gets no special clipboard access merely from that line.

## Clipboard portal (`RequestClipboard`) -- noted, not used by this ticket

Newer `xdg-desktop-portal` versions expose clipboard read/write via the
same `RemoteDesktop` session (`RequestClipboard`, `SetSelection`,
`SelectionWrite`/`SelectionRead`, the `SelectionOwnerChanged` and
`SelectionTransfer` signals), gated by the same session object this
ticket already creates for keyboard emulation. WhisperAloud does not use
it yet -- clipboard *copy* continues to use `wl-copy`/`xclip` outside the
sandbox and the existing file-fallback inside it. If a future ticket
wants sandboxed clipboard copy too, it can reuse the same RemoteDesktop
session `PortalPasteSimulator` already creates (one session covers both
keyboard emulation and clipboard, per the portal's design) rather than
opening a second one.

## Portal API version assumptions

- `org.freedesktop.portal.RemoteDesktop` version 2+: this is the version
  that added `persist_mode` and `restore_token` to `SelectDevices`, and
  the `restore_token` result from `Start`. Without `persist_mode`
  support, `PortalPasteSimulator` still works but the user is re-prompted
  for consent on every daemon restart -- `SelectDevices` simply ignores
  unknown option keys on older portals rather than failing outright
  (per the generic xdg-desktop-portal options-are-best-effort contract),
  so this degrades gracefully rather than erroring.
- `org.freedesktop.host.portal.Registry.Register` (xdg-desktop-portal
  >= 1.19): used opportunistically, exactly like
  `service/hotkey_portal.py` does for `GlobalShortcuts` (WHISP-64), to
  associate the connection with a stable `app_id` so persisted consent
  survives process restarts. Missing/failing `Register` on older portals
  is caught and ignored -- it is not required for the paste flow to
  function, only for the *persistence* of consent to be scoped
  correctly.

## What can only be verified on a real Flatpak install

- That `--talk-name=org.freedesktop.portal.Desktop` alone (with no other
  socket/device permission) is actually sufficient for the real
  `RemoteDesktop` portal to grant a keyboard-only session end to end on a
  real compositor (GNOME/KDE) -- this ticket's tests exercise the
  Request/Response protocol against a fake portal service on a private
  bus, not the real `xdg-desktop-portal` binary or a real consent dialog.
- That the real portal's consent dialog, when granted with
  `persist_mode=2`, actually avoids re-prompting on a second `flatpak
  run` of the app (i.e. that the portal's own persistence storage
  survives across sandbox instances the way the spec describes).
- Real-world behavior of `NotifyKeyboardKeysym` against actual focused
  windows under GNOME Wayland, KDE Wayland, and X11-via-XWayland --
  specifically whether the keysym-based (rather than keycode-based)
  injection reliably reaches the right window, and the actual
  focus-follows semantics the portal uses to decide where synthesized
  keys land.
- Whether `wlroots`-based compositors (Sway, etc.) support
  `RemoteDesktop` keyboard emulation at all; the research notes in
  `docs/research/2026-10-01-sota-stt-offline/notes/apps_packaging.md`
  only confirm GNOME/KDE coverage for Speed of Sound's equivalent
  portal-based approach.
