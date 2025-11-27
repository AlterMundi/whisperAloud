# Phase 7: GNOME Integration Plan

## Objective
Integrate `whisperAloud` seamlessly into the GNOME desktop environment, providing a native user experience with system tray indicators, notifications, and global shortcuts.

## Current State
- **Core**: Robust recording and transcription engine.
- **Service**: D-Bus daemon (`org.fede.whisperAloud`) is running and controllable.
- **GUI**: GTK4 application exists but runs standalone.
- **Missing**: System-level integration (tray icon, notifications, shortcuts).

## Proposed Architecture

### 1. GNOME Shell Extension (Optional but Recommended)
To provide a true "tray icon" or status indicator in modern GNOME (which deprecated legacy tray icons), a GNOME Shell Extension is the best approach.
- **Indicator**: A microphone icon in the top panel.
- **Menu**: Start/Stop, Settings, History, Quit.
- **Visual Feedback**: Change icon color/state when recording or transcribing.
- **Communication**: The extension talks to the Python daemon via D-Bus.

### 2. Desktop Notifications
Use `libnotify` (via `gi.repository.Notify`) to show system notifications when:
- Recording starts/stops.
- Transcription completes (clicking the notification could copy text or open history).
- Errors occur.

### 3. Global Shortcuts
Register global keyboard shortcuts (e.g., `Super+Alt+R`) to toggle recording.
- **Wayland**: Use `xdg-desktop-portal` GlobalShortcuts interface.
- **X11**: Use `Keybinder` or similar.
- **GNOME Settings**: Provide a `.gschema.xml` to allow users to configure shortcuts via GNOME Control Center (custom shortcuts).

### 4. Application Integration
- **Desktop Entry**: Ensure `com.whisperaloud.App.desktop` is properly configured with actions (Start, Stop).
- **Autostart**: Option to start the daemon on login (`~/.config/autostart/`).

## Detailed Tasks

### Step 1: Notifications & System Integration
- [ ] Implement `NotificationManager` class using `Notify`.
- [ ] Connect daemon signals (`StatusChanged`, `TranscriptionCompleted`) to notifications.
- [ ] Add "Copy to Clipboard" action button to the completion notification.

### Step 2: Global Shortcuts
- [ ] Research best approach for universal global shortcuts (likely `GlobalShortcuts` portal for Wayland support).
- [ ] Implement shortcut registration in the daemon or a helper script.

### Step 3: GNOME Shell Extension (JavaScript)
- [ ] Create a basic extension structure (`metadata.json`, `extension.js`).
- [ ] Implement D-Bus client in JS to talk to `org.fede.whisperAloud`.
- [ ] Add panel indicator and menu.
- [ ] Handle state changes (update icon).

### Step 4: Polish & Packaging
- [ ] Create `install_gnome_integration.sh` script.
- [ ] Update `README.md` with integration details.

## Technical Considerations
- **GNOME Shell Extensions**: Written in GJS (JavaScript with GObject bindings). We will need to generate this code.
- **Portals**: Using `xdg-desktop-portal` is the most future-proof way to handle shortcuts on Wayland.