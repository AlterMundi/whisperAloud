# Wayland Integration Plan

## Objective
Ensure full functionality of WhisperAloud on Wayland sessions, specifically addressing Global Shortcuts and Clipboard Paste Simulation, which are restricted by Wayland's security model.

## Current Challenges
1.  **Global Shortcuts**: Traditional X11 keybinding methods (like `Keybinder`) do not work on Wayland. Applications cannot listen to global keystrokes.
2.  **Paste Simulation**: Simulating `Ctrl+V` to paste text is restricted. Tools like `xdotool` do not work.
3.  **Clipboard Access**: While `wl-copy` works for copying, programmatic access can be tricky.

## Proposed Solutions

### 1. Global Shortcuts via XDG Desktop Portal
The `xdg-desktop-portal` provides a `GlobalShortcuts` interface that allows applications to register shortcuts in a Wayland-compliant way.
- **Mechanism**: The app requests a shortcut (e.g., "Toggle Recording"). The compositor (GNOME Shell) prompts the user to bind a key.
- **Implementation**: Use `Gio.DBusProxy` to talk to `org.freedesktop.portal.GlobalShortcuts`.

### 2. Paste Simulation via `ydotool` or `uinput`
- **ydotool**: A generic Linux command-line automation tool that works on Wayland (requires a background daemon).
- **Implementation**: Check if `ydotool` is available and configured. If so, use it to send `Ctrl+V`.
- **Fallback**: If `ydotool` is missing, show a notification "Text copied to clipboard" (which we already do).

### 3. GNOME Shell Extension Enhancements
Since the extension runs *inside* the compositor, it has privileged access.
- **Shortcuts**: The extension can register global shortcuts directly using `Main.wm.addKeybinding`.
- **Paste**: The extension *might* be able to inject input, but this is also restricted.

## Detailed Tasks

### Step 1: Global Shortcuts (Portal Approach)
- [ ] Create a `WaylandShortcutManager` class.
- [ ] Implement D-Bus communication with `org.freedesktop.portal.Desktop`.
- [ ] Request a session and register the "Toggle Recording" shortcut.

### Step 2: Paste Simulation
- [ ] Update `PasteSimulator` to support `ydotool`.
- [ ] Add checks for `ydotool` availability.
- [ ] Document `ydotool` setup (requires `ydotoold` daemon).

### Step 3: GNOME Extension Shortcuts (Alternative)
- [ ] Add keybinding support to `extension.js`.
- [ ] Allow users to set the shortcut in GNOME Settings -> Keyboard -> Custom Shortcuts (mapped to `whisper-aloud toggle`).

## Recommendation
For immediate relief, **Step 3 (Custom Shortcuts)** is the most reliable and easiest for users to understand. We can provide a script to set this up automatically.

**Step 1 (Portals)** is the "correct" modern way but is more complex to implement and requires a running GUI loop to handle the portal interaction.

**Step 2 (Paste)** is already partially handled by the clipboard module, but we need to verify `ydotool` integration.