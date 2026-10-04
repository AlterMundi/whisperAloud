"""Shared helper for building a WhisperAloudService with every boundary mocked.

Not a test module itself (no ``test_`` prefix, so pytest never collects it directly) —
imported by tests that need a daemon instance without touching D-Bus, GTK, wpctl, MPRIS,
hotkey portals, notifications, or the clipboard/paste backends.

Mirrors the stub-module bootstrap in tests/test_daemon.py (pydbus signal() is a descriptor
that blocks instance assignment, so GLib/pydbus must be faked before the daemon module is
imported) but additionally mocks GainController/MprisController, matching the WHISP-45
brief: "Never construct a real WhisperAloudService without mocking GainController, the
indicator, HotkeyManager, media control, notifications and paste."
"""
import importlib
import sys
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

from whisper_aloud.config import WhisperAloudConfig


class _SyncExecutor:
    """Drop-in for ThreadPoolExecutor that runs submitted work synchronously.

    The daemon submits transcription jobs to a real ThreadPoolExecutor so D-Bus calls
    return immediately; in tests that makes `_transcribe_and_emit` racy against
    assertions made right after (observed as flaky failures only under full-suite
    thread contention, never in isolation). Running it inline keeps state-machine
    tests deterministic without touching daemon.py.
    """

    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)
        return MagicMock()

    def shutdown(self, wait: bool = True) -> None:
        return None


class _FakeSignal:
    """Drop-in for pydbus.generic.signal that allows per-instance mocking."""

    def __init__(self):
        self._store = {}
        self.name = "anon"

    def __set_name__(self, owner, name):
        self.name = name

    def __get__(self, obj, objtype=None):
        if obj is None:
            return self
        key = id(obj)
        if key not in self._store:
            self._store[key] = MagicMock(name=f"signal_{self.name}")
        return self._store[key]

    def __set__(self, obj, value):
        self._store[id(obj)] = value


def _build_stub_modules():
    """Build local stubs used for daemon imports and runtime checks."""
    fake_pydbus_generic = MagicMock()
    fake_pydbus_generic.signal = _FakeSignal
    fake_pydbus = MagicMock()
    fake_pydbus.generic = fake_pydbus_generic
    fake_pydbus.SessionBus = MagicMock

    fake_gtk = MagicMock()
    fake_gtk.init_check.return_value = (True, [])
    fake_glib = MagicMock()
    fake_glib.Variant = lambda t, v: v
    fake_glib.idle_add = lambda fn, *args: fn(*args)
    fake_glib.SOURCE_REMOVE = False
    fake_glib.PRIORITY_DEFAULT = 0
    fake_glib.unix_signal_add = MagicMock(return_value=1)
    fake_glib.timeout_add = MagicMock(return_value=1)
    fake_glib.source_remove = MagicMock()

    fake_gi_repo = MagicMock()
    fake_gi_repo.Gtk = fake_gtk
    fake_gi_repo.GLib = fake_glib
    fake_gi = MagicMock()
    fake_gi.repository = fake_gi_repo

    fake_scipy = MagicMock()
    fake_scipy.signal = MagicMock()
    fake_faster_whisper = MagicMock()
    fake_faster_whisper.WhisperModel = MagicMock()

    return {
        "pydbus": fake_pydbus,
        "pydbus.generic": fake_pydbus_generic,
        "gi": fake_gi,
        "gi.repository": fake_gi_repo,
        "sounddevice": MagicMock(),
        "scipy": fake_scipy,
        "faster_whisper": fake_faster_whisper,
    }


def _import_daemon_module(stub_modules):
    """Import daemon module with local D-Bus/GLib stubs."""
    with patch.dict(sys.modules, stub_modules):
        sys.modules.pop("whisper_aloud.service.daemon", None)
        module = importlib.import_module("whisper_aloud.service.daemon")
    return module


def make_daemon(
    config: WhisperAloudConfig = None,
    indicator_fails: bool = False,
    hotkey_fails: bool = False,
):
    """Create a WhisperAloudService with every process boundary mocked.

    Returns the service plus the ExitStack holding the patches (caller must keep a
    reference alive — e.g. via the `daemon` pytest fixture below — or call
    `service._patches.close()` when done, so mocks stay in effect for the test body).
    """
    stub_modules = _build_stub_modules()
    daemon_module = _import_daemon_module(stub_modules)

    stack = ExitStack()
    stack.enter_context(patch.dict(sys.modules, stub_modules))
    stack.enter_context(patch.dict("os.environ", {"DISPLAY": ":0"}, clear=False))
    stack.enter_context(patch.object(daemon_module, "SessionBus"))
    stack.enter_context(patch.object(daemon_module, "AudioRecorder"))
    stack.enter_context(patch.object(daemon_module, "create_engine"))
    stack.enter_context(patch.object(daemon_module, "NotificationManager"))
    stack.enter_context(patch.object(daemon_module, "HistoryManager"))
    # daemon.py constructs the clipboard through build_clipboard_manager()
    # (WHISP-46), not ClipboardManager directly -- patch both names so a
    # real ClipboardManager is never constructed, and importantly never
    # *called*, regardless of which one daemon.py currently references.
    stack.enter_context(patch.object(daemon_module, "ClipboardManager"))
    stack.enter_context(patch.object(daemon_module, "build_clipboard_manager"))
    stack.enter_context(patch.object(daemon_module, "WindowManager"))
    mock_ind_cls = stack.enter_context(patch.object(daemon_module, "WhisperAloudIndicator"))
    mock_hk_cls = stack.enter_context(patch.object(daemon_module, "HotkeyManager"))
    mock_gain_cls = stack.enter_context(patch.object(daemon_module, "GainController"))
    mock_mpris_cls = stack.enter_context(patch.object(daemon_module, "MprisController"))
    mock_glib = stack.enter_context(patch.object(daemon_module, "GLib"))
    # PasteSimulator is imported locally (`from ..clipboard import PasteSimulator`) inside
    # _transcribe_and_emit, so it must be patched on its home module, not on daemon_module.
    # Without this, auto_paste (on by default) spawns a real background thread that shells
    # out to xdotool/ydotool/xclip — exactly the kind of process-boundary call the brief
    # says must stay mocked.
    stack.enter_context(patch("whisper_aloud.clipboard.PasteSimulator"))
    # 0.3.0 model preload: background (re)loads first check, offline, whether
    # the configured model is available locally. Never let a unit test
    # consult the real model caches for that -- report "available" unless a
    # test patches it differently. Patched on the module object the freshly
    # imported daemon module references (patch.dict(sys.modules) above can
    # leave a different whisper_aloud.service.model_state in sys.modules).
    stack.enter_context(
        patch.object(daemon_module.model_state, "is_model_available", return_value=True)
    )

    mock_glib.Variant = lambda t, v: v
    mock_glib.idle_add = lambda fn, *args: fn(*args)
    mock_glib.SOURCE_REMOVE = False
    mock_glib.timeout_add = MagicMock(return_value=1)
    mock_glib.source_remove = MagicMock()

    if indicator_fails:
        mock_ind_cls.side_effect = Exception("no tray available")

    if hotkey_fails:
        mock_hk_cls.side_effect = Exception("hotkey init failed")
    else:
        mock_hk_cls.return_value.available = True
        mock_hk_cls.return_value.backend = "keybinder"

    mock_gain_cls.return_value.restore_from_crash_file.return_value = None
    mock_mpris_cls.return_value.set_bus.return_value = None
    # WHISP-89: pause_all_playing()/resume_ours() are now continuation-based
    # (on_done is only called once media pause/resume is confirmed, or
    # there was nothing to wait for). MprisController itself is mocked out
    # here, so nothing would ever call on_done -- StartRecording/
    # StopRecording would hang mid-flight from every test's point of view.
    # Simulate the real "no bus configured" behavior (the harness never
    # gives MprisController a real bus either): call on_done immediately,
    # inline, exactly like the real implementation does when there is
    # nothing to actually wait for.
    mock_mpris_cls.return_value.pause_all_playing.side_effect = (
        lambda on_done=None, settle_delay_ms=0: on_done() if on_done else None
    )
    mock_mpris_cls.return_value.resume_ours.side_effect = (
        lambda delay_ms=0, on_done=None: on_done() if on_done else None
    )

    try:
        service = daemon_module.WhisperAloudService(config=config or WhisperAloudConfig())
    except Exception:
        stack.close()
        raise

    # Make transcription jobs run inline instead of on a real background thread
    # (see _SyncExecutor docstring).
    service.executor = _SyncExecutor()

    service._daemon_module = daemon_module
    service._mock_glib = mock_glib
    service._mock_indicator_cls = mock_ind_cls
    service._mock_hotkey_cls = mock_hk_cls
    service._mock_gain_cls = mock_gain_cls
    service._mock_mpris_cls = mock_mpris_cls
    service._patches = stack
    return service
