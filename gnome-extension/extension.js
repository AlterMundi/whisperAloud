import GObject from 'gi://GObject';
import Gio from 'gi://Gio';
import St from 'gi://St';

import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import * as PanelMenu from 'resource:///org/gnome/shell/ui/panelMenu.js';
import * as PopupMenu from 'resource:///org/gnome/shell/ui/popupMenu.js';
import Shell from 'gi://Shell';
import Meta from 'gi://Meta';

import * as ExtensionUtils from 'resource:///org/gnome/shell/misc/extensionUtils.js';
const Me = ExtensionUtils.getCurrentExtension();

class WhisperAloudIndicator extends PanelMenu.Button {
    static [GObject.registerClassSymbol()]() {
        return {
            GTypeName: 'WhisperAloudIndicator',
        };
    }

    _init() {
        super._init(0.0, 'WhisperAloud');

        // Create icon
        this._icon = new St.Icon({
            icon_name: 'audio-input-microphone-symbolic',
            style_class: 'system-status-icon'
        });
        this.add_child(this._icon);

        // Create menu
        this._buildMenu();

        // Add keybinding
        this._addKeybinding();

        // Connect to daemon
        this._connectToDaemon();
    }

    _connectToDaemon() {
        Gio.DBusProxy.new_for_bus(
            Gio.BusType.SESSION,
            Gio.DBusProxyFlags.NONE,
            null,
            'org.fede.whisperAloud',
            '/org/fede/whisperAloud',
            'org.fede.whisperAloud.Control',
            null,
            (proxy, res) => {
                try {
                    this._proxy = Gio.DBusProxy.new_for_bus_finish(res);
                    
                    // Listen for signals
                    this._proxy.connect('g-signal', (proxy, sender_name, signal_name, parameters) => {
                        if (signal_name === 'StatusChanged') {
                            const status = parameters.get_child_value(0).get_string();
                            this._updateStatus(status);
                        }
                    });

                    // Get initial status
                    this._proxy.call(
                        'GetStatus',
                        null,
                        Gio.DBusCallFlags.NONE,
                        -1,
                        null,
                        (proxy, res) => {
                            try {
                                const result = proxy.call_finish(res);
                                const status = result.get_child_value(0).get_string();
                                this._updateStatus(status);
                            } catch (e) {
                                console.log('WhisperAloud: Daemon not ready yet');
                            }
                        }
                    );
                } catch (e) {
                    console.error('WhisperAloud: Failed to connect to daemon:', e);
                }
            }
        );
    }

    _updateStatus(status) {
        if (status === 'recording') {
            this._icon.icon_name = 'media-record-symbolic';
            this._statusItem.label.text = 'Recording...';
        } else if (status === 'transcribing') {
            this._icon.icon_name = 'system-run-symbolic';
            this._statusItem.label.text = 'Transcribing...';
        } else {
            this._icon.icon_name = 'audio-input-microphone-symbolic';
            this._statusItem.label.text = 'WhisperAloud Ready';
        }
    }

    _addKeybinding() {
        Main.wm.addKeybinding(
            'toggle-recording',
            Me.getSettings(),
            Meta.KeyBindingFlags.NONE,
            Shell.ActionMode.ALL,
            () => {
                this._toggleRecording();
            }
        );
    }

    _removeKeybinding() {
        Main.wm.removeKeybinding('toggle-recording');
    }

    _toggleRecording() {
        if (this._proxy) {
            this._proxy.call(
                'ToggleRecording',
                null,
                Gio.DBusCallFlags.NONE,
                -1,
                null,
                (proxy, res) => {
                    try {
                        proxy.call_finish(res);
                    } catch (e) {
                        console.error('WhisperAloud: Failed to toggle recording:', e);
                    }
                }
            );
        } else {
            console.log('WhisperAloud: Daemon not connected, trying to reconnect...');
            this._connectToDaemon();
        }
    }

    _buildMenu() {
        // Status item
        this._statusItem = new PopupMenu.PopupMenuItem('WhisperAloud Ready', {
            reactive: false
        });
        this.menu.addMenuItem(this._statusItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // Toggle item
        this._toggleItem = new PopupMenu.PopupMenuItem('Toggle Recording');
        this._toggleItem.connect('activate', () => {
            this._toggleRecording();
        });
        this.menu.addMenuItem(this._toggleItem);

        this.menu.addMenuItem(new PopupMenu.PopupSeparatorMenuItem());

        // Info
        let infoItem = new PopupMenu.PopupMenuItem('Start daemon: whisper-aloud --daemon', {
            reactive: false
        });
        this.menu.addMenuItem(infoItem);
    }
}

class Extension {
    constructor() {
        this._indicator = null;
    }

    enable() {
        this._indicator = new WhisperAloudIndicator();
        Main.panel.addToStatusArea('whisperaloud', this._indicator);
    }

    disable() {
        if (this._indicator) {
            this._indicator._removeKeybinding();
            this._indicator.destroy();
            this._indicator = null;
        }
    }
}

function init() {
    return new Extension();
}