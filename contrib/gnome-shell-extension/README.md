# WhisperAloud GNOME Shell extension (`whisperaloud@fede`)

An **optional** top-bar indicator for WhisperAloud. After setup, it tells you
at a glance, right after login, whether dictation is ready, and it lets you
dictate with the hotkey without opening the main window. The app works
without it.

## What it shows

The icon and the first menu line follow the daemon's state:

| Indicator | Meaning |
|---|---|
| Microphone with a slash, "WhisperAloud is not running" | The daemon's bus name `org.fede.whisperaloud` has no owner. |
| Spinner, "Starting…" | The daemon is starting: we asked D-Bus to activate it, or its `GetStatus()` has not answered yet. |
| Spinner, "Loading model…" / "Downloading model…" | `model_state` is `loading`, `downloading` or `unloaded`. |
| Microphone, "Ready" | **All OK**: idle, `model_state == "ready"` and `hotkey_bound` (D-Bus API 1.7.0), or the extension's fallback keybinding is active. |
| Amber microphone, "Ready, but no global hotkey" | The model is ready, but the daemon reports `hotkey_bound == false`, for example while GNOME's shortcut dialog is still open after login, or after you declined it. |
| Red record dot, "Recording…" | The daemon is recording. |
| Gears, "Transcribing…" | The daemon is transcribing. |
| Warning, "Model not downloaded" | `model_state` is `missing`. Open WhisperAloud to download it. |
| Error, "Error: …" | `model_state` is `error` (with its message), or the daemon's state is `error`. |

The menu also shows the configured model (`Model: …`), the global hotkey
(`Hotkey: <trigger>`, or `Hotkey: not registered`) and, when the daemon
reports `autostart` (1.7.0), whether it starts at login. Its actions are
**Toggle Recording** (while the daemon runs), **Start WhisperAloud** (while
it does not; this D-Bus-activates the daemon without recording) and
**Open WhisperAloud** (launches `org.fede.whisperaloud.desktop`, native or
Flatpak).

Everything is event-driven, with no polling timers:

- `Gio.bus_watch_name` on `org.fede.whisperaloud` tracks whether the daemon
  runs.
- `StatusChanged(s)`, `ModelStateChanged(s, s)`, `HotkeyStateChanged(b, s, s)`
  (API 1.9.0) and `ConfigChanged` signals carry state changes.
- One `GetStatus()` call runs when the daemon appears, when it returns to
  `idle` and when its config changes. Every key is read defensively, so
  daemons older than API 1.6.0/1.7.0/1.9.0 still work.
- `HotkeyStateChanged` fires on every hotkey binding-state transition
  (portal bind result, GNOME's response=2-with-shortcuts quirk,
  `ShortcutsChanged` recovery, keybinder register/unregister, a backend
  re-probe, a hotkey config change, and daemon shutdown), so the extension
  never needs to re-read `GetStatus()` on a timer to learn `hotkey_bound`
  changing. Daemons older than API 1.9.0 never emit it; the "Ready, but no
  global hotkey" state then only clears on the next `StatusChanged`/
  `ConfigChanged`-triggered `GetStatus()`.

The pure state logic is in `status.js`. It has no GNOME Shell imports and is
unit-tested with Node (`tests/test_shell_extension_js.py`).

## Hotkey

The daemon owns the global hotkey, through the XDG GlobalShortcuts portal or
keybinder. The extension's `toggle-recording` keybinding (default
`<Super><Alt>r`, gsettings schema `org.gnome.shell.extensions.whisperaloud`)
is only a **fallback**. The extension grabs it only while the running daemon
has no hotkey backend at all (`hotkey_backend == "none"`). It does *not*
grab it when the portal binding is merely pending or was declined
(`hotkey_bound == false` with backend `portal`), because the portal may
still bind the same accelerator and both would toggle, cancelling each
other out. While the fallback is active, the menu says
`Hotkey: <accel> (via top-bar extension)` and the state counts as ready.

## What it exports

The extension owns the bus name **`org.gnome.Shell.Extensions.whisperaloud`**
and exports three interfaces on it:

| Object | Interface | Used by |
|---|---|---|
| `/org/fede/whisperaloud/Window` | `org.fede.whisperaloud.Window`: `GetFocusedWindow() → a{sv}`, `ActivateWindow(u) → b`, `FocusChanged(s window_id)` signal | The daemon's `WindowManager` "gnome-extension" backend (`paste_to_captured_window`) |
| `/org/fede/whisperaloud/ShellExtension` | `org.fede.whisperaloud.ShellExtension`: `GetInfo() → a{sv}` (`uuid`, `version`, `shell_version`) | Settings → Top bar indicator (status and version) |
| `/org/fede/whisperaloud/TextInput` | `org.fede.whisperaloud.TextInput`: `InsertText(s text, a{sv} options) → a{sv}`, `GetCapabilities() → a{sv}` (since extension version 5) | The daemon's auto-paste (`clipboard.paste_backend` `auto` or `extension`) |

`FocusChanged` fires on every real `global.display` `notify::focus-window`
change (no polling) and carries the newly-focused window's id as a string
(empty when nothing is focused). The daemon's auto-paste flow subscribes to
it after calling `ActivateWindow` to CONFIRM the captured window actually
regained focus before simulating a paste (WHISP-88) -- `ActivateWindow`'s
own return value only ever meant "the call was issued", never "focus
landed"; if no matching `FocusChanged` arrives (extension too old, or focus
genuinely went elsewhere), the daemon skips the paste rather than risk
typing into the wrong window, and the text stays on the clipboard.

## Text insertion (`org.fede.whisperaloud.TextInput`)

Since extension version 5 the extension puts the transcription where you are
typing, from inside GNOME Shell. It replaces the RemoteDesktop portal as the
default auto-paste method, so there is no consent dialog and no orange
"remote access" indicator. The design and its sources are in
`docs/research/2026-10-05-gnome-text-insertion/report.md` (appendix C).

`InsertText(text, options)`:

1. **Caller check.** Only the current owner of `org.fede.whisperaloud` (the
   daemon) may call it; anyone else gets
   `org.freedesktop.DBus.Error.AccessDenied`. The extension tracks that owner
   with its own `Gio.bus_watch_name`, not GNOME's `DBusSenderChecker` (which
   leaks its watchers on destroy and is bypassed in unsafe mode).
2. **Refusals** (`method: "none"`, with a `reason`): `locked` (lock screen),
   `modal` (a Shell modal such as a polkit dialog or the run dialog is open),
   `no-focus`, `window-mismatch` (the `window_id` option no longer has focus:
   the WHISP-88 check, done atomically at insertion time),
   `password-field` (password/PIN purpose or hidden-text hint), `empty`.
3. **Commit** (`method: "commit"`): when a Wayland text field has input-method
   focus and no preedit is in progress, `Main.inputMethod.commit()` sends the
   text through text-input-v3, in pieces of at most 4000 UTF-8 bytes (the
   Wayland protocol caps a message at 4096 bytes and disconnects the app on
   overflow).
4. **Paste** (`method: "paste"`): otherwise (XWayland apps, apps without
   text-input, a CJK preedit in progress) the text goes to the clipboard
   with `St.Clipboard` and a Clutter virtual keyboard sends **Shift+Insert**.
   Insert, Shift and Control exist in every keyboard layout, unlike the `v`
   of Ctrl+V.
5. **Terminals** (input purpose `TERMINAL`, set by VTE): text containing a
   newline or another control character is never committed, because a
   committed newline is a typed Enter and runs the command line. It is pasted
   with **Ctrl+Shift+Insert** instead (VTE's clipboard paste, which goes
   through bracketed paste when the shell enables it). Text without control
   characters is committed as usual.

Options: `window_id` (`u`), `mode` (`s`: `auto`, `commit` or `paste`),
`paste_shortcut` (`s`: `auto`, `shift+insert`, `ctrl+shift+insert`,
`ctrl+v`, `ctrl+shift+v`). The result is `{method: s, reason: s}`.

`GetCapabilities()` returns `insert_text` (`b`), `methods` (`as`),
`max_commit_bytes` (`u`), `reasons` (`as`) and `version` (`u`). The daemon
uses it, not the version number, to decide whether the extension can insert
text. The extension never logs the text. The virtual keyboard is created in
`enable()` and disposed in `disable()`; the extension has no `session-modes`,
so GNOME disables it (and its D-Bus objects) while the screen is locked.

The pure decisions (gating, chunking, chord choice) are in `insertion.js`,
which has no GNOME Shell imports and is unit-tested with Node
(`tests/test_shell_extension_insertion_js.py`).

While that name has an owner, the daemon hides its own AppIndicator tray
icon, so you do not get two WhisperAloud icons. The tray icon comes back as
soon as the extension is disabled. This is automatic, with no setting:
enabling the extension is already the opt-in, and a bus-name watch follows it
with no polling.

## Install

The extension is not published on extensions.gnome.org yet, so you install
it by hand.

**Flatpak.** The app ships the extension as a zip. Open **Settings →
Advanced → Top bar indicator (GNOME Shell extension)**. The section shows the
exact command and has a Copy button. The command looks like this:

```sh
gnome-extensions install --force ~/.var/app/org.fede.whisperaloud/data/whisper-aloud/gnome-shell-extension/whisperaloud@fede.shell-extension.zip
# GNOME on Wayland loads new extensions only at login: log out and back in, then
gnome-extensions enable whisperaloud@fede
```

An app cannot install a Shell extension from inside its sandbox. The app
copies the zip from `/app/share/whisper-aloud/gnome-shell-extension/` to its
data directory, which is the same path on the host, so you can run the
command in any host terminal.

**Native / source checkout.** `./install.sh` copies the extension into
`~/.local/share/gnome-shell/extensions/` and enables it. To build the zip
yourself instead:

```sh
scripts/pack_shell_extension.py ~/whisperaloud@fede.shell-extension.zip
gnome-extensions install --force ~/whisperaloud@fede.shell-extension.zip
```

On a native install, the Settings section also offers an **Enable/Disable**
button. It goes through `org.gnome.Shell.Extensions`. The Flatpak build does
not get this button: it is deliberately not allowed to talk to that service
(see `docs/flatpak.md`).

## Compatibility

`shell-version` lists 45–51. It was tested on 48. Versions 49, 50 and 51
were added because none of the APIs used here appears in the gjs.guide
porting guides' lists of breaking changes:

- <https://gjs.guide/extensions/upgrading/gnome-shell-49.html>: Meta.Rectangle,
  AppMenuButton, `maximize()` flags, `CursorTracker`, `ClickAction`/`TapAction`.
- <https://gjs.guide/extensions/upgrading/gnome-shell-50.html>: X11 removal,
  display restart signals, `easeAsync()`; no `metadata.json` changes.
- <https://gjs.guide/extensions/upgrading/gnome-shell-51.html>: `disable()` must
  not be async (ours is synchronous), `St.WidgetsOrientation`,
  `PopupMenu.close()` parameters (not used), `makeProxyWrapper` (not used),
  `pointerWatcher`.

The APIs used are `PanelMenu.Button`, `PopupMenuItem`,
`PopupSeparatorMenuItem`, `Main.panel.addToStatusArea`,
`Main.wm.addKeybinding`/`removeKeybinding`, `Main.activateWindow`,
`Main.notify`, `Shell.AppSystem`, `Gio.bus_watch_name`,
`Gio.DBusExportedObject` and `Config.PACKAGE_VERSION`. Text insertion adds
`Main.inputMethod` (`commit`, `currentFocus`, `contentPurpose`,
`contentHints`, `hasPreedit`), `Main.sessionMode.isLocked`, `Main.modalCount`,
`St.Clipboard` and `Clutter.Seat.create_virtual_device`. The Clutter backend
is feature-detected (`global.stage.context.get_backend()` from 47,
`Clutter.get_default_backend()` before; the latter is gone in 51). Runtime
behaviour on 49–51 is still untested.

## Limits

- Not on extensions.gnome.org yet. Publishing it there would give
  one-click install and updates. That is a future option and needs EGO
  review.
- After installing or updating, GNOME on Wayland needs a log out and back in.
- Inside Flatpak, the app cannot tell "not installed" from "installed but
  disabled", because it may only see the extension's own bus name. The
  section then shows both the install command and the enable command.
