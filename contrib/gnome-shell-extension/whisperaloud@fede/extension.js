// WhisperAloud top-bar indicator for GNOME Shell 45+ (ESM extensions).
//
// Optional companion to the WhisperAloud daemon (native or Flatpak). It:
//   - shows at a glance whether the daemon runs, the model is ready and the
//     global hotkey is registered, and the recording/transcribing state;
//   - lets you toggle recording and open the window from the top bar;
//   - exports org.fede.whisperaloud.Window (focused-window capture and
//     re-activation, plus a FocusChanged signal the daemon uses to CONFIRM
//     a captured window actually regained focus -- WHISP-88 -- used by the
//     daemon's "gnome-extension" WindowManager backend) and
//     org.fede.whisperaloud.ShellExtension (version info, used by the
//     app's Settings) under the bus name EXT_BUS_NAME;
//   - exports org.fede.whisperaloud.TextInput (WHISP-119): the daemon's
//     native text insertion. InsertText commits the transcription through
//     Main.inputMethod (text-input-v3) or, when that is not possible, sets
//     the clipboard and sends a paste chord through a Clutter virtual
//     keyboard. No portal, so no "remote access" indicator. Only the
//     current owner of org.fede.whisperaloud may call it; it refuses while
//     the session is locked, a Shell modal is open, or a password field has
//     focus. The decisions live in insertion.js (Node-tested).
//
// Event-driven: the daemon's presence is tracked with a bus-name watch and
// its state with StatusChanged / ModelStateChanged / ConfigChanged /
// HotkeyStateChanged. No timers: hotkey_bound used to have no change
// signal, so this used to re-read GetStatus on a bounded backoff timer --
// API 1.8.0's HotkeyStateChanged (WHISP-99) replaced that entirely.

import Clutter from 'gi://Clutter';
import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import GLib from 'gi://GLib';
import St from 'gi://St';
import Shell from 'gi://Shell';
import Meta from 'gi://Meta';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import * as Config from 'resource:///org/gnome/shell/misc/config.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

import {
    ICONS, IndicatorState, STYLE_CLASSES, applyHotkeyState, applyModelState,
    autostartLabel, deriveState, hotkeyLabel, modelLabel, needsFallbackKeybinding,
    statusLabel,
} from './status.js';
import {Purpose, Reason, capabilities, chordEvents, decideInsertion} from './insertion.js';

// The daemon (see docs/dbus-api.md).
const BUS_NAME    = 'org.fede.whisperaloud';
const OBJECT_PATH = '/org/fede/whisperaloud';
const INTERFACE   = 'org.fede.whisperaloud.Control';
const DESKTOP_ID  = 'org.fede.whisperaloud.desktop';

// What this extension exports. The Flatpak manifest grants the daemon
// --talk-name for exactly EXT_BUS_NAME; keep them in sync.
const EXT_BUS_NAME     = 'org.gnome.Shell.Extensions.whisperaloud';
const WINDOW_PATH      = '/org/fede/whisperaloud/Window';
const EXT_INFO_PATH    = '/org/fede/whisperaloud/ShellExtension';
const WINDOW_IFACE     = 'org.fede.whisperaloud.Window';
const EXT_INFO_IFACE   = 'org.fede.whisperaloud.ShellExtension';
const TEXT_INPUT_PATH  = '/org/fede/whisperaloud/TextInput';
const TEXT_INPUT_IFACE = 'org.fede.whisperaloud.TextInput';

const KEYBINDING = 'toggle-recording';

const WhisperAloudIndicator = GObject.registerClass(
class WhisperAloudIndicator extends PanelMenu.Button {
    _init(settings) {
        super._init(0.0, 'WhisperAloud');
        this._settings = settings;
        this._running = false;
        this._starting = false;
        this._status = null;
        this._generation = 0;
        this._fallbackBound = false;
        this._cancellable = new Gio.Cancellable();

        this._icon = new St.Icon({
            icon_name: ICONS[IndicatorState.NOT_RUNNING],
            style_class: 'system-status-icon',
        });
        this.add_child(this._icon);

        this._buildMenu();
        this._subscribeSignals();
        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION, BUS_NAME, Gio.BusNameWatcherFlags.NONE,
            () => this._onDaemonAppeared(),
            () => this._onDaemonVanished());
        this._refreshUi();
    }

    // ── D-Bus: presence and signals ────────────────────────────────────────

    _subscribeSignals() {
        // Subscribing by well-known name: GDBus follows the owner, so this
        // survives daemon restarts without re-subscribing.
        this._signalId = Gio.DBus.session.signal_subscribe(
            BUS_NAME, INTERFACE, null, OBJECT_PATH, null,
            Gio.DBusSignalFlags.NONE,
            (_conn, _sender, _path, _iface, signal, params) =>
                this._onDaemonSignal(signal, params));
    }

    _onDaemonAppeared() {
        this._running = true;
        this._status = null;
        this._generation++;
        this._refreshUi();
        this._fetchStatus();
    }

    _onDaemonVanished() {
        this._running = false;
        this._status = null;
        this._generation++;
        this._refreshUi();
    }

    _onDaemonSignal(signal, params) {
        // The daemon claims its bus name before it exports the object, so the
        // GetStatus sent from _onDaemonAppeared can fail. Any signal proves the
        // object is exported now: fetch the full status if we still lack it.
        if (!this._status && this._running)
            this._fetchStatus();
        if (signal === 'StatusChanged') {
            const [state] = params.deepUnpack();
            if (this._status)
                this._status.state = state;
            this._refreshUi();
            // Back to idle: cheap one-shot refresh (hotkey/model may have changed).
            if (state === 'idle')
                this._fetchStatus();
        } else if (signal === 'ModelStateChanged') {
            const [state, detail] = params.deepUnpack();
            applyModelState(this._status, state, detail);
            this._refreshUi();
        } else if (signal === 'HotkeyStateChanged') {
            const [bound, trigger, backend] = params.deepUnpack();
            applyHotkeyState(this._status, bound, trigger, backend);
            this._refreshUi();
        } else if (signal === 'ConfigChanged') {
            // A hotkey config change re-registers; HotkeyStateChanged
            // reports the outcome, but GetStatus is also re-read here in
            // case something else in the config affected the status dict.
            this._fetchStatus();
        }
    }

    _call(method, {autoStart = false} = {}, onReply = null) {
        Gio.DBus.session.call(
            BUS_NAME, OBJECT_PATH, INTERFACE, method, null, null,
            autoStart ? Gio.DBusCallFlags.NONE : Gio.DBusCallFlags.NO_AUTO_START,
            -1, this._cancellable,
            (conn, res) => {
                let reply = null;
                try {
                    reply = conn.call_finish(res);
                } catch (e) {
                    // Cancelled: the indicator is being destroyed, touch nothing.
                    if (e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
                        return;
                    console.debug(`WhisperAloud: ${method} failed: ${e.message}`);
                }
                onReply?.(reply);
            });
    }

    _fetchStatus() {
        const generation = this._generation;
        this._call('GetStatus', {}, reply => {
            // Ignore replies that raced a vanish/re-appear.
            if (!reply || generation !== this._generation)
                return;
            // (a{sv}) -> plain object, nested variants included.
            [this._status] = reply.recursiveUnpack();
            this._refreshUi();
        });
    }

    _startDaemon() {
        // A method call without NO_AUTO_START makes the bus activate the
        // daemon through its D-Bus service file (native or Flatpak export).
        this._starting = true;
        this._refreshUi();
        this._call('GetStatus', {autoStart: true}, () => {
            this._starting = false;
            this._refreshUi();
        });
    }

    _toggleRecording() {
        if (this._running)
            this._call('ToggleRecording');
    }

    _openWindow() {
        const app = Shell.AppSystem.get_default().lookup_app(DESKTOP_ID);
        if (app)
            app.activate();
        else
            Main.notify('WhisperAloud', `Application ${DESKTOP_ID} is not installed`);
    }

    // ── UI ─────────────────────────────────────────────────────────────────

    _buildMenu() {
        this._statusItem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this.menu.addMenuItem(this._statusItem);
        this._modelItem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this.menu.addMenuItem(this._modelItem);
        this._hotkeyItem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this.menu.addMenuItem(this._hotkeyItem);
        this._autostartItem = new PopupMenu.PopupMenuItem('', {reactive: false});
        this.menu.addMenuItem(this._autostartItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        this._toggleItem = new PopupMenu.PopupMenuItem('Toggle Recording');
        this._toggleItem.connect('activate', () => this._toggleRecording());
        this.menu.addMenuItem(this._toggleItem);

        this._startItem = new PopupMenu.PopupMenuItem('Start WhisperAloud');
        this._startItem.connect('activate', () => this._startDaemon());
        this.menu.addMenuItem(this._startItem);

        const openItem = new PopupMenu.PopupMenuItem('Open WhisperAloud');
        openItem.connect('activate', () => this._openWindow());
        this.menu.addMenuItem(openItem);
    }

    _refreshUi() {
        // Fallback keybinding first: whether it is grabbed changes the state.
        this._syncFallbackKeybinding();

        const state = deriveState({
            running: this._running, starting: this._starting, status: this._status,
            fallbackHotkey: this._fallbackBound,
        });
        const label = statusLabel(state, this._status);

        this._icon.icon_name = ICONS[state] ?? ICONS[IndicatorState.READY];
        for (const cls of Object.values(STYLE_CLASSES))
            this._icon.remove_style_class_name(cls);
        if (STYLE_CLASSES[state])
            this._icon.add_style_class_name(STYLE_CLASSES[state]);
        this.accessible_name = `WhisperAloud: ${label}`;

        this._statusItem.label.text = label;
        this._setLine(this._modelItem, modelLabel(this._status));
        this._setLine(this._hotkeyItem, hotkeyLabel(this._status, this._fallbackAccel()));
        this._setLine(this._autostartItem, autostartLabel(this._status));

        this._toggleItem.visible = this._running;
        this._startItem.visible = !this._running && !this._starting;
    }

    _setLine(item, text) {
        item.label.text = text;
        item.visible = Boolean(text);
    }

    _fallbackAccel() {
        if (!this._fallbackBound)
            return '';
        return this._settings.get_strv(KEYBINDING)[0] ?? '';
    }

    // ── Fallback keybinding ────────────────────────────────────────────────
    //
    // The daemon owns the global hotkey (XDG GlobalShortcuts portal or
    // keybinder). The extension's own 'toggle-recording' keybinding is only
    // grabbed while the running daemon reports it has none, so the same
    // accelerator never fires twice.

    _syncFallbackKeybinding() {
        const wanted = Boolean(this._settings) &&
            needsFallbackKeybinding({running: this._running, status: this._status});
        if (wanted && !this._fallbackBound) {
            const action = Main.wm.addKeybinding(
                KEYBINDING, this._settings, Meta.KeyBindingFlags.NONE,
                Shell.ActionMode.ALL, () => this._toggleRecording());
            // addKeybinding returns Meta.KeyBindingAction.NONE (0) on failure.
            this._fallbackBound = action !== Meta.KeyBindingAction.NONE;
        } else if (!wanted && this._fallbackBound) {
            Main.wm.removeKeybinding(KEYBINDING);
            this._fallbackBound = false;
        }
    }

    // ── Cleanup ────────────────────────────────────────────────────────────

    destroy() {
        this._cancellable.cancel();
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
        if (this._signalId) {
            Gio.DBus.session.signal_unsubscribe(this._signalId);
            this._signalId = 0;
        }
        if (this._fallbackBound) {
            Main.wm.removeKeybinding(KEYBINDING);
            this._fallbackBound = false;
        }
        super.destroy();
    }
});

// ── Exported services ──────────────────────────────────────────────────────

const SERVICE_XML = `
<node>
  <interface name="org.fede.whisperaloud.Window">
    <method name="GetFocusedWindow">
      <arg direction="out" type="a{sv}" name="window"/>
    </method>
    <method name="ActivateWindow">
      <arg direction="in" type="u" name="window_id"/>
      <arg direction="out" type="b" name="success"/>
    </method>
    <signal name="FocusChanged">
      <arg type="s" name="window_id"/>
    </signal>
  </interface>
  <interface name="org.fede.whisperaloud.ShellExtension">
    <method name="GetInfo">
      <arg direction="out" type="a{sv}" name="info"/>
    </method>
  </interface>
  <interface name="org.fede.whisperaloud.TextInput">
    <!-- options: window_id u, mode s (auto|commit|paste),
         paste_shortcut s (auto|shift+insert|ctrl+shift+insert|ctrl+v|ctrl+shift+v).
         result: method s (commit|paste|none), reason s (see insertion.js Reason). -->
    <method name="InsertText">
      <arg direction="in" type="s" name="text"/>
      <arg direction="in" type="a{sv}" name="options"/>
      <arg direction="out" type="a{sv}" name="result"/>
    </method>
    <method name="GetCapabilities">
      <arg direction="out" type="a{sv}" name="capabilities"/>
    </method>
  </interface>
</node>`;

// ── Text insertion (WHISP-119) ────────────────────────────────────────────

const DAEMON_NAME = BUS_NAME;
const ACCESS_DENIED = 'org.freedesktop.DBus.Error.AccessDenied';
const FAILED = 'org.freedesktop.DBus.Error.Failed';

// Clutter.get_default_backend() is gone in GNOME 51; ClutterActor's
// `context` property only exists from 47. Feature-detect.
function clutterBackend() {
    const context = global.stage?.context;
    if (context?.get_backend)
        return context.get_backend();
    return Clutter.get_default_backend();
}

function unpackOption(value) {
    return value instanceof GLib.Variant ? value.recursiveUnpack() : value;
}

function contentPurpose(im) {
    const P = Clutter.InputContentPurpose;
    const purpose = im.contentPurpose;
    if (purpose === P.PASSWORD || (P.PIN !== undefined && purpose === P.PIN))
        return Purpose.PASSWORD;
    const hidden = Clutter.InputContentHintFlags?.HIDDEN_TEXT;
    if (hidden && (im.contentHints & hidden))
        return Purpose.PASSWORD;
    if (purpose === P.TERMINAL)
        return Purpose.TERMINAL;
    return Purpose.NORMAL;
}

class TextInserter {
    constructor(metadata) {
        this._version = Number(metadata.version ?? 0);
        this._keyboard = null;
        this._daemonOwner = null;
        this._watchId = 0;
    }

    enable() {
        const seat = clutterBackend().get_default_seat();
        this._keyboard = seat.create_virtual_device(Clutter.InputDeviceType.KEYBOARD_DEVICE);
        // Our own watcher instead of misc/util.js DBusSenderChecker, whose
        // destroy() leaks its watchers (it iterates array indices) and which
        // unsafe mode bypasses. The unique name of the current owner of
        // org.fede.whisperaloud is the only accepted caller.
        this._watchId = Gio.bus_watch_name(
            Gio.BusType.SESSION, DAEMON_NAME, Gio.BusNameWatcherFlags.NONE,
            (_conn, _name, owner) => {
                this._daemonOwner = owner;
            },
            () => {
                this._daemonOwner = null;
            });
    }

    disable() {
        if (this._watchId) {
            Gio.bus_unwatch_name(this._watchId);
            this._watchId = 0;
        }
        this._daemonOwner = null;
        // As keyboard.js does: dispose releases any key still held.
        this._keyboard?.run_dispose();
        this._keyboard = null;
    }

    GetCapabilities() {
        const caps = capabilities(this._version);
        return {
            'insert_text': GLib.Variant.new_boolean(caps.insert_text && this._keyboard !== null),
            'methods': new GLib.Variant('as', caps.methods),
            'max_commit_bytes': GLib.Variant.new_uint32(caps.max_commit_bytes),
            'reasons': new GLib.Variant('as', caps.reasons),
            'version': GLib.Variant.new_uint32(caps.version),
        };
    }

    InsertTextAsync(params, invocation) {
        if (!this._daemonOwner || invocation.get_sender() !== this._daemonOwner) {
            invocation.return_dbus_error(ACCESS_DENIED,
                'Only the WhisperAloud daemon may insert text');
            return;
        }
        let result;
        try {
            const [text, options] = params;
            result = this._insert(text, options ?? {});
        } catch (e) {
            // Never include the text in errors or logs.
            console.warn(`WhisperAloud: InsertText failed: ${e.message}`);
            invocation.return_dbus_error(FAILED, 'InsertText failed');
            return;
        }
        invocation.return_value(new GLib.Variant('(a{sv})', [{
            'method': GLib.Variant.new_string(result.method),
            'reason': GLib.Variant.new_string(result.reason),
        }]));
    }

    _insert(text, options) {
        const im = Main.inputMethod;
        const win = global.display.get_focus_window();
        const imFocus = Boolean(im.currentFocus);
        const windowId = unpackOption(options['window_id']);
        const decision = decideInsertion({
            text,
            locked: Main.sessionMode.isLocked,
            modalCount: Main.modalCount,
            focusWindowId: win ? win.get_id() : null,
            expectedWindowId: windowId === undefined ? undefined : Number(windowId),
            imFocus,
            hasPreedit: imFocus && typeof im.hasPreedit === 'function' && im.hasPreedit(),
            // Without IM focus the purpose is stale (it belongs to the last
            // focused field), so it is ignored.
            purpose: imFocus ? contentPurpose(im) : Purpose.NORMAL,
            mode: unpackOption(options['mode']) ?? 'auto',
            pasteShortcut: unpackOption(options['paste_shortcut']) ?? 'auto',
        });
        if (decision.method === 'commit') {
            for (const chunk of decision.chunks)
                im.commit(chunk);
        } else if (decision.method === 'paste') {
            if (!this._keyboard)
                return {method: 'none', reason: Reason.NO_VIRTUAL_KEYBOARD};
            St.Clipboard.get_default().set_text(St.ClipboardType.CLIPBOARD, text);
            this._sendChord(decision.chord);
        }
        return {method: decision.method, reason: decision.reason};
    }

    _sendChord(keys) {
        // Microseconds, as gnome-shell's keyboard.js keyvalPress() does.
        // Outside an input event this is CLUTTER_CURRENT_TIME (0), which
        // Mutter's virtual device replaces with "now".
        const time = Clutter.get_current_event_time() * 1000;
        for (const [key, pressed] of chordEvents(keys)) {
            this._keyboard.notify_keyval(time, Clutter[`KEY_${key}`],
                pressed ? Clutter.KeyState.PRESSED : Clutter.KeyState.RELEASED);
        }
    }
}

class ExtensionDBusService {
    constructor(metadata) {
        this._metadata = metadata;
        this._windowImpl = null;
        this._infoImpl = null;
        this._textInputImpl = null;
        this._inserter = new TextInserter(metadata);
        this._nameId = 0;
        this._focusHandlerId = 0;
    }

    enable() {
        // GNOME 45+ ESM: wrapJSObject needs Gio.DBusInterfaceInfo, not XML.
        const node = Gio.DBusNodeInfo.new_for_xml(SERVICE_XML);
        this._windowImpl = Gio.DBusExportedObject.wrapJSObject(
            node.lookup_interface(WINDOW_IFACE), this);
        this._windowImpl.export(Gio.DBus.session, WINDOW_PATH);
        this._infoImpl = Gio.DBusExportedObject.wrapJSObject(
            node.lookup_interface(EXT_INFO_IFACE), this);
        this._infoImpl.export(Gio.DBus.session, EXT_INFO_PATH);
        // Virtual keyboard and caller watch first, so InsertText is never
        // reachable without them.
        this._inserter.enable();
        this._textInputImpl = Gio.DBusExportedObject.wrapJSObject(
            node.lookup_interface(TEXT_INPUT_IFACE), this._inserter);
        this._textInputImpl.export(Gio.DBus.session, TEXT_INPUT_PATH);
        this._nameId = Gio.bus_own_name(
            Gio.BusType.SESSION, EXT_BUS_NAME, Gio.BusNameOwnerFlags.NONE,
            null, null, null);
        // WHISP-88: emit FocusChanged on every real focus-window change, so
        // the daemon's WindowManager can CONFIRM a captured window actually
        // regained focus (instead of assuming so after a fixed settle
        // delay following ActivateWindow). global.display's own
        // 'notify::focus-window' is the authoritative compositor event --
        // no polling.
        this._focusHandlerId = global.display.connect(
            'notify::focus-window', () => this._emitFocusChanged());
    }

    _emitFocusChanged() {
        const win = global.display.get_focus_window();
        const windowId = win ? String(win.get_id()) : '';
        this._windowImpl?.emit_signal('FocusChanged', GLib.Variant.new('(s)', [windowId]));
    }

    disable() {
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = 0;
        }
        if (this._focusHandlerId) {
            global.display.disconnect(this._focusHandlerId);
            this._focusHandlerId = 0;
        }
        this._windowImpl?.unexport();
        this._windowImpl = null;
        this._infoImpl?.unexport();
        this._infoImpl = null;
        this._textInputImpl?.unexport();
        this._textInputImpl = null;
        this._inserter.disable();
    }

    // org.fede.whisperaloud.ShellExtension
    GetInfo() {
        return {
            'uuid': GLib.Variant.new_string(this._metadata.uuid ?? ''),
            'version': GLib.Variant.new_uint32(Number(this._metadata.version ?? 0)),
            'shell_version': GLib.Variant.new_string(Config.PACKAGE_VERSION),
        };
    }

    // org.fede.whisperaloud.Window
    GetFocusedWindow() {
        const win = global.display.get_focus_window();
        if (!win)
            return {};
        return {
            'id': GLib.Variant.new_uint32(win.get_id()),
            'title': GLib.Variant.new_string(win.get_title() || ''),
            'wm_class': GLib.Variant.new_string(win.get_wm_class() || ''),
        };
    }

    ActivateWindow(windowId) {
        for (const actor of global.get_window_actors()) {
            const win = actor.meta_window;
            if (win.get_id() === windowId) {
                // Main.activateWindow also switches workspace if needed. A
                // D-Bus call has no current event, so pass a roundtrip
                // timestamp to avoid focus-steal prevention.
                Main.activateWindow(win, global.display.get_current_time_roundtrip());
                return true;
            }
        }
        return false;
    }
}

export default class WhisperAloudExtension extends Extension {
    enable() {
        let settings = null;
        try {
            settings = this.getSettings();
        } catch (e) {
            console.warn(`WhisperAloud: getSettings() failed: ${e}. Fallback keybinding disabled.`);
        }
        this._indicator = new WhisperAloudIndicator(settings);
        Main.panel.addToStatusArea(this.uuid, this._indicator);
        this._service = new ExtensionDBusService(this.metadata);
        this._service.enable();
    }

    disable() {
        this._service?.disable();
        this._service = null;
        this._indicator?.destroy();
        this._indicator = null;
    }
}
