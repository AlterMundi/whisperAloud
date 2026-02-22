import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import St from 'gi://St';
import Shell from 'gi://Shell';
import Meta from 'gi://Meta';

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

class WhisperAloudIndicator extends PanelMenu.Button {
    static [GObject.registerClassSymbol()]() {
        return { GTypeName: 'WhisperAloudIndicator' };
    }

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
        Main.wm.addKeybinding(
            'toggle-recording',
            this._settings,
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            () => this._toggleRecording()
        );
    }

    removeKeybinding() {
        Main.wm.removeKeybinding('toggle-recording');
    }

    // ── Cleanup ────────────────────────────────────────────────────────────

    destroy() {
        if (this._proxy && this._signalId !== null) {
            this._proxy.disconnect(this._signalId);
            this._signalId = null;
        }
        super.destroy();
    }
}

export default class WhisperAloudExtension extends Extension {
    enable() {
        this._indicator = new WhisperAloudIndicator(this.getSettings());
        Main.panel.addToStatusArea('whisperaloud', this._indicator);
    }

    disable() {
        if (this._indicator) {
            this._indicator.removeKeybinding();
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}
