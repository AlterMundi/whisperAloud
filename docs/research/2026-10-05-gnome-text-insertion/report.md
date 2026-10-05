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
