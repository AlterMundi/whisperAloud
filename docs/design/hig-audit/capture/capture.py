"""One-shot headless capture of WhisperAloud's GTK UI for the GNOME HIG audit.

Run ONLY through run_capture.sh (env -i + private dbus-run-session with the
repo's servicedir-free config + private Xvfb + temp HOME). Refuses to run
otherwise. The daemon (fake engine, WHISPER_ALOUD_TEST_MODE=1) is spawned
directly as a child and stopped only through its own Popen object.

Modes (argv[1]): shipped (full set, exactly what app.py does), shipped-dark,
adw, adw-dark (subsets). Before HIG-01 app.py was a plain Gtk.Application (no
Adw.init) and the "adw" modes added Adw.init() to preview libadwaita; since
HIG-01 app.py is an Adw.Application, so "shipped" already is the libadwaita look.
Dark: GtkSettings prefer-dark for a plain Gtk app, Adw.StyleManager FORCE_DARK
for an Adw.Application (libadwaita ignores the GtkSettings property).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

WT = Path(os.environ["HIG_WT"])
OUT = Path(os.environ["HIG_OUT"])
TMP = Path(os.environ["HIG_TMP"])
MODE = sys.argv[1] if len(sys.argv) > 1 else "shipped"
ADW = MODE.startswith("adw")
DARK = MODE.endswith("dark")
FULL = MODE == "shipped"
SUFFIX = "" if MODE == "shipped" else f"--{MODE}"

# ── Safety gates ────────────────────────────────────────────────────────────
ADDR = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
assert ADDR and "/run/user" not in ADDR, f"refusing: bus address {ADDR!r} is not private"
assert os.environ.get("DISPLAY", ":0").split(".")[0] not in (":0", ":1"), "refusing: real display"
assert "WAYLAND_DISPLAY" not in os.environ, "refusing: wayland display present"
assert str(Path.home()).startswith(str(TMP)), "refusing: HOME is not the temp home"
assert os.environ.get("WHISPER_ALOUD_TEST_MODE") == "1"

import gi  # noqa: E402

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk  # noqa: E402

import pydbus  # noqa: E402

BUS_NAME = "org.fede.whisperaloud"
_bus = pydbus.SessionBus()
assert BUS_NAME not in list(_bus.dbus.ListActivatableNames()), "refusing: name is activatable"
assert not _bus.dbus.NameHasOwner(BUS_NAME), "refusing: name already owned on this bus"

OUT.mkdir(parents=True, exist_ok=True)
METRICS: dict = {"mode": MODE, "adw_version": f"{Adw.get_major_version()}.{Adw.get_minor_version()}",
                 "gtk_version": f"{Gtk.get_major_version()}.{Gtk.get_minor_version()}"}


def log(*a):
    print("[capture]", *a, flush=True)


def pump(seconds: float = 0.2) -> None:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        while ctx.iteration(False):
            pass
        time.sleep(0.01)


def wait_until(cond, timeout: float, what: str) -> bool:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        while ctx.iteration(False):
            pass
        if cond():
            return True
        time.sleep(0.02)
    log(f"timeout waiting for {what}")
    return False


def snap(widget, name: str, settle: float = 0.3) -> None:
    pump(settle)
    w, h = widget.get_width(), widget.get_height()
    if w <= 0 or h <= 0:
        log(f"skip {name}: not allocated")
        return
    paintable = Gtk.WidgetPaintable.new(widget)
    snapshot = Gtk.Snapshot.new()
    paintable.snapshot(snapshot, w, h)
    node = snapshot.to_node()
    if node is None:
        log(f"skip {name}: empty node")
        return
    renderer = widget.get_native().get_renderer()
    texture = renderer.render_texture(node, None)
    path = OUT / f"{name}{SUFFIX}.png"
    texture.save_to_png(str(path))
    log(f"saved {path.name} ({w}x{h})")


def toplevels():
    return [w for w in Gtk.Window.list_toplevels() if w.get_visible()]


def new_toplevel(before, timeout=3.0):
    found = []

    def _c():
        found[:] = [w for w in toplevels() if w not in before]
        return bool(found)

    wait_until(_c, timeout, "new toplevel")
    return found[0] if found else None


# ── Fixtures ────────────────────────────────────────────────────────────────
AUDIO_SRC = WT / "tests/e2e/fixtures/audio/cancion.wav"
DICT_WAV = TMP / "dictation.wav"
shutil.copy(AUDIO_SRC, DICT_WAV)
DICT_TXT = DICT_WAV.with_suffix(".txt")
TEXTS = [
    "Remind me to send the quarterly report to Ana before Friday, and attach the updated budget spreadsheet.",
    "Meeting notes: the release candidate is ready, translations are pending, and the icon needs a designer review.",
    "Buy oat milk, coffee beans and batteries for the wireless keyboard.",
    "Dear team, thanks for the thorough review. I have addressed every comment and pushed a new revision.",
]
DICT_TXT.write_text(TEXTS[0])

# ── Patches: never touch real audio hardware or play sounds ────────────────
from whisper_aloud.audio.device_manager import AudioDevice  # noqa: E402
import whisper_aloud.audio as wa_audio  # noqa: E402

_FAKE_DEVICES = [
    AudioDevice(id=0, name="Built-in Audio Analog Stereo", channels=2, sample_rate=48000.0,
                is_default=True, hostapi="fake"),
    AudioDevice(id=1, name="USB Headset Microphone", channels=1, sample_rate=48000.0,
                is_default=False, hostapi="fake"),
]
wa_audio.DeviceManager.list_input_devices = staticmethod(lambda: list(_FAKE_DEVICES))
wa_audio.DeviceManager.get_device_by_id = staticmethod(
    lambda i: next(d for d in _FAKE_DEVICES if d.id == i))

import whisper_aloud.ui.main_window as mw_mod  # noqa: E402
from whisper_aloud.ui.sound_feedback import SoundFeedback as _SF  # noqa: E402

mw_mod.SoundFeedback = lambda enabled=True: _SF(enabled=False)

# ── Daemon (fake engine) on the private bus ────────────────────────────────
DAEMON = {"proc": None, "log": None}


def _pdeathsig():
    try:
        import ctypes
        ctypes.CDLL("libc.so.6").prctl(1, 15)
    except Exception:
        pass


def start_daemon() -> bool:
    from tests.e2e.env_builder import build_daemon_env

    xdg = TMP / "daemon-home"
    (xdg / "runtime").mkdir(parents=True, exist_ok=True)
    os.chmod(xdg / "runtime", 0o700)
    env = build_daemon_env(xdg, ADDR, {
        "FAKE_AUDIO_WAV": str(DICT_WAV),
        "FAKE_ENGINE_LATENCY_S": "2.0",
    })
    logf = open(TMP / "daemon.log", "w")
    proc = subprocess.Popen([sys.executable, "-m", "whisper_aloud", "--daemon"], env=env,
                            stdout=logf, stderr=subprocess.STDOUT, preexec_fn=_pdeathsig)
    DAEMON.update(proc=proc, log=logf)

    def _up():
        if proc.poll() is not None:
            return True
        try:
            return bool(_bus.dbus.NameHasOwner(BUS_NAME))
        except Exception:
            return False

    wait_until(_up, 20, "daemon bus name")
    if proc.poll() is not None or not _bus.dbus.NameHasOwner(BUS_NAME):
        log("daemon failed to start; log tail:\n" + (TMP / "daemon.log").read_text()[-3000:])
        return False
    owner = int(_bus.dbus.GetConnectionUnixProcessID(BUS_NAME))
    assert owner == proc.pid, f"refusing: {BUS_NAME} owned by pid {owner}, not our daemon {proc.pid}"
    log(f"daemon up pid={proc.pid}")
    return True


def stop_daemon() -> None:
    proc = DAEMON["proc"]
    if proc is None or proc.poll() is not None:
        return
    try:
        _bus.get(BUS_NAME).Quit()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=3)
    if DAEMON["log"]:
        DAEMON["log"].close()


# ── App ─────────────────────────────────────────────────────────────────────
def main() -> None:
    if not Gtk.init_check():
        raise SystemExit("GTK init failed")
    if ADW:
        Adw.init()
        if DARK:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)

    from whisper_aloud.ui.app import WhisperAloudApp

    IS_ADW_APP = issubclass(WhisperAloudApp, Adw.Application)
    METRICS["app_class_base"] = "Adw.Application" if IS_ADW_APP else "Gtk.Application"
    if DARK and not ADW and not IS_ADW_APP:
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)

    app = WhisperAloudApp()
    app.register(None)  # runs do_startup: app CSS + actions, exactly like app.run()
    if DARK and not ADW and IS_ADW_APP:
        # libadwaita is initialized by the app's startup; force dark after it.
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
    app.activate()      # do_activate: creates + presents MainWindow
    win = app.window
    pump(0.15)

    if FULL:
        loading = next((w for w in toplevels() if w is not win), None)
        snap(win, "01-startup-connecting-main", settle=0.05)
        if loading is not None:
            snap(loading, "02-startup-connecting-dialog", settle=0.05)
    # Retry phase (daemon absent) -> unavailable dialog after the backoff budget.
    wait_until(lambda: "retrying" in win.status_bar.status_msg_label.get_text(), 10, "retry status")
    if FULL:
        snap(win, "03-daemon-retrying-statusbar", settle=0.05)
    wait_until(lambda: win._unavailable_dialog is not None, 30, "unavailable dialog")
    if FULL:
        snap(win, "04-daemon-unavailable-main")
        if win._unavailable_dialog is not None:
            snap(win._unavailable_dialog, "05-daemon-unavailable-dialog")

    if not start_daemon():
        return
    if win._unavailable_dialog is not None:
        win._retry_daemon_connection(win._unavailable_dialog)
    wait_until(lambda: win._daemon_available and getattr(win, "history_panel", None) is not None,
               20, "connected")
    wait_until(lambda: win._daemon_model_state == "ready", 10, "model ready")
    pump(0.8)
    snap(win, "06-idle-empty-history")

    def record_once(text: str, shots: bool) -> None:
        DICT_TXT.write_text(text)
        win.record_button.emit("clicked")
        wait_until(lambda: win._state.value == "recording", 5, "recording")
        pump(2.2)
        if shots:
            snap(win, "07-recording", settle=0.05)
        win.record_button.emit("clicked")
        wait_until(lambda: win._state.value == "transcribing", 5, "transcribing")
        if shots:
            snap(win, "08-transcribing", settle=0.2)
        wait_until(lambda: win._state.value == "ready", 15, "ready")
        pump(0.6)

    record_once(TEXTS[3], shots=False)
    record_once(TEXTS[2], shots=False)
    record_once(TEXTS[1], shots=False)
    record_once(TEXTS[0], shots=FULL)
    # one favorite, then refresh the list
    try:
        recent = win.history_manager.get_recent(limit=10)
        if len(recent) > 2:
            win.history_manager.toggle_favorite(recent[2].id)
        win.history_panel.refresh_recent()
    except Exception as e:
        log(f"favorite failed: {e}")
    pump(1.0)
    snap(win, "09-result-shown")

    METRICS["main_window_min_width"] = win.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
    METRICS["main_window_min_height"] = win.measure(Gtk.Orientation.VERTICAL, -1)[0]

    if FULL:
        # history selection mode (right-click entry)
        try:
            first = win.history_manager.get_recent(limit=1)[0]
            win.history_panel._enter_selection_mode(first.id)
            snap(win, "10-history-selection-mode")
            before = toplevels()
            win.history_panel._on_delete_selected_clicked(None)
            dlg = new_toplevel(before)
            if dlg is not None:
                snap(dlg, "11-history-delete-confirm")
                dlg.close()
            win.history_panel._exit_selection_mode()
        except Exception as e:
            log(f"selection mode failed: {e}")
        pump(0.3)

        # model readiness states, via the real ModelStateChanged handler
        for state, detail, name in [
            ("loading", "", "12-model-loading"),
            ("missing", "", "13-model-missing"),
            ("downloading", "", "14-model-downloading"),
            ("error", "CUDA out of memory while loading large-v3 (try a smaller model or CPU)", "15-model-error"),
        ]:
            win._apply_model_state(state, detail)
            snap(win, name)
        win._apply_model_state("loading", "")
        win.set_state(win._state.__class__.TRANSCRIBING)
        snap(win, "16-transcribing-while-model-loads")
        win.set_state(win._state.__class__.READY)
        win._apply_model_state("ready", "")

        # daemon error signal -> ERROR state ("Try Again")
        win._handle_daemon_error("NO_INPUT_DEVICE", "No input device available")
        snap(win, "17-error-state", settle=0.4)
        win.set_state(win._state.__class__.IDLE)

        # live partial transcript rendering (synthetic signal payloads through the real handler)
        win.set_state(win._state.__class__.RECORDING)
        win._partial_view.reset()
        win._handle_partial_transcript("s-demo", "Meeting notes: the release candidate is ready,",
                                       " translations are pend", 1)
        snap(win, "18-live-partial-transcript", settle=0.4)  # past the 200ms button transition
        win._partial_view.reset()
        win.set_state(win._state.__class__.IDLE)
        pump(0.3)

        # file transcription flow (drag-and-drop / Ctrl+O end in the same call)
        DICT_TXT.write_text("Interview recording, part one: thanks for joining us today.")
        win._start_file_transcription(str(DICT_WAV))
        snap(win, "19-file-transcribing", settle=0.15)
        wait_until(lambda: win._file_job_id is None, 15, "file job done")
        snap(win, "20-file-result", settle=0.4)

        # keyboard shortcuts window
        before = toplevels()
        win._on_help_clicked(None)
        sw = new_toplevel(before)
        if sw is not None:
            snap(sw, "21-shortcuts-window")
            sw.close()
        pump(0.2)

    # settings dialog
    before = toplevels()
    win._on_settings_clicked(None)
    sd = new_toplevel(before)
    if sd is not None:
        pages = ["general", "history", "advanced"] if FULL else ["general"]
        for i, page in enumerate(pages):
            sd.stack.set_visible_child_name(page)
            snap(sd, f"3{i}-settings-{page}")
        if FULL:
            sd.set_default_size(620, 2350)
            pump(0.5)
            for i, page in enumerate(pages):
                sd.stack.set_visible_child_name(page)
                snap(sd, f"3{i}-settings-{page}-full", settle=0.5)
            sd.stack.set_visible_child_name("general")
            sd.advanced_row.set_expanded(True)
            snap(sd, "33-settings-general-model-advanced-expanded", settle=0.5)
            sd.advanced_row.set_expanded(False)
            sd.set_default_size(600, 700)
            pump(0.4)
            before2 = toplevels()
            sd._on_view_catalog_clicked(None)
            cat = new_toplevel(before2)
            if cat is not None:
                snap(cat, "34-settings-model-catalog")
                cat.close()
            pump(0.2)
            # unsaved-changes confirmation
            sd.auto_paste_switch.set_active(not sd.auto_paste_switch.get_active())
            before3 = toplevels()
            sd.close()
            conf = new_toplevel(before3)
            if conf is not None:
                snap(conf, "35-settings-discard-confirm")
                conf.close()
            pump(0.3)
            # save -> success alert
            before4 = toplevels()
            sd._on_save_clicked(None)
            ok = new_toplevel(before4)
            if ok is not None:
                snap(ok, "36-settings-saved-alert")
                ok.close()
            pump(0.3)
        try:
            sd._allow_close = True
            sd.close()
        except Exception:
            pass
        pump(0.3)

    # narrow width (metainfo promises display_length >= 360)
    if FULL or ADW or DARK:
        win.set_default_size(360, 640)
        pump(0.6)
        METRICS["narrow_request_360_actual_width"] = win.get_width()
        snap(win, "40-narrow-360-request")
        win.history_toggle.set_active(False)
        pump(0.5)
        METRICS["min_width_history_hidden"] = win.measure(Gtk.Orientation.HORIZONTAL, -1)[0]
        snap(win, "41-narrow-history-hidden")
        win.history_toggle.set_active(True)

    (OUT / f"metrics{SUFFIX}.json").write_text(json.dumps(METRICS, indent=2))
    log(json.dumps(METRICS))
    try:
        win.cleanup()
    except Exception:
        pass


try:
    main()
finally:
    stop_daemon()
