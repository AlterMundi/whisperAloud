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
- `StatusChanged(s)`, `ModelStateChanged(s, s)` and `ConfigChanged` signals
  carry state changes.
- One `GetStatus()` call runs when the daemon appears, when it returns to
  `idle` and when its config changes. Every key is read defensively, so
  daemons older than API 1.6.0/1.7.0 still work.
- `hotkey_bound` has no change signal. While it is false and the daemon
  has a hotkey backend, the extension re-reads `GetStatus()` after 2, 5, 10,
  20, 40 and 60 s, then stops until the next `ConfigChanged` or daemon
  restart. This is the extension's only timer. It is bounded and is not
  used to detect whether the daemon is running.

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
and exports two interfaces on it:

| Object | Interface | Used by |
|---|---|---|
| `/org/fede/whisperaloud/Window` | `org.fede.whisperaloud.Window`: `GetFocusedWindow() → a{sv}`, `ActivateWindow(u) → b` | The daemon's `WindowManager` "gnome-extension" backend (`paste_to_captured_window`) |
| `/org/fede/whisperaloud/ShellExtension` | `org.fede.whisperaloud.ShellExtension`: `GetInfo() → a{sv}` (`uuid`, `version`, `shell_version`) | Settings → Top bar indicator (status and version) |

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
`Gio.DBusExportedObject` and `Config.PACKAGE_VERSION`. Runtime behaviour on
49–51 is still untested.

## Limits

- Not on extensions.gnome.org yet. Publishing it there would give
  one-click install and updates. That is a future option and needs EGO
  review.
- After installing or updating, GNOME on Wayland needs a log out and back in.
- Inside Flatpak, the app cannot tell "not installed" from "installed but
  disabled", because it may only see the extension's own bus name. The
  section then shows both the install command and the enable command.
