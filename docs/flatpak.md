# Flatpak

The Flatpak build (WHISP-61) lives in `flatpak/org.fede.whisperaloud.yml`.
`scripts/build_flatpak.sh` reproduces the local build and the single-file
bundle. This document has four parts:

1. the portal notes written for WHISP-59, corrected where the manifest
   turned out differently;
2. how the manifest is put together;
3. the **install-and-validate** procedure for a field test on a desktop that
   already has a native install;
4. rollback.

Flathub-specific requirements and blockers are covered in
`docs/flathub-submission.md`.

## First run

One GNOME permission dialog is part of normal first-run behavior for the
Flatpak build: "Add keyboard shortcuts". It is asked once and then
remembered by GNOME (not by WhisperAloud) — you won't see it again on a
later start unless you explicitly revoke the permission.

Since WHISP-119 the "Remote Desktop" dialog of the 0.3.0 build is **no
longer needed by default**: auto-paste goes through the optional GNOME
Shell extension, which needs no portal and shows no "remote access"
indicator. Without the extension, the text is copied and a notification
says "Text copied — press Ctrl+V to paste". The RemoteDesktop portal is
still available as an opt-in rollback (`clipboard.paste_backend =
"remote-desktop"`, see below).

### "Add keyboard shortcuts" (GlobalShortcuts portal)

The first time the daemon starts, it asks the `GlobalShortcuts` portal to
bind the recording-toggle shortcut (default `<Super><Alt>r`). GNOME shows an
"Add keyboard shortcuts" dialog listing WhisperAloud's requested shortcut;
pick the key combination you want (or accept the default) and confirm.

- **If you accept:** the shortcut works immediately, and the daemon's
  `ListShortcuts` check on every later start sees it's already bound, so the
  dialog does not reappear.
- **If you decline or close the dialog:** no global hotkey is registered.
  WhisperAloud still works — start/stop recording via the GUI's record
  button or `whisper-aloud toggle` from a terminal — but the keyboard
  shortcut won't trigger it until you grant this permission (see below for
  how to retry).

### Auto-paste without a dialog: the GNOME Shell extension

Outside Flatpak, WhisperAloud can paste with `ydotool`/`xdotool`. Neither
is available inside the sandbox. The default (`clipboard.paste_backend =
"auto"`) therefore uses the `whisperaloud@fede` extension's
`org.fede.whisperaloud.TextInput.InsertText` when the extension is enabled
(version 5 or newer): GNOME Shell itself types the text into the focused
field, or pastes it with Shift+Insert where typing is not possible. It
reuses the talk-name the manifest already grants (see "GNOME Shell
extension" below): no new permission, no dialog, no orange indicator.
Install it from **Settings → Advanced → Top bar indicator**. See
[`configuration.md`](configuration.md#auto-paste-clipboard) for every
backend.

### Remote Desktop ("Share your screen") — opt-in rollback only

With `clipboard.paste_backend = "remote-desktop"` (the 0.3.0 method, kept
as the rollback switch of
[ADR 0001](decisions/0001-baseline-before-native-text-insertion.md)), the
Flatpak build instead asks the `RemoteDesktop` portal for permission
to synthesize a paste keystroke. While that session is open GNOME shows its
orange "remote access" indicator; this is by design in GNOME Shell and
cannot be turned off from the app. With any other setting no RemoteDesktop
session is ever opened. This dialog appears the first time
WhisperAloud actually tries to auto-paste (not at daemon startup) — GNOME's
"Share your screen" style picker, asking you to choose what the app can
interact with.

1. Choose **Allow Remote Interaction** (WhisperAloud only ever sends a
   keyboard shortcut — it does not request screen capture, pointer, or
   touch access).
2. Check **Remember This Selection** so you aren't asked again.
3. Select **Share** to confirm.

- **If you accept and remember:** the portal issues a `restore_token` that
  WhisperAloud saves (`~/.var/app/org.fede.whisperaloud/data/whisper_aloud/
  portal_restore_token`) and presents on every later session, so the dialog
  does not reappear.
- **If you decline, close the dialog, or don't check "Remember":**
  WhisperAloud falls back to clipboard-only for that paste (and any later
  one, until you grant the permission) — the transcript is still copied, you
  just need to paste it yourself with <kbd>Ctrl</kbd>+<kbd>V</kbd>.

### Changing or revoking either permission later

- **Keyboard shortcut:** GNOME Settings → Keyboard → View and Customize
  Shortcuts. WhisperAloud's shortcut is listed there like any other
  application shortcut; you can rebind or remove it.
- **Remote Desktop / paste access:** GNOME Settings → Apps → WhisperAloud →
  Permissions (or Settings → Privacy → Remote Desktop, depending on your
  GNOME version). Toggling it off there means the next paste attempt asks
  again.
- **Reset everything at once** (both dialogs, plus any other portal grant)
  from a terminal:

  ```sh
  flatpak permission-reset org.fede.whisperaloud
  ```

  The next daemon start (for the shortcut) and the next paste attempt (for
  Remote Desktop) will prompt again.

### Start in background at login (Background portal)

Settings → General → **Startup** → **Start in background at login** makes
the daemon start at every login *without* the window: the model preloads,
the global shortcut binds (no dialog after the first time), and (only with
the opt-in `remote-desktop` paste backend) the RemoteDesktop session is
prewarmed, so the shortcut dictates right away and the top-bar indicator
shows that all is ready. Closing the window never stops the daemon.

- **Turning it on** asks GNOME for permission to run in the background. The
  switch waits (and cannot be toggled again) until you answer; there is no
  time limit. If you allow it, the portal writes
  `~/.config/autostart/org.fede.whisperaloud.desktop` on the host, running
  `flatpak run --command=whisper-aloud org.fede.whisperaloud --daemon`.
  If you refuse or close the dialog, the switch goes back off and says why.
- **Turning it off** asks the portal to remove that entry.
- The confirmed state is stored as `startup.autostart_background` in
  `config.json` and reported by D-Bus `GetStatus()["autostart"]` (API
  1.7.0), next to `"model_state"` and `"hotkey_bound"`; `whisper-aloud
  status` shows all three. If you answer the dialog after closing
  Settings, the answer is still recorded; if you quit the whole app before
  answering, the portal still applies your answer but the switch may show
  the old state until you toggle it again.
- To revoke background permission later: GNOME Settings → Apps →
  WhisperAloud → "Run in Background", or `flatpak permission-reset
  org.fede.whisperaloud` (which resets every portal grant).

Native installs get the same switch; it writes or removes
`~/.config/autostart/whisper-aloud-autostart.desktop` (the name
`install.sh` already uses) instead of calling the portal, and reports any
older entry (`whisperaloud-daemon.desktop`,
`org.fede.whisperaloud-daemon.desktop`) rather than adding a duplicate.

## Portal notes (WHISP-59)

### Correction: no portal `--talk-name` is needed

The original WHISP-59 note asked for
`--talk-name=org.freedesktop.portal.Desktop`. That is wrong, and the
manifest does not use it. Every sandboxed app can always talk to
`org.freedesktop.portal.*`, and Flathub's linter rejects that line
(`finish-args-portal-talk-name`). `RemoteDesktop` (paste), `GlobalShortcuts`
(hotkey) and `Notification` (libnotify picks it up automatically inside
Flatpak) all work without any finish-arg. The portal itself asks the user
for consent for each interface at runtime.

- No `--socket=x11` or `--socket=fallback-x11` is needed for paste.

  Text insertion goes entirely through `NotifyKeyboardKeysym` over the
  `RemoteDesktop` portal D-Bus interface -- it does not touch X11 or
  Wayland input sockets directly, and the sandboxed app never runs
  ydotool/xdotool/wtype (see `src/whisper_aloud/sandbox.py` and
  `src/whisper_aloud/clipboard/paste_selection.py`, which refuse to
  select those backends whenever `is_flatpak_sandbox()` is true).

### Not required for paste, but likely needed elsewhere (out of scope here)

- `--socket=wayland` and/or `--socket=fallback-x11` would still be needed
  if/when the GTK4 GUI itself needs to *display* a window inside the
  sandbox -- that is a windowing concern, unrelated to paste.
- Clipboard *copy* (not paste) uses `wl-copy`. WHISP-61 builds wl-clipboard
  into the Flatpak (`/app/bin/wl-copy`, running against the granted Wayland
  socket), so copying works inside the sandbox on Wayland. Under the X11
  fallback there is no `xclip` in the sandbox, and the file fallback is
  used. Originally (WHISP-59) this ticket did not change clipboard copy
  behavior -- only paste/text-insertion. A future ticket may want to
  route copy through the Clipboard portal's `RequestClipboard` (see
  below) for full sandbox-cleanliness, but it is not required for
  WHISP-59 since `wl-copy`/`xclip` already degrade to the existing
  fallback-file path on failure (`ClipboardManager._copy_fallback`), and
  a sandboxed WhisperAloud with `--talk-name=org.freedesktop.portal.Desktop`
  gets no special clipboard access merely from that line.

### Clipboard portal (`RequestClipboard`) -- noted, not used by this ticket

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

### Portal API version assumptions

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

### Portal behavior that can only be verified on a real install

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

## Running `transcribe` inside the Flatpak sandbox

`whisper-aloud transcribe <host-path>` run via `flatpak run
--command=whisper-aloud org.fede.whisperaloud transcribe <host-path>` does
**not** work for a host file: the sandbox has no filesystem permission at
all (WHISP-96's `finish-args` intentionally grant none for this — see "The
manifest" below), so a plain path argument resolves to nothing inside the
sandbox even though it looks valid on the host shell. This is the same
restriction that motivated the GUI's `Gtk.FileDialog`-based "Transcribe
file…" action, which goes through the document portal instead of a raw
path.

Two ways to feed a host file to the sandboxed CLI:

1. **`--file-forwarding`** (recommended for a single file): Flatpak
   rewrites `@@ ... @@`-wrapped arguments into sandbox-visible document
   portal paths automatically, with no extra manifest permission needed:

   ```sh
   flatpak run --file-forwarding --command=whisper-aloud \
     org.fede.whisperaloud transcribe @@ /home/me/meeting.m4a @@
   ```

2. **stdin** (`-` as the file argument — see
   ["Reading audio from stdin"](cli-reference.md#reading-audio-from-stdin)
   in `docs/cli-reference.md`): pipe the bytes in from the host shell
   instead of naming a path at all:

   ```sh
   flatpak run --command=whisper-aloud org.fede.whisperaloud transcribe - \
     < /home/me/meeting.m4a
   ```

   This also works when the audio is itself produced by another sandboxed
   or host process that has no document-portal access of its own, as long
   as it can write to stdout.

## The manifest (WHISP-61)

- **App ID `org.fede.whisperaloud`.** A Flatpak app may own D-Bus names
  equal to or below its app ID without any `--own-name`. That covers both the
  daemon's `org.fede.whisperaloud` and the GUI's `org.fede.whisperaloud.Gui`,
  and the desktop entry, metainfo and icon already use that ID. For why this
  ID is fine locally but a problem on Flathub, see
  `docs/flathub-submission.md`.
- **Runtime `org.gnome.Platform//51`.** This is the current stable GNOME
  runtime (51.0, freedesktop-sdk 26.08, Python 3.14). It already contains
  GTK 4.24, libadwaita 1.10, PyGObject, GTK 3, libnotify, libsndfile and
  dbus-run-session. Every compiled Python dependency ships a cp314 or abi3
  manylinux wheel.
- **Modules:**
  - PortAudio 19.7.0 (ALSA backend, routed to PipeWire through the runtime's
    pulse ALSA plugin);
  - wl-clipboard 2.3.0 (the daemon's `wl-copy`, run inside the sandbox);
  - libayatana-appindicator, libdbusmenu-gtk3 and ayatana-ido, for the tray
    (a vendored copy of flathub/shared-modules);
  - the Python dependencies in `flatpak/python3-requirements.json`, generated
    by flatpak-pip-generator from `flatpak/requirements-flatpak.txt`. These
    are runtime dependencies only: no pytest, black, ruff or mypy;
  - the app itself, installed with `pip install --no-deps`.
- **Startup is window-first.** `command: whisper-aloud-gui`. The GUI's first
  D-Bus call auto-starts the daemon through the exported
  `share/dbus-1/services/org.fede.whisperaloud.service`. The build strips
  `SystemdService=`, the comments and the `/usr/bin/` prefix from the native
  file. On install, Flatpak rewrites it to
  `Exec=flatpak run --branch=stable --arch=x86_64 --command=whisper-aloud org.fede.whisperaloud --daemon`.
  The desktop entry and its Toggle/Cancel actions get the same rewrite.
- **Start in background at login** (WHISP-87) is a setting, off by
  default: Settings → General → Startup. A Flatpak app cannot write
  `~/.config/autostart` itself, so the switch calls the Background portal
  (`RequestBackground` with `autostart`, `commandline: ["whisper-aloud",
  "--daemon"]`, `dbus-activatable: false`); see
  [Start in background at login](#start-in-background-at-login-background-portal).
  With it off, the daemon starts the first time the window opens, a desktop
  action runs, or a CLI command is used, and until then the global shortcut
  does nothing. The manual autostart entry described below stays as a
  fallback.
- **finish-args.** Each argument is justified in a comment in the manifest:
  - `wayland`, `fallback-x11`, `ipc` and `dri`: display;
  - `pulseaudio`: microphone;
  - `network`: model downloads only;
  - `talk-name=org.kde.StatusNotifierWatcher`: tray;
  - `talk-name=org.mpris.MediaPlayer2.*`: pause media while recording;
  - `talk-name=org.gnome.Shell.Extensions.whisperaloud`: the optional GNOME
    Shell extension. See "GNOME Shell extension (top-bar indicator)" below.

  The manifest deliberately does not request:
  - `--filesystem=home` or any other host path;
  - any portal talk-name;
  - `--own-name`;
  - `talk-name=org.gnome.Shell.Extensions`, GNOME Shell's extension manager.
    See below.

  `flatpak-builder-lint manifest` reports only `appid-url-not-reachable`
  (fede.org; see `docs/flathub-submission.md`). `lint repo` adds only the
  placeholder-screenshot errors.
- **Sandbox-only code paths:**
  - `config.user_config_dir()` and `config.user_data_dir()` follow
    `XDG_CONFIG_HOME` and `XDG_DATA_HOME`, so config and history persist in
    `~/.var/app/org.fede.whisperaloud/{config,data}/whisper_aloud/`.
    Previously they would have been written to an unpersisted `~/.config`
    inside the sandbox and lost;
  - `GainController` (wpctl) is disabled;
  - the indicator skips its gsettings/notify-send GNOME nudge;
  - the hotkey is portal-only (WHISP-64); auto-paste goes through the GNOME
    Shell extension's `TextInput` (WHISP-119), or the RemoteDesktop portal
    only when opted in (WHISP-59);
  - the model cache already follows `XDG_CACHE_HOME`, which is
    `~/.var/app/org.fede.whisperaloud/cache/`. That holds both
    `huggingface/` and `whisper_aloud/models/`.

### GNOME Shell extension (top-bar indicator)

The extension `whisperaloud@fede`
(`contrib/gnome-shell-extension/`, see its README) is an optional
companion. It shows a top-bar indicator: whether the daemon is running,
whether the model is ready, whether the hotkey is registered, and the
recording state. It also exports the window-capture service that
`paste_to_captured_window` needs. Flatpak changes two things about it.

**1. The daemon must reach the extension's bus name.** The extension owns
exactly `org.gnome.Shell.Extensions.whisperaloud` (`EXT_BUS_NAME` in
`extension.js`). On that name it exports:

- `org.fede.whisperaloud.Window`: `GetFocusedWindow`/`ActivateWindow`, used
  by `clipboard/window_manager.py`'s "gnome-extension" backend;
- `org.fede.whisperaloud.ShellExtension.GetInfo`, which Settings uses to
  read the extension's version;
- `org.fede.whisperaloud.TextInput` (extension version 5+):
  `GetCapabilities` and `InsertText`, the default auto-paste method
  (`clipboard/text_insertion.py`). The extension accepts `InsertText` only
  from the current owner of `org.fede.whisperaloud`.

Without a talk-name, the bus proxy hides the name from the sandbox. The
daemon's `WindowManager` then falls back to backend `none`, and
`paste_to_captured_window` cannot re-focus the captured window. The
manifest therefore adds:

```
--talk-name=org.gnome.Shell.Extensions.whisperaloud
```

This is an exact name with no wildcard, owned by our own extension. It does
not match `org.gnome.Shell.Extensions` or `org.gnome.Shell`, because
Flatpak's talk-name matching is exact unless the name ends in `.*`.

The same permission lets the daemon watch the name, through
`NameOwnerChanged` for a name the app may see. The daemon uses that to hide
its AppIndicator tray icon while the extension's indicator is shown, and
shows the icon again when the extension goes away. The watch is
event-driven, with no polling.

**2. The extension can't be installed from inside the sandbox.** The
manifest packs it with `scripts/pack_shell_extension.py` into
`/app/share/whisper-aloud/gnome-shell-extension/whisperaloud@fede.shell-extension.zip`.
**Settings → Advanced → Top bar indicator (GNOME Shell extension)** copies the
zip to `$XDG_DATA_HOME/whisper-aloud/gnome-shell-extension/`. Inside Flatpak,
that is `~/.var/app/org.fede.whisperaloud/data/...`, the same absolute path
on the host. The section shows the host command, with a Copy button:

```sh
gnome-extensions install --force ~/.var/app/org.fede.whisperaloud/data/whisper-aloud/gnome-shell-extension/whisperaloud@fede.shell-extension.zip
# log out and back in (Wayland loads new extensions only at login), then:
gnome-extensions enable whisperaloud@fede
```

**Why not `--talk-name=org.gnome.Shell.Extensions`.** That service would
give a precise installed/enabled status and an in-app Enable/Disable
button. But the same interface also exposes:

- `EnableExtension`/`DisableExtension` for **any** uuid;
- `UninstallExtension`;
- `InstallRemoteExtension`;
- `LaunchExtensionPrefs`;
- the writable `UserExtensionsEnabled` property, which turns all extensions
  off at once.

A compromised or buggy app holding that permission could silently disable
or remove the user's other extensions, including security- or
privacy-related ones. That is far more than a status display needs, and
Flathub review flags it. The sandboxed app therefore uses only its own
extension's name:

- name owned → enabled and running, with `GetInfo` giving the version (an
  older version means "Update available");
- not owned → "not installed **or** disabled".

When the extension is not running, the section can't tell those two cases
apart, so it shows both the install and the enable command. Native,
unsandboxed installs do query `org.gnome.Shell.Extensions`
(`GetExtensionInfo`) and get the Enable/Disable button.

**Limits.**

- Installing is a manual host step.
- Updates need `install --force` and a re-login.
- The extension is not on extensions.gnome.org. Publishing it there would
  give one-click install and updates, and is a possible future step.
- `shell-version` lists 45–51. It was tested on 48. 49–51 were added
  because none of the APIs used appears in the gjs.guide porting guides'
  lists of breaking changes (details in the extension README).

### Building

```sh
flatpak install --user flathub org.flatpak.Builder org.gnome.Platform//51 org.gnome.Sdk//51
scripts/build_flatpak.sh                                  # build, export to ~/REPOS/scripts/flatpak-build/repo, write the bundle
scripts/build_flatpak.sh --no-build --lint --test --e2e   # lint and test the existing build
```

The bundle is written to `~/REPOS/scripts/flatpak-build/whisper-aloud-0.3.0.flatpak`
(about 114 MB). The script never installs or runs the app; it uses
`flatpak build`, not `flatpak run`.

### What was verified inside the build sandbox

The checks below ran with `flatpak build`, with no display, audio, host bus
or network:

- **Imports.** All of the following import from `/app`:
  - whisper_aloud 0.3.0;
  - faster-whisper 1.2.1 and ctranslate2 4.8.2;
  - sherpa-onnx 1.13.8;
  - av 19.0.1 and onnxruntime 1.30.0;
  - numpy 2.5.3 and scipy 1.18.1;
  - soundfile, with the runtime's libsndfile 1.2.2;
  - sounddevice, with the bundled PortAudio 19.7.0;
  - pydbus, psutil, huggingface_hub and tokenizers;
  - Gtk 4.24 and Adw 1.10;
  - AyatanaAppIndicator3 0.1 (in a GTK 3 process).

  `is_flatpak_sandbox()` returns True, `wl-copy` is `/app/bin/wl-copy`, and
  `wpctl` and `ydotool` are absent. GSound is absent, so sound feedback is
  off.
- **Unit suite** (`-m "not e2e and not gui"`, without `tests/bench`, whose
  `jiwer` is a dev-only benchmark dependency): 1226 passed and 18 failed. All
  18 failures come from the environment, not from bugs:
  - 8 config-path tests assert `$HOME/.config/...`, but inside the sandbox
    config deliberately follows `XDG_CONFIG_HOME`;
  - 4 keybinder tests fail because the sandbox forces the portal backend
    (WHISP-59);
  - 2 paste-selection auto-detection tests fail because detection correctly
    reports "sandboxed";
  - 4 `tests/test_integration.py` tests need the `tiny` Whisper model, which
    is not cached in the sandbox and cannot be downloaded without network.
    These tests also download on the host when the model is missing, despite
    not being marked `real_model`.
- **e2e suite** (`scripts/test_e2e.sh` on a private dbus-run-session bus
  inside the sandbox): 38 passed and 2 failed. The 2 failures are the same
  config-path assumption. One run also had two daemons miss the harness's
  5 s readiness timeout under load.

## Install and validate (field test with Fede)

These steps are for the orchestrator to run with Fede. They change Fede's
real session, so do them in order and check each one.

`$ID` means `org.fede.whisperaloud`.

### 0. Record what the native install has (read-only)

```sh
systemctl --user is-enabled whisper-aloud.service; systemctl --user is-active whisper-aloud.service
ls -l ~/.config/systemd/user/whisper-aloud.service \
      ~/.config/autostart/whisper-aloud-autostart.desktop \
      ~/.config/autostart/whisperaloud-daemon.desktop \
      ~/.local/share/dbus-1/services/org.fede.whisperaloud.service \
      ~/.local/share/applications/org.fede.whisperaloud.desktop \
      /usr/share/dbus-1/services/org.fede.whisperaloud.service \
      /usr/share/applications/org.fede.whisperaloud.desktop 2>&1
```

Write down which of these exist and whether the unit was enabled. Rollback
restores exactly that state.

### 1. Stop and park the native install (reversible, nothing deleted)

```sh
whisper-aloud quit || true                       # ask the native daemon to exit cleanly
systemctl --user disable --now whisper-aloud.service || true
PARK=~/whisper-aloud-native-parked; mkdir -p "$PARK"
for f in ~/.config/autostart/whisper-aloud-autostart.desktop \
         ~/.config/autostart/whisperaloud-daemon.desktop \
         ~/.local/share/dbus-1/services/org.fede.whisperaloud.service \
         ~/.local/share/applications/org.fede.whisperaloud.desktop; do
    [ -e "$f" ] && mv -v "$f" "$PARK/"
done
systemctl --user daemon-reload
gdbus call --session --dest org.freedesktop.DBus --object-path /org/freedesktop/DBus \
    --method org.freedesktop.DBus.NameHasOwner org.fede.whisperaloud   # expect (false,)
```

Why each step is needed:

- Files in `~/.local/share/dbus-1/services` and `~/.local/share/applications`
  shadow Flatpak's exported copies, because `XDG_DATA_HOME` comes before
  the Flatpak export dirs in the search path.
- An enabled systemd unit or autostart entry would start the native daemon
  at login. The native daemon would then own `org.fede.whisperaloud`, and
  the Flatpak GUI would quietly talk to it instead of its own.
- If the Debian package is installed, its `/usr/share/...` files have lower
  precedence than the Flatpak exports and can stay. Its
  `lib/systemd/user/whisper-aloud.service` must still be disabled, which
  the `systemctl` line above does.

Leave `~/.local/bin/whisper-aloud*` and the native venv in place: they are
inert once nothing auto-starts them.

### 2. Copy native data into the sandbox (copy, never move)

The Flatpak sees only `~/.var/app/$ID/`. Copy Fede's existing config, history
and models into it. The native originals stay untouched for rollback. There
is deliberately no in-app importer: it would need `--filesystem` read access
to `~/.config/whisper_aloud` and `~/.local/share/whisper_aloud`, which is a
standing permission paid for a one-time copy.

```sh
VAR=~/.var/app/org.fede.whisperaloud
mkdir -p "$VAR/config/whisper_aloud" "$VAR/data/whisper_aloud" "$VAR/cache"
cp -av ~/.config/whisper_aloud/config.json "$VAR/config/whisper_aloud/"
cp -av ~/.local/share/whisper_aloud/.      "$VAR/data/whisper_aloud/"   # history.db, audio/, portal token
# Optional, avoids re-downloading models (reflink where the filesystem supports it):
cp -a --reflink=auto ~/.cache/huggingface   "$VAR/cache/" 2>/dev/null || true
cp -a --reflink=auto ~/.cache/whisper_aloud "$VAR/cache/" 2>/dev/null || true
```

Verify the copy before going on:

```sh
cmp ~/.config/whisper_aloud/config.json "$VAR/config/whisper_aloud/config.json" && echo config-ok
sqlite3 "$VAR/data/whisper_aloud/history.db" 'PRAGMA integrity_check; SELECT count(*) FROM transcriptions;'
sqlite3 ~/.local/share/whisper_aloud/history.db 'SELECT count(*) FROM transcriptions;'   # counts must match
```

If `sqlite3` is not installed, compare the two `history.db` files with `cmp`
instead.

Two caveats:

- If the copied config sets `model.device` to `cuda`, change it to `auto`
  or `cpu` in the sandboxed copy, because the Flatpak has no CUDA.
- If the native config sets absolute `persistence.db_path` or
  `audio_archive_path`, those paths point at the host's
  `~/.local/share/...`, which the sandbox cannot see. Remove those two keys
  from the sandboxed copy so the defaults under `~/.var/app/$ID/data` apply.

### 3. Install the bundle (user scope)

```sh
flatpak install --user --bundle ~/REPOS/scripts/flatpak-build/whisper-aloud-0.3.0.flatpak
flatpak info --user org.fede.whisperaloud
cat ~/.local/share/flatpak/exports/share/dbus-1/services/org.fede.whisperaloud.service
#   expect: Exec=/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=whisper-aloud org.fede.whisperaloud --daemon
```

The runtime `org.gnome.Platform//51` is already installed in user scope on
this machine. On another machine, the bundle's `--runtime-repo` pulls it
from Flathub.

### 4. First run and portal prompts

1. Launch **WhisperAloud** from the app grid. The window must appear first.
   Then check:

   ```sh
   flatpak ps
   busctl --user status org.fede.whisperaloud | grep -E 'PID|Comm|Unit'
   ```

   `flatpak ps` should list two instances, the GUI and the daemon, and
   `busctl` should show that the owner of `org.fede.whisperaloud` is the
   Flatpak daemon, not a native process.
2. **Global shortcut.** On first start the daemon asks the GlobalShortcuts
   portal. GNOME shows an "Add keyboard shortcuts" dialog: accept it, and
   confirm or set the toggle shortcut.
3. **Microphone.** Recording uses PulseAudio. GNOME may show a
   "microphone in use" indicator; there is no consent dialog for PulseAudio
   clients.
4. **Paste.** The first auto-paste opens the RemoteDesktop portal's "Allow
   remote interaction" dialog. Accept it, with "Remember" if offered. After
   that, paste works without asking again (`persist_mode=2`, WHISP-59).
5. **Notifications** arrive through the Notification portal. GNOME lists
   WhisperAloud under Settings → Notifications.
6. **Background.** When the window closes, the daemon keeps running, and
   GNOME lists WhisperAloud under "Background Apps" in the quick settings.
   That is expected. Stopping it there kills the daemon, and the next
   window launch re-activates it.
7. **Start at login** (optional). Turn on Settings → General → Startup →
   "Start in background at login" and accept the background dialog; see
   [Start in background at login](#start-in-background-at-login-background-portal).

### 5. Field-test checklist (things only a real install can verify)

- [ ] Window-first: the app grid opens the window, and the daemon is
      D-Bus-activated (step 4.1). This includes the activated daemon getting
      `WAYLAND_DISPLAY` from the session's activation environment, which
      the tray and `wl-copy` need.
- [ ] Desktop actions: "Toggle Recording" and "Cancel Recording" from the
      app grid context menu.
- [ ] Recording from the real microphone through PortAudio, ALSA, pulse and
      PipeWire. The level meter moves and the device list makes sense.
- [ ] A downloaded model lands under `~/.var/app/$ID/cache/`. A
      transcription completes.
- [ ] Clipboard: after a transcription, `wl-paste` on the host shows the
      text, copied by the sandboxed `wl-copy`.
- [ ] Auto-paste into a focused app through the RemoteDesktop portal, and
      no prompt on the second paste.
- [ ] Global shortcut toggles recording while the window is closed.
- [ ] Tray icon appears, if the AppIndicator extension is enabled, and its
      menu works.
- [ ] Settings → Advanced → Top bar indicator: with the old (v1)
      extension installed, it says "Update available". The install command
      points at an existing file under
      `~/.var/app/org.fede.whisperaloud/data/whisper-aloud/gnome-shell-extension/`,
      and Copy puts it on the clipboard.
- [ ] Running that command on the host, then logging out and back in,
      installs v2. Settings then says "Running (version 2)".
- [ ] Top-bar indicator after login: it shows "Starting…"/"Loading
      model…", then "Ready". The menu shows the model and the bound hotkey.
      Recording, transcribing and stopping the daemon (`flatpak kill`)
      update it without delay. "Start WhisperAloud" brings the daemon back
      through D-Bus activation.
- [ ] While the extension is enabled, the AppIndicator tray icon is hidden.
      After `gnome-extensions disable whisperaloud@fede`, it comes back
      without restarting the daemon.
- [ ] `paste_to_captured_window`: the daemon log shows
      `WindowManager initialized with backend: gnome-extension`, and paste
      goes back to the window that was focused when recording started.
- [ ] With the daemon's hotkey bound (portal), pressing it toggles once,
      not twice: the extension's fallback keybinding is not grabbed.
- [ ] Media pause/resume with a playing MPRIS player.
- [ ] Copied history entries are visible, and new entries persist across
      `flatpak kill org.fede.whisperaloud` and a relaunch.
- [ ] Settings changes persist in `~/.var/app/$ID/config/whisper_aloud/config.json`.
- [ ] CLI from the host:
      `flatpak run --command=whisper-aloud org.fede.whisperaloud status`.
- [ ] Known off: sound feedback (no GSound in the runtime) and mic gain
      boost (no wpctl in the sandbox).

Fallback manual autostart, only if the "Start in background at login"
setting cannot be used (for example, no Background portal backend on the
desktop). Do not combine it with the setting: two entries would both try
to start the daemon, and the loser just exits (`EXIT_ALREADY_RUNNING`).

```sh
cat > ~/.config/autostart/org.fede.whisperaloud-daemon.desktop <<'EOT'
[Desktop Entry]
Type=Application
Name=WhisperAloud background service
Exec=flatpak run --command=whisper-aloud org.fede.whisperaloud --daemon
X-GNOME-Autostart-enabled=true
NoDisplay=true
EOT
```

## Rollback

The Flatpak and the native install never share files: native data was only
copied, never moved. Rollback therefore leaves `~/.config/whisper_aloud` and
`~/.local/share/whisper_aloud` exactly as they were before step 2. Anything
recorded while testing the Flatpak stays only in `~/.var/app/$ID/`.

```sh
flatpak kill org.fede.whisperaloud 2>/dev/null || true
flatpak uninstall --user org.fede.whisperaloud          # keeps ~/.var/app/$ID (add --delete-data to drop it)
rm -f ~/.config/autostart/org.fede.whisperaloud-daemon.desktop   # only if created above
rm -f ~/.config/autostart/org.fede.whisperaloud.desktop          # written by the portal if "Start in background at login" was on
PARK=~/whisper-aloud-native-parked
for f in "$PARK"/*; do
    case "$(basename "$f")" in
        org.fede.whisperaloud.service) mv -v "$f" ~/.local/share/dbus-1/services/ ;;
        org.fede.whisperaloud.desktop) mv -v "$f" ~/.local/share/applications/ ;;
        *.desktop)                     mv -v "$f" ~/.config/autostart/ ;;
    esac
done
systemctl --user daemon-reload
systemctl --user enable --now whisper-aloud.service      # only if it was enabled in step 0
```

Then confirm that the native daemon owns the name again:

```sh
busctl --user status org.fede.whisperaloud
```

The output should show the native venv's python, not `flatpak`.
