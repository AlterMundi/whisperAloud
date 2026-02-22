import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Shell from 'gi://Shell';
import Meta from 'gi://Meta';
import GLib from 'gi://GLib';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import { Extension } from 'resource:///org/gnome/shell/extensions/extension.js';

const BUS_NAME    = 'org.fede.whisperaloud';
const OBJECT_PATH = '/org/fede/whisperaloud';
const INTERFACE   = 'org.fede.whisperaloud.Control';

const ICONS = {
    idle:         'audio-input-microphone-symbolic',
    recording:    'media-record-symbolic',
    transcribing: 'system-run-symbolic',
    error:        'dialog-error-symbolic',
};

const LABELS = {
    idle:         'WhisperAloud Ready',
    recording:    'Recording...',
    transcribing: 'Transcribing...',
    error:        'Error',
};

const WhisperAloudIndicator = GObject.registerClass(
class WhisperAloudIndicator extends PanelMenu.Button {
    _init(settings) {
        super._init(0.0, 'WhisperAloud');
        this._settings = settings;
        this._proxy    = null;
        this._signalId = null;

        this._icon = new St.Icon({
            icon_name:   ICONS.idle,
            style_class: 'system-status-icon',
        });
        this.add_child(this._icon);

        this._buildMenu();
        this._addKeybinding();
        this._connectToDaemon();
    }

    // ── D-Bus ──────────────────────────────────────────────────────────────

    _connectToDaemon() {
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.DO_NOT_AUTO_START,
            null,
            BUS_NAME, OBJECT_PATH, INTERFACE,
            null,
            (_source, res) => {
                try {
                    this._proxy = Gio.DBusProxy.new_for_bus_finish(res);
                    this._signalId = this._proxy.connect(
                        'g-signal',
                        (_proxy, _sender, signal_name, params) => {
                            if (signal_name === 'StatusChanged') {
                                const state = params.get_child_value(0).get_string();
                                this._updateStatus(state);
                            }
                        }
                    );
                    this._fetchStatus();
                } catch (e) {
                    console.error(`WhisperAloud: proxy failed: ${e}`);
                }
            }
        );
    }

    _fetchStatus() {
        if (!this._proxy) return;
        this._proxy.call(
            'GetStatus', null,
            Gio.DBusCallFlags.NONE, -1, null,
            (proxy, res) => {
                try {
                    const result = proxy.call_finish(res);
                    // GetStatus returns (a{sv}) — look up 'state' key
                    const dict   = result.get_child_value(0);
                    const stateV = dict.lookup_value('state', null);
                    const state  = stateV ? stateV.get_string() : 'idle';
                    this._updateStatus(state);
                } catch (_e) {
                    // daemon not running yet — leave icon at idle
                }
            }
        );
    }

    _toggleRecording() {
        if (!this._proxy) {
            this._connectToDaemon();
            return;
        }
        this._proxy.call(
            'ToggleRecording', null,
            Gio.DBusCallFlags.NONE, -1, null,
            (proxy, res) => {
                try { proxy.call_finish(res); }
                catch (e) { console.error(`WhisperAloud: toggle failed: ${e}`); }
            }
        );
    }

    // ── UI ─────────────────────────────────────────────────────────────────

    _updateStatus(state) {
        this._icon.icon_name        = ICONS[state]  ?? ICONS.idle;
        this._statusItem.label.text = LABELS[state] ?? 'WhisperAloud';
    }

    _buildMenu() {
        this._statusItem = new PopupMenu.PopupMenuItem(LABELS.idle, { reactive: false });
        this.menu.addMenuItem(this._statusItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        const toggleItem = new PopupMenu.PopupMenuItem('Toggle Recording');
        toggleItem.connect('activate', () => this._toggleRecording());
        this.menu.addMenuItem(toggleItem);
    }

    // ── Keybinding ─────────────────────────────────────────────────────────

    _addKeybinding() {
        if (!this._settings) {
            console.warn('WhisperAloud: No settings available, keybinding not registered');
            return;
        }
        Main.wm.addKeybinding(
            'toggle-recording',
            this._settings,
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            () => this._toggleRecording()
        );
        this._hasKeybinding = true;
    }

    removeKeybinding() {
        if (this._hasKeybinding) {
            Main.wm.removeKeybinding('toggle-recording');
            this._hasKeybinding = false;
        }
    }

    // ── Cleanup ────────────────────────────────────────────────────────────

    destroy() {
        if (this._proxy && this._signalId !== null) {
            this._proxy.disconnect(this._signalId);
            this._signalId = null;
        }
        super.destroy();
    }
});

// ── Window D-Bus service ────────────────────────────────────────────
const WINDOW_IFACE_XML = `
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
</node>`;

// GNOME 45+ ESM: wrapJSObject requires Gio.DBusInterfaceInfo, not raw XML
const WINDOW_NODE  = Gio.DBusNodeInfo.new_for_xml(WINDOW_IFACE_XML);
const WINDOW_IFACE = WINDOW_NODE.interfaces[0];

class WindowDBusService {
    constructor() {
        this._impl   = null;
        this._nameId = null;
    }

    enable() {
        this._impl = Gio.DBusExportedObject.wrapJSObject(WINDOW_IFACE, this);
        this._impl.export(Gio.DBus.session, '/org/fede/whisperaloud/Window');
        this._nameId = Gio.bus_own_name(
            Gio.BusType.SESSION,
            'org.gnome.Shell.Extensions.whisperaloud',
            Gio.BusNameOwnerFlags.NONE,
            null, null, null,
        );
    }

    disable() {
        if (this._impl) {
            this._impl.unexport();
            this._impl = null;
        }
        if (this._nameId) {
            Gio.bus_unown_name(this._nameId);
            this._nameId = null;
        }
    }

    GetFocusedWindow() {
        const win = global.display.get_focus_window();
        if (!win) return {};
        return {
            'id':       GLib.Variant.new_uint32(win.get_id()),
            'title':    GLib.Variant.new_string(win.get_title() || ''),
            'wm_class': GLib.Variant.new_string(win.get_wm_class() || ''),
        };
    }

    ActivateWindow(window_id) {
        const actors = global.get_window_actors();
        for (const actor of actors) {
            const win = actor.meta_window;
            if (win.get_id() === window_id) {
                // Use roundtrip timestamp to avoid focus-steal prevention
                win.activate(global.display.get_current_time_roundtrip());
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
            console.warn(`WhisperAloud: getSettings() failed: ${e}. Keybinding disabled.`);
        }
        this._indicator = new WhisperAloudIndicator(settings);
        Main.panel.addToStatusArea('whisperaloud', this._indicator);
        this._windowService = new WindowDBusService();
        this._windowService.enable();
    }

    disable() {
        if (this._windowService) {
            this._windowService.disable();
            this._windowService = null;
        }
        if (this._indicator) {
            this._indicator.removeKeybinding();
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}
