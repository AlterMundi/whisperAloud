# Phase 6: D-Bus Service (Daemon Mode) Implementation Plan

## Objective
Implement a D-Bus service to allow `whisperAloud` to run as a background daemon, enabling control from the CLI, GUI, or other system components (like global shortcuts). This decouples the core logic from the UI and allows for a "headless" operation mode.

## Current State
- **Core Components**: `AudioRecorder` (recording state machine) and `Transcriber` (Whisper wrapper) are robust and independent.
- **GUI**: `WhisperAloudApp` (GTK4) currently manages the lifecycle and directly instantiates core components.
- **CLI**: `__main__.py` provides a simple one-shot transcription command.
- **Missing**: A persistent background service that can handle recording requests from multiple sources (GUI, CLI, shortcuts).

## Proposed Architecture

### 1. D-Bus Interface Definition
We will define a standard D-Bus interface for controlling the application.

- **Bus Name**: `org.fede.whisperAloud`
- **Object Path**: `/org/fede/whisperAloud/Service`
- **Interface**: `org.fede.whisperAloud.Control`

**Methods**:
| Method | Signature | Description |
|--------|-----------|-------------|
| `StartRecording` | `() -> v` | Starts audio recording. Returns nothing. |
| `StopRecording` | `() -> s` | Stops recording and triggers transcription. Returns transcription text. |
| `ToggleRecording` | `() -> s` | Toggles state. Returns new state string. |
| `GetStatus` | `() -> s` | Returns current state (`idle`, `recording`, `transcribing`, `paused`). |
| `Quit` | `() -> v` | Stops the daemon service. |

**Signals**:
| Signal | Signature | Description |
|--------|-----------|-------------|
| `StatusChanged` | `s` | Emitted when state changes (e.g., `idle` -> `recording`). |
| `TranscriptionCompleted` | `s` | Emitted with result text when transcription finishes. |
| `ErrorOccurred` | `s` | Emitted with error message if something goes wrong. |

### 2. Service Implementation (`src/whisper_aloud/service/`)
We will create a new package `service` to house the daemon logic.

- **`daemon.py`**:
    - Contains the `WhisperAloudService` class.
    - Inherits from `Gio.DBusInterfaceSkeleton` (or uses high-level `pydbus`/`dbus-python` if preferred, but `PyGObject` is already a dependency).
    - **Lifecycle**:
        - Instantiates `AudioRecorder` and `Transcriber`.
        - Runs a `GLib.MainLoop` to handle D-Bus events and async operations.
    - **Concurrency**:
        - Recording happens in a background thread (handled by `AudioRecorder`).
        - Transcription should be offloaded to a separate thread to avoid blocking the D-Bus main loop.
        - Signals must be emitted on the main thread.

### 3. Application Entry Point Updates (`src/whisper_aloud/__main__.py`)
The entry point needs to become smarter to handle the single-instance/daemon logic.

- **New Argument**: `--daemon` to start the service in the background.
- **Logic Flow**:
    1. **Check for existing service**: Try to connect to `org.fede.whisperAloud` on the session bus.
    2. **If Service Exists**:
        - If CLI args (like `start`, `stop`) are present, call the corresponding D-Bus method on the existing service.
        - If no args (or `gui` arg), send a command to the service to show the window (if we decide the service manages the GUI, otherwise just launch the GUI client). *Refinement: The GUI should probably be a client of the service.*
    3. **If Service Does NOT Exist**:
        - If `--daemon`: Start the `WhisperAloudService` and run the main loop.
        - If GUI requested: Start the Service *and* the GUI (in the same process for simplicity initially, or spawn service in background). *Decision: For Phase 6, let's keep it simple: `whisper-aloud --daemon` starts the background process. The GUI app will be refactored to connect to this service if available, or run standalone (Phase 7 integration).*

### 4. Client Implementation
- **CLI Client**: Update `__main__.py` to act as a D-Bus client when commands are issued.
    - `whisper-aloud start`: Calls `StartRecording()`
    - `whisper-aloud stop`: Calls `StopRecording()` and prints result.
    - `whisper-aloud status`: Calls `GetStatus()`

## Detailed Task List

### Step 1: D-Bus Service Skeleton
- [ ] Create `src/whisper_aloud/service/__init__.py`
- [ ] Create `src/whisper_aloud/service/daemon.py`
- [ ] Define the XML introspection data for the interface.
- [ ] Implement the `WhisperAloudService` class with stub methods.
- [ ] Create a script to run the service for testing.

### Step 2: Core Integration
- [ ] Integrate `AudioRecorder` into `WhisperAloudService`.
- [ ] Integrate `Transcriber` into `WhisperAloudService`.
- [ ] Implement `StartRecording`, `StopRecording`, `ToggleRecording` logic.
- [ ] Handle threading: Ensure `stop()` (which triggers transcription) doesn't block the D-Bus loop. Use `concurrent.futures` or a dedicated worker thread.

### Step 3: CLI & Entry Point
- [ ] Refactor `src/whisper_aloud/__main__.py`.
- [ ] Add `argparse` subcommands or flags for daemon control.
- [ ] Implement the D-Bus client logic to send commands to the running service.

### Step 4: Testing & Documentation
- [ ] Verify `whisper-aloud --daemon` runs and stays alive.
- [ ] Verify `whisper-aloud start` triggers recording in the daemon.
- [ ] Verify `whisper-aloud stop` returns the transcription.
- [ ] Update `README.md` with new usage instructions.

## Technical Considerations
- **PyGObject vs. dbus-python**: Since the project already uses GTK4 (PyGObject), we should stick to `Gio.DBusConnection` or a high-level wrapper compatible with it to avoid extra dependencies.
- **Main Loop**: The daemon requires a `GLib.MainLoop`. The `AudioRecorder` uses `sounddevice` (PortAudio) which has its own threads, so they coexist well.
- **Error Handling**: D-Bus errors should be returned to the client if operations fail (e.g., no microphone).