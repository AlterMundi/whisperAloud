# WHISP-64: xdg-desktop-portal GlobalShortcuts re-prompt — findings

## Problem

WhisperAloud's daemon runs outside Flatpak (a plain systemd user service),
and called `BindShortcuts` unconditionally on every start. GNOME's portal
backend popped "Add Keyboard Shortcuts" every single daemon start instead
of remembering the previous grant.

## Portal semantics (with sources)

### 1. Host app identification: `org.freedesktop.host.portal.Registry.Register`

- Spec: <https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.host.portal.Registry.html>
- Signature: `Register(IN app_id s, IN options a{sv})` — a plain synchronous
  method call, **not** the async Request/Response pattern used by the
  app-facing portals.
- "An Application identifier, ... should correspond to a .desktop file
  basename." `options` is reserved for future use (empty dict today).
- **Timing**: "Registering must be done before any portal method call;
  registering after such a call will result in an error." It can also only
  be called **once** per connection.
- **Purpose**: lets unsandboxed ("host") apps associate their D-Bus
  connection with a stable app_id, the same way Flatpak's sandbox already
  does for sandboxed apps automatically. Without it, xdg-desktop-portal
  >= 1.19/1.20 may refuse app-id-gated portals outright
  (`org.freedesktop.portal.Error.NotAllowed: An app id is required`), or —
  on older/more lenient builds — fall back to deriving an identity from the
  systemd scope the process runs in (`app-<id>-<pid>.scope` naming), which
  does not exist for a daemon started as a user-scope systemd *service*
  unit (not a transient scope), and is reported as broken even for the
  processes it was meant to support (see the Electron issue below). Either
  way, each daemon start then looks like a brand-new, never-before-seen
  app, so any previous shortcut grant is never found and the dialog
  reappears.
- Corroborating report: xdg-desktop-portal/electron issue describing
  exactly this failure mode for a non-Flatpak Electron app —
  <https://github.com/electron/electron/issues/51875> — confirms the
  "before any other portal call" ordering requirement and the
  `app-electron-<pid>.scope` legacy fallback that stopped working in 1.21+.

### 2. `org.freedesktop.portal.GlobalShortcuts`

- Spec: <https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.GlobalShortcuts.html>
- Method signatures (async Request/Response, like all portal calls that can
  show UI):
  ```
  CreateSession(IN options a{sv}) -> OUT handle o
  ListShortcuts(IN session_handle o, IN options a{sv}) -> OUT handle o
  BindShortcuts(IN session_handle o, IN shortcuts a(sa{sv}),
                IN parent_window s, IN options a{sv}) -> OUT handle o
  Activated(session_handle o, shortcut_id s, timestamp t, options a{sv})  [signal]
  ```
- All sessions are bound to the application that created them (i.e. scoped
  by app_id, once that's established via Registry.Register).
- `ListShortcuts`: if `BindShortcuts` hasn't been called yet on the current
  session, it "returns the shortcuts that were successfully bound in a
  previous session by this application" — i.e. it reflects standing grants
  for our app_id, not just the current session. This is exactly the signal
  WHISP-64 needs: if our shortcut id is already there, we must not call
  `BindShortcuts` again.
- The docs state apps "can only attempt to bind shortcuts of a session
  once" — reinforcing that `BindShortcuts` is the operation that shows
  (or re-shows) the consent UI, and must be treated as a one-time action
  per logical shortcut set, not something to repeat defensively on every
  start.

## What we implemented

`src/whisper_aloud/service/hotkey_portal.py` (`PortalHotkeys`):

1. `__init__` now calls a new `_register_app_id()` right after connecting
   to the session bus and **before** constructing the `GlobalShortcuts`
   proxy or calling `CreateSession` — i.e. before any other portal call on
   that connection, as required. It calls
   `Registry.Register("org.fede.whisperaloud", {})` via a plain
   `Gio.DBusConnection.call_sync`; any failure (missing interface/method on
   older portals, `NotAllowed`, etc.) is caught and logged at `debug`
   level only — never fatal.
2. New `list_shortcuts()` method: runs `ListShortcuts` through the same
   Request/Response wait helper as `CreateSession`/`BindShortcuts`
   (factored out into a shared `_call_request()`), and returns the set of
   shortcut ids already bound for our app_id (empty set on any failure, so
   callers safely fall back to binding).
3. New `bind_shortcuts_if_needed(shortcuts, on_activated)`: calls
   `list_shortcuts()` first; if every requested shortcut id is already
   present, it **skips `BindShortcuts` entirely** and just subscribes to
   `Activated` (+ best-effort `EnableShortcuts`) via a shared
   `_finish_binding()` helper. Only calls the existing `bind_shortcuts()`
   (which does prompt) when at least one requested id is missing.
   `HotkeyManager._register_portal` (in `hotkey.py`) now calls this
   instead of `bind_shortcuts()` directly.
4. No retry loop was added anywhere that re-binds; `bind_shortcuts_if_needed`
   is idempotent within a process (calling it twice in a row with the same
   already-bound ids calls `ListShortcuts` again but not `BindShortcuts`
   again — see `test_no_retry_rebind_within_same_session`).
5. Verified (and pinned with a regression test) that the portal is only
   ever touched from inside `WhisperAloudService.__init__`, which
   WHISP-63 already guarantees runs strictly after `acquire_bus_name()`
   succeeds in `__main__.handle_daemon_command` — a losing daemon start
   exits via `AlreadyRunningError` before `WhisperAloudService` (and hence
   `HotkeyManager`/`PortalHotkeys`) is ever constructed, so it can never
   contact the portal at all.

## Tests

All in `tests/test_hotkey_portal_flow.py` (new file, to avoid clashing
with other in-flight branches touching `tests/test_hotkey.py`):

- Unit tests (`TestRegisterCalledBeforeCreateSession`,
  `TestListShortcutsParsing`) mock `Gio.bus_get_sync`, `Gio.DBusProxy.new_sync`,
  and `PortalHotkeys._call_request` to record and assert the call order
  `Register -> CreateSession -> ListShortcuts -> (BindShortcuts only if
  missing)`, including: first run binds; second run (already bound) skips
  Bind; adding a new shortcut id still binds; a missing/older Registry
  interface doesn't block the rest of the flow; calling
  `bind_shortcuts_if_needed` twice doesn't double-bind.
- `tests/test_daemon.py::TestPortalTouchedOnlyAfterBusNameOwned` pins the
  "portal only touched after the bus name is owned" invariant directly
  against `daemon.py`/`acquire_bus_name`.
- An **optional integration test**,
  `TestPortalIntegrationOnPrivateBus::test_bind_once_then_skipped_on_second_run`,
  starts a minimal fake `org.freedesktop.portal.Desktop` (Registry +
  GlobalShortcuts) as a real D-Bus service and drives `PortalHotkeys`
  against it for real, asserting `BindShortcuts` is called exactly once on
  the first run and zero times on a second run with the same app_id. It is
  marked `@pytest.mark.dbus_private` and self-skips unless the bus this
  process actually started on (stashed by `conftest.py` under
  `_WHISPER_ALOUD_TEST_ORIGINAL_DBUS_ADDR`, since every other test runs
  with `DBUS_SESSION_BUS_ADDRESS` pointed at a nonexistent path) is neither
  the user's real `$XDG_RUNTIME_DIR/bus` nor a bus with `org.freedesktop.systemd1`
  owned on it. Run only via:

  ```
  timeout 60 dbus-run-session -- python -m pytest -m dbus_private \
      tests/test_hotkey_portal_flow.py -q
  ```

  Verified manually with this exact invocation: passes (1 passed) under
  `dbus-run-session`, and self-skips (1 skipped) when run normally.

## What the user should expect on the next daemon restart

- **One dialog, once.** The very first start after this fix still shows
  "Add Keyboard Shortcuts" (nothing was bound for this app_id before, or
  the previous attempts under the broken flow never actually persisted).
- **Every start after that: no dialog.** `Registry.Register` gives the
  compositor a stable app_id across restarts; `ListShortcuts` then finds
  the previously bound "toggle" shortcut for that app_id and
  `BindShortcuts` is skipped entirely.
- If the user ever changes `hotkey.toggle_recording` in config, the daemon
  calls `unregister()` then `register()` again (existing `daemon.py`
  logic, unchanged by this ticket) — because this creates a **new** accel
  for the same shortcut id, the portal is likely to re-prompt for that one
  change (the id is already "bound" by id, but typically the accelerator
  itself is what the user picks/confirms in the compositor's own shortcut
  settings, not something this app can silently swap). That one-time
  re-prompt on a deliberate hotkey change is expected and out of scope for
  this ticket, which is only about re-prompting on every *unchanged*
  start.
