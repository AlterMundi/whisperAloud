# Native text insertion on GNOME Wayland: put the transcription where the user is typing

**Date:** 2026-10-05 · **Target:** GNOME 48.7 (Wayland), Mutter 48.7, IBus 1.5.32, xdg-desktop-portal 1.20.3 / -gnome 48 (Debian 13), WhisperAloud as Flatpak + optional `whisperaloud@fede` extension.

**Method:**
- Read GNOME Shell 48.7's JavaScript from the installed `libshell-16.so` gresource.
- Read Mutter 48's typelibs on this machine.
- Read the WhisperAloud code.
- Web research.
- Nothing was run against the live session. No calls to the session bus, IBus, GNOME Shell or the portals, and `~/.config/ibus` was not read.

**Labels:** **[V]** = checked this session against source or a primary document. **[I]** = inferred, or background knowledge not source-checked this session; these are hypotheses for the §7 field tests.

## 1. Executive summary

**The orange indicator is GNOME working as designed, so no portal setting will remove it.** **[V]**
- In GNOME Shell 48, `js/ui/status/remoteAccess.js` `_onNewHandle` adds every remote-access handle that has `!handle.isRecording` to the "screen sharing" indicator on Wayland.
- `MIN_SHARED_INDICATOR_VISIBLE_TIME_US = 5 * GLib.TIME_SPAN_SECOND` keeps the indicator up for at least 5 s.
- There is no exception for keyboard-only sessions.
- So a session per paste, or per recording, still flashes the indicator for at least 5 s each time.

**GNOME already has a native "insert text into the focused app" primitive, inside the compositor.** **[V]** GNOME Shell 48's on-screen keyboard uses it (`js/ui/keyboard.js` `KeyboardController.commit`):
- If `Main.inputMethod.currentFocus` is set, it calls `Main.inputMethod.commit(str)`. That is `Clutter.InputMethod.commit`, which Mutter delivers as a text-input-v3 `commit_string`.
- Otherwise (for example X11/XWayland clients), it uses `global.stage.context.get_backend().get_default_seat().create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE)` and `notify_keyval(...)`.

**IBus engines use the same final step on GNOME Wayland.** **[V]** GNOME Shell is the IBus client: it creates an IBus input context named `'gnome-shell'`, and that context's `commit-text` handler calls `this.commit(text)` (`js/misc/inputMethod.js` `_onCommitText`). An extension calling `Main.inputMethod.commit()` therefore delivers text the same way an IBus dictation engine does, with no input-source switching and no portal.

**Ranked recommendation**
1. **Primary: insertion done by our extension.**
   - The extension gets a new D-Bus method `InsertText(s text, a{sv} options)`.
   - Inside the compositor it checks the caller (only our daemon) and the target window.
   - It commits with `Main.inputMethod.commit()` when an IM focus exists.
   - Otherwise it pastes: `St.Clipboard` set_text, then Ctrl+V or Ctrl+Shift+V sent through a Clutter virtual keyboard.
   - No portal, no indicator, and no new Flatpak permission: the manifest already has `--talk-name=org.gnome.Shell.Extensions.whisperaloud` [V].
2. **Fallback without the extension: clipboard only, plus a "Press Ctrl+V" notification.**
   - RemoteDesktop auto-paste becomes an explicit opt-in, off by default, with a warning about the indicator.
   - `prewarm()` stops running by default.
3. **Optional, longer term: an IBus engine installed on the host** (`.deb`/distro package, not inside the Flatpak), driven by the daemon over D-Bus.
   - It also covers Qt apps that talk to IBus directly.
   - It supports preedit for streaming dictation.
   - Its cost is the input-source switching UX.

**Not recommended**
- Keeping a RemoteDesktop session open, or opening one per recording or paste: the indicator appears either way, for at least 5 s [V].
- Calling `org.gnome.Mutter.RemoteDesktop` directly: it is still a remote-access handle [I]. A Flatpak would also need a talk-name that lets it control the whole session.
- ydotool or uinput: needs root or the `input` group, can't run in the sandbox, and injects at kernel level.
- Hiding the indicator from our extension: security-hostile, and EGO would reject it [I].

## 2. Comparison table

| | Extension InsertText (commit, else paste) | IBus engine (host pkg) | RemoteDesktop portal (today) | Clipboard only | ydotool/dotool |
|---|---|---|---|---|---|
| UX | Text appears in place, no dialog | Same, but the user must switch to or keep a "Voice" input source | Dialog once; indicator permanent or ≥5 s | User presses Ctrl+V | Blind typing |
| Permissions | User enables our extension (already documented) | Root install + add the input source in Settings | Portal consent (restore_token) | None | udev rule or `input` group |
| Flatpak | Yes, over the already-granted name [V] | Engine must be on the host; daemon talks to it over our own name [I] | Yes | Yes | No |
| Works without our extension | No | Yes | Yes | Yes | Yes |
| Wayland GTK, Firefox | Commit through text-input-v3 [I] | Yes | Yes | Manual | Yes |
| Qt with `QT_IM_MODULE=ibus` (set in this session [V]) | No Clutter IM focus, so paste fallback [I] | Yes [I] | Yes | Manual | Yes |
| XWayland | Paste through virtual keyboard (the OSK does the same [V]) | Via the X11 IBus path [I] | Yes | Manual | Yes |
| Terminals | VTE accepts commits as typing [I]; no bracketed paste, so strip newlines when purpose is TERMINAL | Same as commit | Yes, with Ctrl+Shift+V | Manual | Yes |
| Indicator | **No**. Only remote-access controller handles drive it [V source, confirm in T1] | No | **Yes** [V] | No | No |
| Preedit / streaming | `Clutter.InputMethod.set_preedit_text` exists [V], but may fight the user's own IBus engine [I] | Native | No | No | No |
| Effort | Small to medium: about 150 lines of GJS plus a daemon backend; version gating reused | Large: separate package, input-source UX, per-distro packaging | Done | Done | n/a |
| Risks | IM-focus race after ActivateWindow; keymaps for the paste chord; EGO review; API drift 45–51 | Users won't switch sources; only one IBus engine can be active (mozc, hangul, m17n are installed here [V]) | The indicator | Friction | Security, not sandboxable |

## 3. Findings

### 3.1 Extension route [mostly V]

**The Shell itself uses this pattern** (`KeyboardController`, keyboard.js around lines 1990–2140):
- It creates a virtual keyboard device.
- `commit()` sends raw keyvals when there is no IM focus or a modifier is held (comment: "If there is no IM focus (e.g. with X11 clients)…"). Otherwise it calls `Main.inputMethod.commit(str)`.
- `destroy()` calls `run_dispose()` so held keys are released. Our `disable()` must do the same.

**The APIs exist in the Clutter-16 typelib [V]:**
- `Clutter.InputMethod`: `commit`, `delete_surrounding`, `set_preedit_text`, `forward_key`.
- `Clutter.Seat.create_virtual_device`.
- `Clutter.VirtualInputDevice`: `notify_keyval`, `notify_key`.

**`Main.inputMethod` [V]:**
- Created in `main.js` and exported.
- Exposes `currentFocus`, `contentPurpose` (with `PASSWORD` and `TERMINAL` mapped), `contentHints`, `getSurroundingText()` and `hasPreedit()`.

**Restricting the caller [V]:**
- `DBusSenderChecker` in `resource:///org/gnome/shell/misc/util.js` allows only callers that currently own the listed well-known names; it is bypassed only in unsafe mode.
- Better: reimplement it in about 20 lines that allow only the owner of `org.fede.whisperaloud`.

**Lock screen:**
- Our `metadata.json` has no `session-modes`, so GNOME disables the extension while locked [I: gjs.guide behaviour].
- Also refuse when `Main.sessionMode.isLocked` [V: property exists].

**Indicator:**
- Only `remoteAccess.js` shows it, and only for remote-access controller handles [V].
- Shell-internal virtual devices don't create such handles [I, confirm in T1].

**Clipboard:** `St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text)` [I: long-standing API].

**API drift:**
- Use `global.stage.context.get_backend()`, which 48 itself uses [V], instead of the deprecated `Clutter.get_default_backend()` [I].
- Check the gjs.guide port guides for 49, 50 and 51.

**Precedents:**
- **EasySpeak [V]** ships a Shell extension, but its dictation pastes Ctrl+V or Ctrl+Shift+V (for terminals) through **`org.gnome.Mutter.RemoteDesktop`** directly (`src/core/mediakeys.py`: CreateSession→Start→NotifyKeyboardKeycode→Stop per chord). It uses AT-SPI only to check focus, because "Chromium-based apps accept [AT-SPI insertion] and quietly discard it" (`src/plugins/dictation/__init__.py`).
- Clipboard Indicator ("paste on select") and Smile's companion extension reportedly send Ctrl+V through a Clutter virtual keyboard [I].

**EGO:**
- Exporting D-Bus services isn't forbidden, and we already do it [I].
- An injecting method must be restricted to our daemon, inactive while locked, and documented.
- If review blocks it, we keep distributing the extension from our own zip, as today.

### 3.2 IBus route

**Delivery path [V]:**
- GNOME runs `ibus-daemon --panel disable`, adding `--xim` only on X11 (`ibusManager.js` `_spawn`).
- The path is: engine `commit_text` → the `'gnome-shell'` input context → `Clutter.InputMethod.commit` → text-input-v3.

**Engine model [I]:**
- An engine is declared by a component XML in `/usr/share/ibus/component/`. This machine has `simple`, `m17n`, `mozc`, `hangul` and `gtkextension` [V].
- ibus-daemon spawns it, and it gets focus and keys only while it is the **active input source**.
- A dictation engine would own something like `org.fede.whisperaloud.Ime` and expose `Commit(s)` / `SetPreedit(s)` for the daemon, then call `commit_text` / `update_preedit_text`.

**Packaging [I]:**
- `IBUS_COMPONENT_PATH` is read by ibus-daemon, which GNOME Shell spawns, so users can't practically set it, and there is no supported user-level component directory. That leaves a host package.
- A Flatpak can't register an engine: the in-sandbox `org.freedesktop.portal.IBus` only offers `CreateInputContext`.

**Activation UX [I]:**
- The engine must be in `org.gnome.desktop.input-sources` and selected.
- With `<layout>default</layout>` and pass-through key handling it can sit over the user's keyboard layout, as ibus-typing-booster does.
- It can't coexist with another IBus engine such as mozc, pinyin or hangul.

**Panel-extension commit [I]:**
- ibus-emojier's `org.freedesktop.IBus.Panel.Extension` (`gtkextension.xml` [V]) commits whatever the active engine is.
- It has a single owner and isn't a third-party integration point.

**GNOME's stance:**
- [V] The GNOME Discourse thread "Proposal: desktop-wide offline speech-to-text dictation support" (2026-06-26):
  - Adrian Vovk (release team) pointed to ibus-speech-to-text (Vosk / whisper.cpp).
  - Antonio Zugaldia said his Speed of Sound "uses desktop portals".
  - The proposer asked whether dictation fits "better as an accessibility feature".
- [I] No native GNOME dictation API in 48–50, and none found on roadmaps. Re-check the GNOME 50/51 notes.

### 3.3 Portals [I except the indicator]
- **RemoteDesktop:** the indicator is by design [V]. ConnectToEIS (libei) still creates a session, so the same indicator.
- **InputCapture:** captures input (Synergy/Barrier style); it doesn't inject.
- **Clipboard portal:** works only inside a RemoteDesktop session.
- **Nothing new:** no text-insertion, virtual-keyboard or dictation portal in 1.20, and no accepted 2025–2026 proposal found. KeePassXC auto-type on Wayland has been blocked on the same gap.

### 3.4 Comparable apps

| App | Mechanism | GNOME Wayland |
|---|---|---|
| EasySpeak [V] | wl-copy + Ctrl+V chord through Mutter RemoteDesktop, one session per chord; AT-SPI focus check; own extension for pointer and windows | Works unsandboxed; remote-access session each time [I] |
| Speed of Sound [V: author's statement] | "Desktop portals" (RemoteDesktop) | Same indicator [I] |
| ibus-speech-to-text [I] | IBus engine | Native; system install + input-source switch |
| nerd-dictation [I] | xdotool / ydotool / dotool / wtype | xdotool reaches XWayland only; wtype needs zwp_virtual_keyboard_v1, which Mutter lacks; the others need uinput |
| Numen [I] | dotool (uinput) | Needs `input` group or udev rule |
| Speech Note [I] | xdotool/ydotool helpers + clipboard | Partial |
| espanso [I] | uinput | Needs capabilities |

**Pattern:** apps outside the compositor end up on uinput or RemoteDesktop. The clean options are an IBus engine or code inside the Shell.

### 3.5 GTK and libadwaita
- No API and no guidance for one app to send text into another [I]. `GtkIMContext` is receiver-side only. Checked `docs.gtk.org/gtk4/class.IMContext.html` and `running.html`.
- What matters for us is the receiving side. In this session `GTK_IM_MODULE` is unset, `QT_IM_MODULE=ibus` and `XMODIFIERS=@im=ibus` [V]. Qt apps therefore bypass Clutter's IM, which is why the extension needs the paste fallback.

## 4. Recommended design

### 4.1 Extension
- Keep the bus name `org.gnome.Shell.Extensions.whisperaloud`, so the manifest doesn't change.
- New path `/org/fede/whisperaloud/TextInput`.
- Bump `metadata.json` version to 5 and gate on it like `_FOCUS_CHANGED_MIN_VERSION`.

```xml
<interface name="org.fede.whisperaloud.TextInput">
  <!-- options: window_id u, mode s ("auto"|"commit"|"paste"), paste_shortcut s ("ctrl+v"|"ctrl+shift+v"|"auto"), allow_password b
       result: method s ("commit"|"paste"|"none"), reason s ("no-focus","window-mismatch","locked","password-field",...) -->
  <method name="InsertText">
    <arg direction="in" type="s" name="text"/>
    <arg direction="in" type="a{sv}" name="options"/>
    <arg direction="out" type="a{sv}" name="result"/>
  </method>
  <method name="GetCapabilities"><arg direction="out" type="a{sv}" name="caps"/></method>
</interface>
```

```js
const DAEMON_NAME = 'org.fede.whisperaloud';
class TextInserter {
  enable() {
    const seat = global.stage.context.get_backend().get_default_seat();
    this._kbd = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
    this._daemonOwner = null;
    this._watch = Gio.bus_watch_name(Gio.BusType.SESSION, DAEMON_NAME, Gio.BusNameWatcherFlags.NONE,
      (_c, _n, owner) => { this._daemonOwner = owner; }, () => { this._daemonOwner = null; });
  }
  disable() { Gio.bus_unwatch_name(this._watch); this._kbd?.run_dispose(); this._kbd = null; }
  InsertTextAsync([text, options], invocation) {
    if (invocation.get_sender() !== this._daemonOwner)
      return invocation.return_dbus_error('org.freedesktop.DBus.Error.AccessDenied', 'not the WhisperAloud daemon');
    invocation.return_value(new GLib.Variant('(a{sv})', [this._insert(text, options)]));
  }
  _insert(text, o) {
    const r = (m, why = '') => ({method: new GLib.Variant('s', m), reason: new GLib.Variant('s', why)});
    if (Main.sessionMode.isLocked) return r('none', 'locked');
    const win = global.display.get_focus_window();
    const wantId = o.window_id?.deepUnpack();
    if (wantId !== undefined && (!win || win.get_id() !== wantId)) return r('none', 'window-mismatch'); // atomic WHISP-88 check
    const im = Main.inputMethod, purpose = im.contentPurpose;
    if (purpose === Clutter.InputContentPurpose.PASSWORD && !o.allow_password?.deepUnpack()) return r('none', 'password-field');
    const terminal = purpose === Clutter.InputContentPurpose.TERMINAL;
    if (terminal) text = text.replace(/[\r\n]+/g, ' ');       // commit bypasses bracketed paste
    const mode = o.mode?.deepUnpack() ?? 'auto';
    if (mode !== 'paste' && im.currentFocus && !im.hasPreedit()) { im.commit(text); return r('commit'); }
    if (mode === 'commit') return r('none', 'no-im-focus');
    St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text);
    const sc = o.paste_shortcut?.deepUnpack() ?? 'auto';
    const shift = sc === 'ctrl+shift+v' || (sc === 'auto' && terminal);
    this._chord(shift ? [Clutter.KEY_Control_L, Clutter.KEY_Shift_L, Clutter.KEY_v] : [Clutter.KEY_Control_L, Clutter.KEY_v]);
    return r('paste');
  }
  _chord(keys) {
    const t = Clutter.get_current_event_time() * 1000;          // µs, as keyboard.js does
    for (const k of keys) this._kbd.notify_keyval(t, k, Clutter.KeyState.PRESSED);
    for (const k of [...keys].reverse()) this._kbd.notify_keyval(t, k, Clutter.KeyState.RELEASED);
  }
}
```

### 4.2 Daemon
- **New backend:** `clipboard/ExtensionTextInserter`, an async `Gio.DBusProxy.call("InsertText", (text, {"window_id": u, "paste_shortcut": s}))`, with the reply handled on the main loop.
- **Backend order in `paste_selection.py`:** extension (version ≥ 5, present per `ShellExtensionWatcher`) → clipboard only + notification.
  - Native installs keep ydotool and xdotool.
  - RemoteDesktop only when opted in (for example `clipboard.paste_backend="remote-desktop"`); no prewarm by default.
- **WHISP-88:** pass `window_id` so the extension checks focus atomically.
  - The daemon still calls `ActivateWindow` and waits for `FocusChanged` to bring the window back.
  - `window-mismatch` and `no-focus` map to the existing "paste skipped" notice. The text is already on the clipboard via wl-copy.
- **`paste_delay_ms`:** likely unnecessary for commits; keep it for paste.

### 4.3 Optional IBus engine
- Package `whisper-aloud-ibus`:
  - `/usr/share/ibus/component/whisperaloud.xml` with `<layout>default</layout>`.
  - `/usr/libexec/whisper-aloud-ibus-engine`: a Python `IBus.Engine` that passes keys through and owns `org.fede.whisperaloud.Ime` with `Commit(s)`, `SetPreedit(s)` and `ClearPreedit()`, effective only while focused.
- The Flatpak daemon would need `--talk-name=org.fede.whisperaloud.Ime` (our own prefix).
- Build only if T2/T3 show gaps, or for streaming preedit.

## 5. Risks and open questions
1. **IM focus right after ActivateWindow.**
   - The client re-enables text-input only after `wl_keyboard.enter`, so an early commit sees no focus and drops to the paste fallback (still correct).
   - The Shell's IM overrides `vfunc_focus_in` without emitting a signal [V].
   - No timers (project rule). Accept the fallback, or retry once on an `inputMethod` notify (T3).
   - In the common case the user never leaves the window, so this race doesn't happen.
2. **Paste chord and keymaps.**
   - `notify_keyval(KEY_v)` may fail when a non-Latin layout group is active [I].
   - `notify_key` with evdev codes breaks on Dvorak.
   - Commit avoids both.
3. **Terminals.** A committed newline runs a command, so strip newlines for TERMINAL purpose and consider never committing a trailing newline anywhere.
4. **User preedit (CJK).** Fall back to paste when `hasPreedit()`.
5. **Security.**
   - The sender check limits callers to our daemon's unique name.
   - Unsandboxed same-user code is already trusted; sandboxed apps can't reach our name unless granted.
   - The extension must never log the text.
6. **API drift.** Re-check GNOME 49/50/51 before claiming support; metadata already claims 45–51.

## 6. Verified vs. inferred
- **[V] local (GNOME Shell 48.7 gresource, Clutter-16 typelib, repo):**
  - OSK commit + virtual keyboard pattern.
  - IBus commit path through `Main.inputMethod.commit`.
  - `ibus-daemon --panel disable` (plus `--xim` on X11).
  - Indicator logic and its 5 s minimum.
  - `DBusSenderChecker`.
  - `isLocked`.
  - The Clutter methods named above.
  - Installed IBus components.
  - The session's IM env vars.
  - The manifest's talk-name.
- **[V] web:**
  - GNOME Discourse thread 35858.
  - EasySpeak source and docs.
- **[I] not source-checked this session:**
  - Mutter text-input internals.
  - Per-toolkit text-input-v3 support (Qt, Chromium `--enable-wayland-ime`, VTE).
  - IBus component path and panel extension.
  - Portal roadmap.
  - The mechanisms of nerd-dictation, Numen, Speech Note, espanso, wtype, Clipboard Indicator and Smile.
  - EGO stance.
  - Whether a Mutter-RemoteDesktop chord flashes the indicator (strongly implied by remoteAccess.js).

## 7. Proposed field tests (VM or throwaway user, never the maintainer's session)
- **T1:** No indicator during `InsertText`.
- **T2:** Coverage matrix:
  - Apps: GNOME Text Editor, Firefox, Chromium (with and without `--enable-wayland-ime`), LibreOffice, Qt6 (`QT_IM_MODULE=ibus` vs unset), Ptyxis/Console, kitty/foot/Alacritty, an XWayland app.
  - Record: method used, and whether the text appeared.
- **T3:** Focus race after `paste_to_captured_window`: commit vs paste counts, and which `inputMethod` notifies fire.
- **T4:** Paste fallback with a Cyrillic layout, with Dvorak, and in a terminal.
- **T5:** Lock screen.
- **T6:** Minimal IBus engine prototype in a VM, only if T2 shows gaps.

## 8. Sources
**GNOME Shell 48.7 (read locally; upstream equivalents):**
- https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/48.7/js/ui/keyboard.js
- https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/48.7/js/misc/inputMethod.js
- https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/48.7/js/misc/ibusManager.js
- https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/48.7/js/ui/status/remoteAccess.js
- https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/48.7/js/misc/util.js
- https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/48.7/js/ui/sessionMode.js

**Mutter and Wayland:**
- https://gitlab.gnome.org/GNOME/mutter/-/blob/main/src/wayland/meta-wayland-text-input.c [I]
- https://wayland.app/protocols/text-input-unstable-v3 [I]

**Extensions (gjs.guide):**
- https://gjs.guide/extensions/review-guidelines/review-guidelines.html [I]
- https://gjs.guide/extensions/topics/session-modes.html [I]
- https://gjs.guide/extensions/upgrading/gnome-shell-49.html (and 50/51) [I]

**IBus:**
- https://github.com/ibus/ibus [I]
- https://ibus.github.io/docs/ibus-1.5/IBusEngine.html [I]
- https://github.com/PhilippeRo/IBus-Speech-To-Text [I]
- https://github.com/mike-fabian/ibus-typing-booster [I]

**GNOME Discourse:**
- https://discourse.gnome.org/t/proposal-desktop-wide-offline-speech-to-text-dictation-support/35858 [V]
- https://discourse.gnome.org/t/local-ai-models-as-shared-desktop-infrastructure/36000 [search hit only]

**Portals:**
- https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html [I]
- https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.InputCapture.html [I]
- https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Clipboard.html [I]

**Comparable apps:**
- EasySpeak [V]: https://github.com/ctsdownloads/easyspeak (`src/core/mediakeys.py`, `src/plugins/dictation/__init__.py`) and https://easyspeak.dev/latest/how-it-works/
- https://github.com/ideasman42/nerd-dictation [I]
- https://git.sr.ht/~geb/numen [I]
- https://git.sr.ht/~geb/dotool [I]
- https://github.com/mkiol/dsnote [I]
- https://github.com/atx/wtype [I]
- https://github.com/ReimuNotMoe/ydotool [I]
- https://github.com/keepassxreboot/keepassxc [I]
- https://github.com/Tudmotu/gnome-shell-extension-clipboard-indicator [I]
- https://github.com/mijorus/smile [I]

**GTK:**
- https://docs.gtk.org/gtk4/class.IMContext.html and https://docs.gtk.org/gtk4/running.html [V: no cross-app guidance]

**Repo:**
- `contrib/gnome-shell-extension/whisperaloud@fede/extension.js`
- `src/whisper_aloud/clipboard/{portal_paste_simulator,paste_selection,window_manager}.py`
- `src/whisper_aloud/service/daemon.py`
- `flatpak/org.fede.whisperaloud.yml`
- `docs/flatpak.md`


---

## Appendix A — Portals and comparable apps (supporting research, 2026-10-05)

Each claim is tagged **[V]** (I read the doc or source myself) or **[I]** (inferred).

### 1. Portals

**Does RemoteDesktop show the indicator for a keyboard-only session? Yes, on every GNOME version I checked, including main.**
- [V] In mutter, `meta_remote_desktop_session_start()` always calls `init_remote_access_handle()` and notifies the RemoteAccessController. It does this whether or not a screencast is attached. https://gitlab.gnome.org/GNOME/mutter/-/raw/main/src/backends/meta-remote-desktop-session.c
- [V] In gnome-shell `remoteAccess.js`, `ScreenSharingIndicator` adds every handle that is not `isRecording` and shows itself while any handle exists. The handle carries no device-type or "input-only" property, so the shell has no way to tell a keyboard-only session apart. The indicator also stays up for at least 5 s (`MIN_SHARED_INDICATOR_VISIBLE_TIME_US`) after the session ends. https://gitlab.gnome.org/GNOME/gnome-shell/-/raw/main/js/ui/status/remoteAccess.js
- [V] I found no MR that hides or softens the indicator for input-only sessions. I searched the gnome-shell and mutter MRs and issues. The closest is issue #8821, "RDP: Screen sharing indicator always present" (Nov 2025, open, no developer reply). https://gitlab.gnome.org/GNOME/gnome-shell/-/work_items/8821
- [V] The Handy portal PR documents the same behaviour: "The GNOME screen-sharing indicator appears during injection and may linger briefly after completion." https://github.com/cjpais/Handy/pull/689
- [V] The KeePassXC PR also notes that GNOME shows the orange remote-desktop icon while a session is active. https://github.com/keepassxreboot/keepassxc/pull/13359

**RemoteDesktop portal v2 basics** [V] https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.RemoteDesktop.html
- Device types are KEYBOARD=1, POINTER=2, TOUCHSCREEN=4.
- `persist_mode` can be 0, 1 or 2 (2 = until revoked). A `restore_token` is single-use, so you must save the new one each time.
- The docs recommend EIS via `ConnectToEIS`. Once EIS is connected, the `Notify*` methods must not be used.
- [V] xdg-desktop-portal-gnome 48 implements `persist_mode`/restore data and a "remote clipboard" switch in the dialog. https://gitlab.gnome.org/GNOME/xdg-desktop-portal-gnome/-/raw/gnome-48/src/remotedesktop.c
- [I] A valid token restores the session without showing the dialog again. Emote and KeePassXC both rely on this.

**libei text events (new)**
- [V] libei 1.6 (May 2026) added an `ei_text` capability with keysym and UTF-8 events. A client can send the string "Hello World" and the compositor maps it to keysyms. http://who-t.blogspot.com/2026/07/libei-and-keysymtext-events.html
- [V] Mutter support is MR !4996, "eis: Handle libei keysym and utf8 text events" by Peter Hutterer. It is still open: it was a draft waiting on the libei release, and Garnacho and Ådahl were asked to review. https://gitlab.gnome.org/GNOME/mutter/-/merge_requests/4996
- [I] This would let text be typed directly instead of via Ctrl+V, but it still runs through a RemoteDesktop session, so it does not remove the indicator.

**InputCapture is capture-only** [V]
- The docs say it "allows capturing input events from connected physical or logical devices"; events go to the app. It cannot inject input. https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.InputCapture.html

**The Clipboard portal is tied to a session** [V]
- It "extends sessions created by other portals". `RequestClipboard` must be called before the session starts, and "Currently, the Remote Desktop and Input Capture portals support clipboard integration." https://flatpak.github.io/xdg-desktop-portal/docs/doc-org.freedesktop.portal.Clipboard.html
- So using it to avoid wl-copy still means opening a RemoteDesktop session, which shows the indicator.

**Is a text-insertion or dictation portal proposed? No.**
- [V] I searched the flatpak/xdg-desktop-portal issues for "virtual keyboard", "type text", "input method", "dictation", "autotype", "auto-type", "text input", "speech" and "keepassxc". The only related items are:
  - #1690, "Introduce Speech portal" (open since April 2025). It covers text-to-speech only; recognition is "in the future". Reviews from swick and jadahl ask for explicit user action to enable a speech provider. https://github.com/flatpak/xdg-desktop-portal/pull/1690
  - #2112, "Add local model portals" (Language/Speech/Vision). It was a draft and was closed unmerged with no maintainer review. https://github.com/flatpak/xdg-desktop-portal/pull/2112
  - #565, "Add UI automation portal" (open since 2021, no maintainer guidance). https://github.com/flatpak/xdg-desktop-portal/issues/565
  - #2009/#2010, a keyboard-layout option for RemoteDesktop (closed May 2026). https://github.com/flatpak/xdg-desktop-portal/pull/2010
- [V] A GNOME Discourse thread, "Proposal: desktop-wide offline speech-to-text / dictation support" (June 2026), pointed at ibus-speech-to-text, Speed of Sound and Myna. No GNOME core developers replied. https://discourse.gnome.org/t/proposal-desktop-wide-offline-speech-to-text-dictation-support/35858

**What GNOME and portal developers recommend**
- [V] I found no explicit statement from Ådahl, Garnacho, Stavracas or Wick recommending a mechanism for "typing into other apps". What the record does show:
  - Hutterer's libei text work is meant to make "a bunch of utilities a lot simpler to write". http://who-t.blogspot.com/2026/07/libei-and-keysymtext-events.html
  - The portal docs steer clients to EIS (link above).
  - Fedora 42 shipped ibus-speech-to-text, a dictation engine built as an input method. https://fedoraproject.org/wiki/Changes/ibus-speech-to-text
- [I] The sanctioned paths are therefore (a) RemoteDesktop + libei, which brings the indicator, or (b) an IBus input-method engine. Mutter implements `text-input-v3` and gnome-shell bridges it to IBus, so committed text reaches GTK, Qt and Electron apps with no remote-control indicator.
- [V] Mutter's Wayland protocol list includes `text-input` v3 but neither `virtual-keyboard-unstable-v1` nor `input-method-v2`. https://gitlab.gnome.org/GNOME/mutter/-/raw/main/src/meson.build

### 2. How comparable apps do it

- **KeePassXC 2.8** [V]
  - Uses the RemoteDesktop portal with `NotifyKeyboardKeysym`, plus GlobalShortcuts. It has restore-token persistence (never / until exit / until revoked), an optional "keep remote desktop connection open", and an experimental "use remote desktop for clipboard" option.
  - It has no focus tracking on Wayland. Their docs list "GNOME 51+, supported since 48".
  - Merged in PR #13359 on 2026-06-20. They earlier tried libei but hit invalid-keymap-fd problems (PR #10905).
  - https://raw.githubusercontent.com/keepassxreboot/keepassxc/develop/docs/topics/AutoType.adoc, https://github.com/keepassxreboot/keepassxc/pull/13359, https://github.com/keepassxreboot/keepassxc/pull/10905
- **Emote** — this is the most relevant pattern for us. [V]
  - Uses RemoteDesktop (KEYBOARD only) + libei (`ConnectToEIS` through python libei) with `persist_mode=UNTIL_REVOKED`. It saves the new restore token on every session.
  - On GNOME it opens a fresh restored session for each paste and closes it afterwards, so the indicator flashes rather than staying on. Only on KDE does it keep the session open, because KDE notifies on every new session.
  - Its README says it releases control after each paste on non-KDE desktops, and tells GNOME users to allow "Remote Interaction".
  - https://github.com/tom-james-watson/Emote (README; `emote/wayland_paste.py`)
- **Speed of Sound** (Flathub)
  - [V] Uses the RemoteDesktop portal (through the author's "Stargate" library), with clipboard output as a fallback. https://www.speedofsound.io/faq/, https://github.com/zugaldia/speedofsound
  - [I] Session lifetime and how the indicator behaves are not documented.
- **Handy (cjpais)** [V]
  - Uses wtype, dotool (needs the `input` group) or ydotool, falling back to enigo. Its README says wtype does not work on Ubuntu 26.04/GNOME. https://github.com/cjpais/Handy
  - A RemoteDesktop-portal PR (#689) is still unmerged. https://github.com/cjpais/Handy/pull/689
- **Speech Note / dsnote** [V]
  - "Insert into active window" on Wayland requires an external `ydotoold` daemon. Under Flatpak the user must grant access to its socket.
  - There are open problems with root-owned sockets under Flatpak.
  - https://github.com/mkiol/dsnote (README), https://github.com/mkiol/dsnote/issues/313
- **Numen** [V] Uses dotool, which writes to `/dev/uinput`. dotool ships a udev rule `KERNEL=="uinput", GROUP="input", MODE="0620"`. https://git.sr.ht/~geb/dotool
- **nerd-dictation** [V] Options are XDOTOOL (default, X11), DOTOOL, DOTOOLC, YDOTOOL and WTYPE. https://github.com/ideasman42/nerd-dictation
- **voxtype** [V] Tries wtype, then dotool, then ydotool, then clipboard. A "paste" mode copies and sends Ctrl+V. https://github.com/peteonrails/voxtype
- **hyprwhspr** [V]
  - Its docs say "Mutter blocks wtype". On GNOME it runs a private `ydotoold` on its own socket, rootless through the `uaccess` ACL on `/dev/uinput`, with the `input` group as a fallback.
  - It uses notifications instead of an overlay to avoid stealing focus.
  - https://github.com/goodroot/hyprwhspr/blob/main/docs/CONFIGURATION.md
- **Blurt** (GNOME extension) [V] Only writes to PRIMARY/clipboard; the user pastes with middle-click or Ctrl+V. Auto-paste uses xdotool, so it is X11 only. https://github.com/QuantiusBenignus/blurt
- **gnome-speech2text** [V] Auto-insert works on X11 only; on Wayland it is clipboard only. https://github.com/kavehtehrani/gnome-speech2text
- **Speech Panel extension** [V] Uses dotool. https://github.com/TheSkyNet/speech-to-text
- **ibus-speech-to-text** [V] An IBus engine using VOSK. Works on Wayland in any IBus-aware app. https://github.com/PhilippeRo/IBus-Speech-To-Text
- **Smile** — you were right about the companion extension. [V]
  - The app emits a D-Bus signal, `it.mijorus.smile.CopiedEmoji`.
  - The extension, running inside gnome-shell, creates a Clutter virtual keyboard (`seat.create_virtual_device(KEYBOARD_DEVICE)`) and sends Ctrl+V with `notify_keyval`.
  - Because it is in-shell, there is no portal and no remote-access handle, so no indicator. It needs the user to install the extension.
  - https://github.com/mijorus/smile-gnome-extension (`extension.js`), https://extensions.gnome.org/extension/6096/smile-complementary-extension/
- **espanso** [V] Needs `/dev/input/event*` and `/dev/uinput`. It recommends `setcap cap_dac_override+p` and drops the capability after opening the devices. Wayland support is "experimental", and GNOME shows a clipboard "flicker". https://espanso.org/docs/install/linux/
- **Talon** [V] Announced in May 2026 that it is dropping Linux/X11 and will not support Wayland, because it lacks compositor integration. https://www.osnews.com/story/145162/accessibility-input-tool-removes-x11-support-doesnt-want-to-support-wayland-users-caught-in-the-middle/
- **ydotool** [V] `ydotoold` has been mandatory since v1.0 and needs `/dev/uinput`, which "usually requires root". https://github.com/ReimuNotMoe/ydotool
- **wtype** [V] Uses `zwp_virtual_keyboard_v1`, which mutter does not implement (see the mutter protocol list above). https://github.com/atx/wtype

### Summary table

| App | Mechanism | Works on GNOME Wayland? | Permission needed |
|---|---|---|---|
| KeePassXC 2.8 | RemoteDesktop `NotifyKeyboardKeysym` + GlobalShortcuts | Yes (indicator shows) | Portal dialog, restore token |
| Emote | RemoteDesktop + libei; Ctrl+V; new session per paste | Yes (indicator flashes about 5 s) | "Remote Interaction" once, restore token |
| Speed of Sound | RemoteDesktop portal; clipboard fallback | Yes (indicator shows) [I on lifetime] | Portal dialog |
| Smile | In-shell Clutter virtual keyboard sends Ctrl+V | Yes, no indicator | User installs a shell extension |
| ibus-speech-to-text | IBus engine `commit_text` via text-input-v3 | Yes, no indicator | IBus engine installed on the host and selected as an input source |
| Speech Note | clipboard + ydotool | Yes, with setup | ydotoold with root/uinput; Flatpak socket access |
| Numen / dotool | uinput | Yes | `input` group + udev rule |
| nerd-dictation | xdotool/ydotool/dotool/wtype | Yes with dotool or ydotool; no with wtype | uinput access |
| Handy | wtype/dotool/ydotool/enigo | Partial (wtype fails) | `input` group or ydotoold |
| voxtype / hyprwhspr | wtype, falling back to ydotool/dotool or clipboard | Yes via ydotool | uinput (uaccess ACL or `input` group) |
| espanso | evdev + uinput | Yes, experimental | `cap_dac_override` |
| Blurt / speech2text extensions | clipboard only (xdotool on X11) | Copy only | None |
| wtype | `zwp_virtual_keyboard_v1` | No | n/a |
| Talon | X11 only | No | n/a |

### What this means for WhisperAloud
1. [V] The permanent indicator comes from keeping one RemoteDesktop session open. No version of GNOME distinguishes keyboard-only sessions, and no upstream change is pending.
2. [I] Emote's approach is the quickest mitigation: `persist_mode=2`, save the restore token every time, open a session for each paste and close it immediately. The indicator then only flashes for about 5 s, with no dialog after the first grant.
3. [I] For no indicator at all there are two options:
   - An IBus engine (the GNOME-native input-method path).
   - A small companion shell extension like Smile's that sends Ctrl+V or `commit_text` from inside the shell.
   - uinput tools (ydotool, dotool) also avoid the indicator but need host-level permissions that don't fit a Flatpak.
4. [I] libei `ei_text` (mutter !4996, unmerged) would allow direct UTF-8 typing without the clipboard, but still through a RemoteDesktop session.


---

## Appendix B — IBus input-method route (supporting research, 2026-10-05)

**Disclosure of what I touched under ~/.config/ibus.** I did this before the safety boundary arrived. I ran `ls ~/.config/ibus/bus/`, which showed two files: `<machine-id>-unix-0` and `-unix-wayland-0`. I also read the `-unix-wayland-0` file, which contains `IBUS_ADDRESS=unix:path=/home/fede/.cache/ibus/dbus-…` and `IBUS_DAEMON_PID`; I removed the guid from my output. I never connected to the IBus bus or GNOME Shell. I ran no ibus, gsettings or systemctl commands. Everything else was GitHub/GitLab source, web pages, and static files under /usr/share, /usr/lib and /usr/libexec. Source copies are in the session scratchpad.

### Bottom line
The best model to copy is Canonical's Myna, which ships in Ubuntu 26.10. It is a plain IBus engine with no component XML file:
1. It connects to the IBus private bus.
2. It calls `RegisterComponent` while running.
3. For each dictation it calls `SetGlobalEngine("myna-stt")`, commits text with `CommitText`, then restores the engine it recorded from `GetGlobalEngine`.

The catch for us: **an IBus engine cannot be shipped as a normal Flatpak.** Flatpak and IBus upstream both refuse this. Myna works because snap's `desktop-legacy` plug grants access to the IBus socket.

### 1. How existing dictation engines insert text

- **Myna (Canonical)** [VERIFIED]
  - Text goes in through an IBus engine. The engine name is `myna-stt` and its layout is `us`. It is registered at runtime with `RegisterComponent`, made active with `SetGlobalEngine`, and the previous engine is restored afterwards. Engine-to-app text uses `CommitText`, and streaming partial results use `UpdatePreeditText`. https://github.com/canonical/myna/blob/main/client/myna-desktop/src/inject/ibus.rs
  - It finds the bus address from `$IBUS_ADDRESS` or `~/.config/ibus/bus/`. Its snap uses the `desktop-legacy` plug "the IBus daemon's private socket". https://github.com/canonical/myna/blob/main/myna-snap/snap/snapcraft.yaml
  - Design rules: refuse password fields (content-type purpose PASSWORD/PIN or hint HIDDEN_TEXT). Drop buffered text if focus moves. Hold a per-utterance "lease" tied to `FocusInId`. They also note "IBus offers no way to address a commit to one context". https://github.com/canonical/myna/blob/main/client/.kb/desktop-integration.md
  - That doc argues for an input method over libei or RemoteDesktop: keycodes depend on the keyboard layout, modifier keys can race, CJK input methods intercept fake keys, and only the IM path supports preedit. It adds: "There is no cross-desktop text-commit portal yet."
  - Activation is a GNOME custom shortcut. They tried the GlobalShortcuts portal and dropped it in 2026-10. Their UI overlay (HUD) is a GNOME Shell extension. https://github.com/canonical/myna/blob/main/.kb/system-architecture.md
- **GNOME Shell does the same engine swap itself** [VERIFIED]. `setCompletionEnabled()` saves `_preOskEngine`, switches to `typing-booster`, then restores. https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/main/js/misc/ibusManager.js
- **IBus-Speech-To-Text (PhilippeRo)** uses Vosk, Python and Meson, and installs to `/usr`. You activate it from the IBus menu or Win+Space. https://github.com/PhilippeRo/IBus-Speech-To-Text [VERIFIED from the README]. Forks add whisper.cpp: https://github.com/Manish7093/IBus-Speech-To-Text [VERIFIED title only].
- **voice-typing-linux** uses `commit_text`, preedit, and `do_process_key_event` returning False, so normal keys pass through. Its README tells users to copy the XML to `~/.local/share/ibus/component`. https://github.com/GitJuhb/voice-typing-linux [VERIFIED from the README]. **That directory is not scanned by ibus** (see the next bullet).
- **Component directories** [VERIFIED]. `ibus_registry_load` reads `$IBUS_COMPONENT_PATH`, which *replaces* the default, or else only `IBUS_DATA_DIR/component`. The user-directory code sits inside `#if 0 /* FIXME Should we support install some IME in user dir? */`. https://github.com/ibus/ibus/blob/main/src/ibusregistry.c (lines ~262-281; it moved there from bus/registry.c). For us, runtime `RegisterComponent` is the way to avoid installing a file: https://github.com/ibus/ibus/blob/main/bus/ibusimpl.c (`_ibus_register_component`; the component is destroyed along with its factory).
- **ibus-typing-booster** has supported Google Cloud Speech-to-Text, which is paid and not free software. The maintainer said in 2022 that it was untested and looked at Vosk and Whisper, but I found no Whisper integration shipped. https://github.com/mike-fabian/ibus-typing-booster/discussions/394 [VERIFIED]. Whether the Google code is still there today is [INFERRED].
- **Speed of Sound** (Flathub) does *not* use IBus. It uses the RemoteDesktop portal through its Stargate library. https://github.com/zugaldia/speedofsound/blob/main/docs/faq.md [VERIFIED]
- **Flatpak limits** [VERIFIED]
  - ibus-portal only exposes `CreateInputContext` plus methods on the caller's own InputContext, and it rejects any caller other than that context's owner. It is a client-side API only. https://github.com/ibus/ibus/blob/main/portal/org.freedesktop.IBus.Portal.xml and https://github.com/ibus/ibus/blob/main/portal/portal.c
  - The "Packaging IBus engines with flatpak" issue was closed as not_planned. fujiwarat (IBus): "There is no reason to run ibus-daemon in host and engines in sandbox." TingPing: "this is just how ibus is designed." https://github.com/flatpak/flatpak/issues/3019
  - The bus is a private socket under `$XDG_CACHE_HOME/ibus` (`IBUS_SOCKET_DIR` in https://github.com/ibus/ibus/blob/main/configure.ac), and the server checks peer credentials (https://github.com/ibus/ibus/blob/main/bus/server.c).
  - [INFERRED] A Flatpak could still reach it with `--filesystem=~/.config/ibus/bus:ro` and `--filesystem=~/.cache/ibus`, because Flatpak remaps XDG_CACHE_HOME. Flathub reviewers would likely object. A cleaner split is a small host-side engine helper (deb or system package) that the Flatpak'd daemon talks to over session D-Bus.

### 2. Activation on GNOME and living alongside other engines

- **GNOME always routes Wayland text-input through IBus, even for users with only xkb layouts** [VERIFIED]
  - Shell's `InputMethod` (a Clutter.InputMethod) creates one IBus context named `'gnome-shell'` for all apps and forwards `commit-text` to `this.commit()`, which reaches clients over text-input-v3. https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/main/js/misc/inputMethod.js
  - A plain xkb input source is mapped to the IBus engine `xkb:<layout>:<variant>:<lang>`. https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/main/js/ui/status/keyboard.js (`activateInputSource`)
- **Temporary engine swap (Myna's approach)** [INFERRED]. It needs no change to the user's input sources. Shell only records an outside `global-engine-changed` (`_engineChanged`). Mozc or Pinyin users get their engine back when dictation ends, though their composition state is interrupted. If the user switches source, or has per-window sources and changes window, during dictation, Shell calls `setEngine` and our engine drops out.
- **Permanent "voice" input source with layout `default`** [VERIFIED/INFERRED]. `keyboardManager._doApply(id)` returns early when the layout id is unknown (`if (!info) return;`), so the previous keymap stays [VERIFIED]: https://gitlab.gnome.org/GNOME/gnome-shell/-/blob/main/js/misc/keyboardManager.js. Typing still works if the engine returns False from process_key_event. However, only one engine is active at a time, so a mozc or pinyin user cannot have both [INFERRED].
- **Committing regardless of the active engine (panel and extension path)** [VERIFIED in source, untested, a hack]
  - GNOME starts ibus with `--panel disable` and adds `--xim` only on X11. Sources: `/usr/lib/systemd/user/org.freedesktop.IBus.session.GNOME.service` and the `_spawn` function in ibusManager.js.
  - `--emoji-extension` defaults to "default", so ibus-extension-gtk3 still runs and owns `org.freedesktop.IBus.Panel.Extension.Emoji` (https://github.com/ibus/ibus/blob/main/bus/main.c).
  - In `bus_panel_proxy_commit_text`, a `CommitText` signal from the owner of that name, or of `org.freedesktop.IBus.Panel` (GNOME Shell here), goes to `panel->focused_context` with no check on which engine is active (https://github.com/ibus/ibus/blob/main/bus/panelproxy.c, ~L747).
  - When the name changes owner, the daemon immediately sends `FocusIn` for the current context (ibusimpl.c ~L597).
  - ibus-extension-gtk3 requests the name with `ALLOW_REPLACEMENT|REPLACE_EXISTING` (https://github.com/ibus/ibus/blob/main/ui/gtk3/extension.vala), so another process *could* take it over with REPLACE_EXISTING and send `IBus.PanelService.commit_text` (that method is in `/usr/share/gir-1.0/IBus-1.0.gir`).
  - Downsides: on losing the name, the extension destroys its panel, so the emoji picker probably breaks until ibus restarts [INFERRED]. It is a private interface, and it still needs host access to the bus socket. **Not recommended.** It is worth a sandboxed field test (nested `ibus-daemon`, never the live one).
  - The panel itself is GNOME Shell, so a Shell extension calling `Main.inputMethod.commit(text)` skips IBus altogether [INFERRED from inputMethod.js]. Myna's lease docs also mention a "fake" context when no context has focus.

### 3. Native GNOME dictation and protocols
- **GNOME has no native dictation API.** The GNOME 51 release notes (2026-09-16) say nothing about dictation, speech or input methods: https://release.gnome.org/51/ [VERIFIED]. GitLab searches for "dictation" and "speech" in gnome-shell and mutter found no dictation feature work [VERIFIED search result, so absence is INFERRED]: https://gitlab.gnome.org/api/v4/projects/GNOME%2Fgnome-shell/issues?search=dictation
- A community proposal from June 2026 drew replies that pointed to Myna, Speed of Sound and ibus-speech-to-text. I found no maintainer commitment: https://discourse.gnome.org/t/proposal-desktop-wide-offline-speech-to-text-dictation-support/35858 [VERIFIED]
- **Mutter does not implement input-method-v2.** Mutter main's `src/meson.build` lists only `['text-input','unstable',3]` and `meta-wayland-text-input.c`: https://gitlab.gnome.org/GNOME/mutter/-/blob/main/src/meson.build [VERIFIED]. wayland.app's "Mutter 51" claim for input-method-v2 is wrong.
- There is no text-commit portal. A vocalinux issue from Aug 2026 notes that RemoteDesktop `NotifyKeyboardKeysym` is the only portal path and does not solve layout mapping: https://github.com/VocaHQ/vocalinux/issues/750 [VERIFIED]. I found nothing that adds speech-to-text to Spiel or speech-dispatcher, both of which are text-to-speech only [INFERRED].
- I found no GUADEC 2025/2026 talk on dictation [INFERRED].

### 4. Pitfalls by toolkit and app
- **GTK3 and GTK4** use text-input-v3 natively. **Qt** supports text-input-v3 from 6.7, with fixes from 6.8.2; older Qt needs `QT_IM_MODULE=ibus`, because Mutter has no text-input-v2. https://fcitx-im.org/wiki/Using_Fcitx_5_on_Wayland [VERIFIED]
- **Chromium/Electron on native Wayland** needs `--enable-wayland-ime --wayland-text-input-version=3` (experimental since M129) or `--gtk-version=4`. https://wiki.archlinux.org/title/Chromium and the fcitx wiki above [VERIFIED as documented]
- **XWayland apps**: GNOME Wayland starts ibus **without `--xim`** (the systemd unit) [VERIFIED]. GTK and Qt apps under XWayland still reach IBus over D-Bus through IM modules. Pure XIM clients (xterm, old Java) will probably get nothing [INFERRED].
- **Firefox** goes through GTK3 to text-input-v3 [INFERRED].
- **VTE terminals** (GNOME Terminal, Console, Ptyxis) go through GTK, so they work [INFERRED].
- **foot** implemented text-input since Dec 2020: https://codeberg.org/dnkl/foot/issues/134 [VERIFIED].
- **WezTerm** has Wayland IME on by default since 20220807 and requires `zwp_text_input_v3`: https://wezterm.org/config/lua/config/use_ime.html [VERIFIED].
- **kitty** ships `glfw/wl_text_input.c` (text-input) and `ibus_glfw.c` (X11): https://github.com/kovidgoyal/kitty/tree/master/glfw [VERIFIED].
- **Alacritty** uses winit's text-input-v3 [INFERRED].
- **General caveats** [INFERRED, supported by Myna's lease docs]:
  - Text committed with no focused text field is dropped.
  - Password fields must be skipped.
  - Because GNOME uses one context for all apps, telling apps apart relies on Shell's FocusOut events.

### Other references
- Myna announcement coverage: https://www.theregister.com/os-platforms/2026/06/18/canonical-unveils-myna-speech-to-text-app-for-ubuntu-2610/5258376 and https://www.omgubuntu.co.uk/2026/06/what-is-myna-ubuntu-ai-dictation
- Handy's tracker on Flatpak, RemoteDesktop and GlobalShortcuts problems: https://github.com/cjpais/Handy/issues/1555
