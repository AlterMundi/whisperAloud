# D-Bus activation for the WhisperAloud daemon (WHISP-60)

## Why

Flathub rejects tray-only apps that have no window as their launch entry
point (see the flathub#9368 Vocalinux rejection,
`docs/research/2026-10-01-sota-stt-offline/notes/apps_packaging.md`).
WhisperAloud's daemon must therefore start without a user ever running
`whisper-aloud --daemon` or enabling a systemd unit by hand: the GUI's first
D-Bus call against `org.fede.whisperaloud` should be enough. This is ordinary
[D-Bus service activation][spec], not anything WhisperAloud-specific — the
GUI (`src/whisper_aloud/ui/app.py` / `main_window.py`) never spawns a daemon
subprocess; it only ever talks to the bus (`service/client.py`).

[spec]: https://dbus.freedesktop.org/doc/dbus-specification.html#message-bus-starting-services

## The activation file: `data/org.fede.whisperaloud.service`

```ini
[D-BUS Service]
Name=org.fede.whisperaloud
SystemdService=whisper-aloud.service
Exec=/usr/bin/whisper-aloud --daemon
```

Both `SystemdService` and `Exec` are allowed in the same file, and
dbus-daemon chooses between them like this (verified empirically below, and
consistent with the D-Bus specification's `SystemdService` section):

1. If `SystemdService` is present, dbus-daemon first asks systemd (over
   systemd's own D-Bus interface, `org.freedesktop.systemd1`) to start that
   unit.
2. If that fails for *any* reason — no systemd running, no systemd *user*
   session reachable from this bus, the unit file doesn't exist, or systemd
   starts the unit but it exits without claiming the name — dbus-daemon
   falls back to `Exec`, exactly as if `SystemdService` had not been present.
3. If `Exec` is also absent, failure of the `SystemdService` path is final
   (`org.freedesktop.DBus.Error.Spawn.*` / `.ServiceUnknown`).

So one `.service` file activates the daemon correctly on:

- A systemd-managed desktop with `whisper-aloud.service` installed under
  `~/.config/systemd/user/` (debian packaging installs it under
  `lib/systemd/user/`, see `debian/whisper-aloud.install`) — handled by
  `SystemdService`.
- Any other setup — no systemd, a systemd session that doesn't know about
  the unit (e.g. `install.sh --skip-user-service`, or Flatpak, or a plain
  `dbus-daemon`-only container) — handled by the `Exec` fallback.
- A dev checkout, *if* this file is symlinked/copied into a directory
  dbus-daemon scans for service files (e.g. `$XDG_DATA_HOME/dbus-1/services/`
  or `/usr/share/dbus-1/services/`) — neither key fires otherwise, and the
  GUI falls into its "daemon unavailable" retry UI (see
  `src/whisper_aloud/ui/daemon_connect_logic.py`). This repo does not install
  that symlink automatically; `install.sh` (pre-existing, not part of this
  ticket) is the place that would do it for a real install.

### How this was verified

Local `dbus-daemon` (1.16.2) doesn't ship a man page, and the system is not
running a reachable systemd user session, so rather than relying on
documentation alone, the fallback was reproduced directly: a throwaway
private session bus (`dbus-run-session --config-file=...` with a
`<servicedir>` pointing at a temp directory, torn down with the session —
nothing persistent, no real bus or files touched) was given a service file
for `com.example.probe` with:

```ini
SystemdService=com-example-probe-nonexistent.service
Exec=/bin/sh -c 'touch .../exec_ran; exit 1'
```

Calling `Introspect` on `com.example.probe` (the same kind of call
`WhisperAloudClient._try_connect()` makes, which is enough to trigger
auto-start — see below) produced:

```
dbus-daemon[...]: Activating service name='com.example.probe' ...
dbus-daemon[...]: Activated service 'com.example.probe' failed: Process com.example.probe exited with status 1
INTROSPECT_FAILED GLib.Error('...Spawn.ChildExited: Process com.example.probe exited with status 1', ...)
```

and the `exec_ran` marker file existed afterward — i.e. with `SystemdService`
pointing at a unit that cannot possibly be started, dbus-daemon fell through
to `Exec` and ran it. That one-off probe was not kept as an automated test
(it needs a *nested* private bus distinct from the one
`scripts/test_private_bus.sh` already provides, a nonstandard `<servicedir>`
config, and has nothing to do with WhisperAloud's own code); the automated
coverage for this ticket's activation file is
`tests/test_dbus_activation_file.py` (structural: both keys present, in the
same file, Exec targets `--daemon`, SystemdService matches the real unit's
filename) and, where `dbus-run-session --config-file` support allows it,
`tests/e2e/test_dbus_activation.py` (behavioral: the real `whisper_aloud`
daemon, in `WHISPER_ALOUD_TEST_MODE`, actually gets D-Bus-activated via an
`Exec=`-only service file on its own nested private bus).

## How the GUI triggers activation (no subprocess)

`pydbus`'s `Bus.get()` (used by `WhisperAloudClient._try_connect()`) calls
`Gio.DBusConnection.call_sync(..., flags=0, ...)` directly — a raw
`org.freedesktop.DBus.Introspectable.Introspect` call with no
`G_DBUS_CALL_FLAGS_NO_AUTO_START` flag set. Per GDBus/D-Bus semantics, that
means auto-start is *allowed* by default: if `org.fede.whisperaloud` isn't
currently owned and an activation file exists, dbus-daemon activates it as
part of servicing this call. The GUI process never calls `subprocess.Popen`,
`systemctl`, or anything else — the architecture already satisfied "activate,
don't spawn" before this ticket; what this ticket added is:

- The `Exec=` fallback in the activation file itself (previously
  `SystemdService`-only, which would hard-fail off-systemd or in a dev
  checkout with no unit installed).
- Pure decision/backoff logic (`ui/daemon_connect_logic.py`) for what the GUI
  does when activation *does* fail — retry automatically a few times with
  backoff, then fall back to the existing manual "Retry Connection" dialog.

## Window-first startup (desktop entry)

`data/org.fede.whisperaloud.desktop`'s `Exec` already launches the GUI
directly (`/usr/bin/whisper-aloud-gui`, no `--daemon`), so re-launching from
the app grid opens/presents the GUI window, which then connects to (and, via
activation, starts) the daemon — not the other way around. `GtkApplication`'s
default flags (`Gio.ApplicationFlags.FLAGS_NONE`, i.e. *not*
`NON_UNIQUE`) already make a second launch `do_activate()`-present the
existing window instead of opening a duplicate one. Closing the window only
calls `MainWindow.cleanup()` → `client.disconnect()`; it never calls
`client.quit_daemon()`, so the daemon (and its tray icon, which it owns, not
the GUI process) keeps running after the window closes.

## Inside Flatpak (WHISP-61)

The Flatpak build installs a variant of this file at
`/app/share/dbus-1/services/org.fede.whisperaloud.service`, with
`SystemdService=` and the comments removed and with `Exec=whisper-aloud --daemon`
(no `/usr/bin/`). The removal is belt and braces: Flatpak's exporter only keeps
`Name` and `Exec` in exported D-Bus service files, and drops `SystemdService`
explicitly (`allowed_dbus_service_keys` in flatpak's `common/flatpak-dir.c`).
On install, Flatpak rewrites `Exec` to
`flatpak run --branch=stable --arch=x86_64 --command=whisper-aloud org.fede.whisperaloud --daemon`.
GUI-triggered activation therefore works exactly as described above, with no
systemd unit involved. See `docs/flatpak.md`.
