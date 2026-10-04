// WhisperAloud top-bar indicator for GNOME Shell 45+ (ESM extensions).
//
// Optional companion to the WhisperAloud daemon (native or Flatpak). It:
//   - shows at a glance whether the daemon runs, the model is ready and the
//     global hotkey is registered, and the recording/transcribing state;
//   - lets you toggle recording and open the window from the top bar;
//   - exports org.fede.whisperaloud.Window (focused-window capture and
//     re-activation, used by the daemon's "gnome-extension" WindowManager
//     backend) and org.fede.whisperaloud.ShellExtension (version info, used
//     by the app's Settings) under the bus name EXT_BUS_NAME.
//
// Event-driven only: the daemon's presence is tracked with a bus-name watch
// and its state with StatusChanged / ModelStateChanged / ConfigChanged. No
// polling timers.

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
    ICONS, IndicatorState, applyModelState, deriveState, hotkeyLabel, modelLabel,
    needsFallbackKeybinding, statusLabel,
} from './status.js';

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
        } else if (signal === 'ConfigChanged') {
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
                    if (!e.matches?.(Gio.IOErrorEnum, Gio.IOErrorEnum.CANCELLED))
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
        const state = deriveState({
            running: this._running, starting: this._starting, status: this._status,
        });
        const label = statusLabel(state, this._status);

        this._icon.icon_name = ICONS[state] ?? ICONS[IndicatorState.READY];
        if (state === IndicatorState.RECORDING)
            this._icon.add_style_class_name('whisperaloud-recording');
        else
            this._icon.remove_style_class_name('whisperaloud-recording');
        this.accessible_name = `WhisperAloud: ${label}`;

        this._statusItem.label.text = label;
        const model = modelLabel(this._status);
        this._modelItem.label.text = model;
        this._modelItem.visible = Boolean(model);
        const hotkey = hotkeyLabel(this._status);
        this._hotkeyItem.label.text = hotkey;
        this._hotkeyItem.visible = Boolean(hotkey);

        this._toggleItem.visible = this._running;
        this._startItem.visible = !this._running && !this._starting;

        this._syncFallbackKeybinding();
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
  </interface>
  <interface name="org.fede.whisperaloud.ShellExtension">
    <method name="GetInfo">
      <arg direction="out" type="a{sv}" name="info"/>
    </method>
  </interface>
</node>`;

class ExtensionDBusService {
    constructor(metadata) {
        this._metadata = metadata;
        this._windowImpl = null;
        this._infoImpl = null;
        this._nameId = 0;
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
        this._nameId = Gio.bus_own_name(
            Gio.BusType.SESSION, EXT_BUS_NAME, Gio.BusNameOwnerFlags.NONE,
            null, null, null);
    }

    disable() {
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = 0;
        }
        this._windowImpl?.unexport();
        this._windowImpl = null;
        this._infoImpl?.unexport();
        this._infoImpl = null;
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
