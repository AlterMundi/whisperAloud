"""Smart recording flow: MPRIS media pause/resume and hardware mic gain control.

WHISP-89: pausing a media player over MPRIS only issues an async D-Bus
``Pause()`` call -- it says nothing about when the player's audio stream
actually goes silent. The old code assumed a fixed ``pre_pause_delay_ms``
(default 0, i.e. no wait at all) was long enough, which could let a tail of
media audio bleed into the start of a recording. Pause/resume now wait for
the real confirmation event instead: a ``PropertiesChanged`` signal on
``org.freedesktop.DBus.Properties`` reporting ``PlaybackStatus`` as
"Paused"/"Stopped" (or "Playing" again, for resume), or the player's bus
name vanishing outright. A bounded, logged-at-WARNING safety timeout
(``_CONFIRM_SAFETY_TIMEOUT_MS``) exists only for a non-compliant player that
never emits either -- it is never the primary mechanism, and recording
always proceeds (degraded) once it fires rather than stalling forever.

Design note -- continuation vs. trim: the ticket brief allowed either (a)
delaying the start of capture until pause is confirmed, or (b) starting
capture immediately and trimming/muting the leading audio once confirmation
arrives. This module implements (a), via ``pause_all_playing(on_done=...)``;
``service/daemon.py``'s ``StartRecording`` calls ``self.recorder.start()``
from that callback instead of synchronously. (b) was rejected: it needs a
cross-thread mute flag shared with ``AudioRecorder``'s callback-driven
capture thread, race-prone against the stateful ``AudioPipeline`` (NoiseGate/
AGC envelope tracking would see a run of unprocessed zeros), and harder to
reason about/test than "don't start yet". (a)'s cost is a short, bounded
delay before capture begins (microseconds-to-tens-of-milliseconds for a
compliant player signalling over a local D-Bus connection; capped at
``_CONFIRM_SAFETY_TIMEOUT_MS`` otherwise) -- strictly better than today's
default-0 fixed wait, never blocks the GLib main loop, and keeps
``StartRecording``'s rarely-exercised failure-cleanup logic in one place
(inside the deferred callback) instead of duplicated across a sync/async
split.
"""

import json
import atexit
import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Callable, Dict, Optional, Set

from gi.repository import GLib

from ..sandbox import is_flatpak_sandbox

logger = logging.getLogger(__name__)

# Recovery file lives in /run/user/<uid>/ (tmpfs, cleared on reboot)
_RECOVERY_FILE = f"/run/user/{os.getuid()}/whisperaloud-gain.json"

# Bounded safety watchdog for pause/resume confirmation. NEVER the primary
# mechanism (PropertiesChanged / name-vanished are) -- exists only so a
# player that never emits either can't stall recording forever. Firing it
# is always a logged degradation, never silent.
_CONFIRM_SAFETY_TIMEOUT_MS = 1500

_PLAYER_PATH = "/org/mpris/MediaPlayer2"
_PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
_PROPS_IFACE = "org.freedesktop.DBus.Properties"


# =============================================================================
# MPRIS Controller
# =============================================================================

@dataclass
class MprisController:
    """
    Pause active MPRIS media players before recording and resume them after.

    Tracks which players WE paused so we only resume those, and only if they
    are still in the Paused state (user may have manually resumed mid-recording).
    """

    _bus: object = field(default=None, repr=False)
    _paused_by_us: Set[str] = field(default_factory=set, repr=False)
    # Bumped on every pause_all_playing()/resume_ours() call so a stale
    # in-flight confirmation wait (e.g. resume_ours() called again before an
    # earlier one's signals arrived) can recognize itself as superseded and
    # skip calling its on_done -- it still tears down its own subscriptions
    # once its players settle or its own safety timeout fires, just without
    # telling anyone.
    _epoch: int = field(default=0, repr=False)
    # Teardown for whatever confirmation wait is currently active (if any),
    # so a superseding call can unsubscribe it right away instead of
    # leaving it to clean itself up once its own safety timeout fires.
    _active_teardown: Optional[Callable[[], None]] = field(default=None, repr=False)

    def _supersede_active_wait(self) -> None:
        self._epoch += 1
        if self._active_teardown is not None:
            try:
                self._active_teardown()
            except Exception:
                pass
            self._active_teardown = None

    def set_bus(self, bus: object) -> None:
        """Inject the pydbus SessionBus instance (avoids opening a second connection).

        No-ops under WHISPER_ALOUD_TEST_MODE=1 (defense in depth, WHISP-46):
        every other method on this class already treats `self._bus is None`
        as the disabled/no-op path, so refusing to store a bus here is
        enough to make pause_all_playing()/resume_ours()/clear() inert for
        the lifetime of the daemon, regardless of recording_flow.pause_media.
        """
        if os.environ.get("WHISPER_ALOUD_TEST_MODE") == "1":
            logger.info("MprisController: WHISPER_ALOUD_TEST_MODE=1, media control disabled")
            return
        self._bus = bus

    def pause_all_playing(
        self,
        on_done: Optional[Callable[[], None]] = None,
        settle_delay_ms: int = 0,
    ) -> None:
        """Pause all MPRIS players that are currently Playing and support CanPause.

        `on_done` (if given) is called exactly once, always from the GLib
        main loop (never a background thread, never a nested loop), once
        every paused player has confirmed PlaybackStatus Paused/Stopped (via
        a PropertiesChanged signal), vanished from the bus, or the bounded
        `_CONFIRM_SAFETY_TIMEOUT_MS` safety timeout elapsed for it (logged at
        WARNING -- a degradation, not a success). `settle_delay_ms`
        (`recording_flow.pre_pause_delay_ms`) is an optional *additional*
        settle margin honored via `GLib.timeout_add` after confirmation,
        never a blocking `time.sleep` -- default 0 means "call on_done as
        soon as confirmed". If there was nothing to pause (no bus, no
        playing+CanPause players, or the D-Bus call itself failed), on_done
        is called inline instead -- there is nothing to wait for, so this
        call stays fully synchronous (as it was before this confirmation
        wait existed) rather than taking a pointless extra main-loop round
        trip through `GLib.idle_add`.
        """
        self._supersede_active_wait()
        epoch = self._epoch
        self._paused_by_us.clear()

        if self._bus is None:
            logger.debug("MprisController: no D-Bus bus, skipping media pause")
            self._call_done(on_done)
            return

        try:
            names = self._bus.get(".DBus").ListNames()
        except Exception as e:
            logger.debug(f"MprisController: failed to list D-Bus names: {e}")
            self._call_done(on_done)
            return

        targets = []
        for name in names:
            if not name.startswith("org.mpris.MediaPlayer2."):
                continue
            try:
                player = self._bus.get(name, _PLAYER_PATH)
                iface = player[_PLAYER_IFACE]
                status = self._safe_prop(iface, "PlaybackStatus", "")
                can_pause = self._safe_prop(iface, "CanPause", False)

                if status == "Playing" and can_pause:
                    iface.Pause()
                    self._paused_by_us.add(name)
                    targets.append(name)
                    logger.debug(f"MprisController: paused {name}")
            except Exception as e:
                logger.debug(f"MprisController: skipping {name}: {e}")

        if targets:
            logger.info(f"MprisController: paused {len(targets)} player(s): {sorted(targets)}")

        if not targets:
            self._call_done(on_done)
            return

        def _on_settled(arrivals: Dict[str, str]) -> None:
            if epoch != self._epoch:
                return
            for name, how in arrivals.items():
                logger.info(f"MprisController: pause of {name} confirmed via {how}")
            self._call_done(on_done, settle_delay_ms)

        self._await_confirmation(epoch, targets, wanted_statuses=("Paused", "Stopped"),
                                  on_all_settled=_on_settled)

    def resume_ours(
        self,
        delay_ms: int = 0,
        on_done: Optional[Callable[[], None]] = None,
    ) -> None:
        """Resume players we paused, only if they are still Paused.

        `delay_ms` (`recording_flow.post_resume_delay_ms`) is an optional
        settle margin honored via `GLib.timeout_add` *before* re-checking
        each player's status and issuing `Play()` -- default 0, i.e.
        re-check immediately. This preserves the original self-correcting
        behavior (a player the user already resumed manually during the
        delay is left alone), just without a blocking `time.sleep`. Nothing
        in the daemon needs to block on a resume completing, so
        confirmation that a resumed player actually reports Playing again
        is observed the same way pause is (PropertiesChanged / name-
        vanished / bounded WARNING degrade) purely for INFO-level
        observability; `on_done` is optional and mainly for tests.
        """
        self._supersede_active_wait()
        epoch = self._epoch

        if not self._paused_by_us or self._bus is None:
            self._paused_by_us.clear()
            self._call_done(on_done)
            return

        def _do_resume() -> None:
            if epoch != self._epoch:
                return
            resumed = []
            for name in list(self._paused_by_us):
                try:
                    player = self._bus.get(name, _PLAYER_PATH)
                    iface = player[_PLAYER_IFACE]
                    status = self._safe_prop(iface, "PlaybackStatus", "")
                    if status == "Paused":
                        iface.Play()
                        resumed.append(name)
                        logger.debug(f"MprisController: resumed {name}")
                    else:
                        logger.debug(f"MprisController: {name} is {status}, not resuming")
                except Exception as e:
                    logger.debug(f"MprisController: failed to resume {name}: {e}")

            self._paused_by_us.clear()
            if not resumed:
                self._call_done(on_done)
                return
            logger.info(f"MprisController: resumed {len(resumed)} player(s): {sorted(resumed)}")

            def _on_settled(arrivals: Dict[str, str]) -> None:
                if epoch != self._epoch:
                    return
                for name, how in arrivals.items():
                    logger.info(f"MprisController: resume of {name} confirmed via {how}")
                self._call_done(on_done)

            self._await_confirmation(epoch, resumed, wanted_statuses=("Playing",),
                                      on_all_settled=_on_settled)

        if delay_ms > 0:
            GLib.timeout_add(delay_ms, lambda: (_do_resume(), GLib.SOURCE_REMOVE)[1])
        else:
            _do_resume()

    def clear(self) -> None:
        """Discard tracked players without resuming (e.g. on cancel)."""
        self._supersede_active_wait()
        self._paused_by_us.clear()

    # ── Internal: event-driven pause/resume confirmation ────────────────────

    @staticmethod
    def _safe_prop(iface: object, prop_name: str, default):
        """Read an MPRIS property off a pydbus interface proxy.

        A pydbus interface object exposes properties as plain attributes
        (descriptors generated from introspection), not a dict -- there is
        no `.get(name, default)`. Missing/unreadable properties (a
        malformed or half-torn-down player) degrade to `default` rather
        than raising.
        """
        try:
            return getattr(iface, prop_name)
        except Exception:
            return default

    @staticmethod
    def _call_done(on_done: Optional[Callable[[], None]], settle_delay_ms: int = 0) -> None:
        """Invoke `on_done`, honoring an optional extra settle delay via
        `GLib.timeout_add` (`settle_delay_ms > 0`) -- never a blocking
        `time.sleep`. With no settle delay, `on_done` is called inline: when
        there was genuinely nothing to wait for (no bus, nothing playing/
        paused), that keeps `pause_all_playing`/`resume_ours` synchronous,
        same as before this wait existed -- deferring via `GLib.idle_add`
        here bought nothing but an unnecessary main-loop round trip (and
        broke every test/caller that reasonably assumes "no media players"
        completes inline). When a real confirmation wait *did* happen,
        `on_done` is still only ever reached from a GLib signal/timeout
        callback (see `_await_confirmation`), so it is already running on
        the main loop by the time it gets here -- never from a background
        thread, regardless of which branch this takes.
        """
        if on_done is None:
            return
        if settle_delay_ms > 0:
            GLib.timeout_add(settle_delay_ms, lambda: (on_done(), GLib.SOURCE_REMOVE)[1])
        else:
            on_done()

    def _await_confirmation(
        self,
        epoch: int,
        names: list,
        wanted_statuses: tuple,
        on_all_settled: Callable[[Dict[str, str]], None],
    ) -> None:
        """Wait for each of `names` to report a status in `wanted_statuses`
        (via PropertiesChanged), vanish from the bus, or time out, then call
        `on_all_settled({name: how_confirmed})` exactly once.

        `on_all_settled` is only ever invoked if `epoch` still matches
        `self._epoch` at the moment every name has settled -- a newer
        pause_all_playing()/resume_ours()/clear() call supersedes this wait
        silently (the newer call owns the outcome), but subscriptions are
        still torn down either way, bounded by the safety timeout if they
        haven't already fired naturally.
        """
        pending = set(names)
        arrivals: Dict[str, str] = {}
        subs: list = []
        safety_id = [None]

        def _unsubscribe_all() -> None:
            for s in subs:
                try:
                    s.unsubscribe()
                except Exception:
                    pass
            subs.clear()

        def _settle(name: str, how: str) -> None:
            if name not in pending:
                return
            pending.discard(name)
            arrivals[name] = how
            if pending:
                return
            if safety_id[0] is not None:
                try:
                    GLib.source_remove(safety_id[0])
                except Exception:
                    pass
                safety_id[0] = None
            _unsubscribe_all()
            if self._active_teardown is _unsubscribe_all:
                self._active_teardown = None
            if epoch == self._epoch:
                on_all_settled(arrivals)

        def _make_props_cb(name: str):
            def _cb(_sender, _obj, _iface, _signal, params):
                try:
                    changed_iface, changed_props, _invalidated = params
                except Exception:
                    return
                if changed_iface != _PLAYER_IFACE:
                    return
                status = changed_props.get("PlaybackStatus")
                if status in wanted_statuses:
                    _settle(name, "PropertiesChanged signal")
            return _cb

        def _make_vanished_cb(name: str):
            def _cb():
                _settle(name, "name vanished")
            return _cb

        for name in names:
            try:
                subs.append(self._bus.subscribe(
                    sender=name, iface=_PROPS_IFACE, signal="PropertiesChanged",
                    object=_PLAYER_PATH, signal_fired=_make_props_cb(name),
                ))
            except Exception as e:
                logger.debug(f"MprisController: could not subscribe to {name}: {e}")
            try:
                subs.append(self._bus.watch_name(name, name_vanished=_make_vanished_cb(name)))
            except Exception as e:
                logger.debug(f"MprisController: could not watch {name}: {e}")

        if not pending:
            _unsubscribe_all()
            if epoch == self._epoch:
                on_all_settled(arrivals)
            return

        def _on_safety_timeout():
            _unsubscribe_all()
            if self._active_teardown is _unsubscribe_all:
                self._active_teardown = None
            safety_id[0] = None
            if pending:
                still_pending = sorted(pending)
                logger.warning(
                    "MprisController: %d player(s) never confirmed status within "
                    "%dms (%s) -- proceeding anyway (degraded)",
                    len(still_pending), _CONFIRM_SAFETY_TIMEOUT_MS, still_pending,
                )
                for name in still_pending:
                    arrivals[name] = "degraded (safety timeout)"
                pending.clear()
                if epoch == self._epoch:
                    on_all_settled(arrivals)
            return GLib.SOURCE_REMOVE

        safety_id[0] = GLib.timeout_add(_CONFIRM_SAFETY_TIMEOUT_MS, _on_safety_timeout)
        self._active_teardown = _unsubscribe_all


# =============================================================================
# Gain Controller
# =============================================================================

@dataclass
class GainSnapshot:
    """Saved mic gain state for a recording session."""
    node_id: str
    volume: float
    muted: bool


@dataclass
class GainController:
    """
    Save, raise, and restore the hardware mic input gain via wpctl (PipeWire).

    Safety features:
    - Never lowers gain (only raises to target if current is lower)
    - Skips if mic is muted (privacy: never unmutes)
    - Persists snapshot to /run/user/<uid>/whisperaloud-gain.json for crash recovery
    - Registers atexit handler for best-effort restore on clean exit
    """

    _wpctl: Optional[str] = field(default=None, repr=False)
    _snapshot: Optional[GainSnapshot] = field(default=None, repr=False)
    _atexit_registered: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        if os.environ.get("WHISPER_ALOUD_TEST_MODE") == "1":
            # WHISP-46 e2e harness: never probe for or shell out to wpctl, and
            # never touch the real crash-recovery file (restore_from_crash_file
            # already no-ops when self._wpctl is None).
            self._wpctl = None
            logger.info("GainController: WHISPER_ALOUD_TEST_MODE=1, gain control disabled")
            return
        if is_flatpak_sandbox():
            # WHISP-61: wpctl is a host tool (not in the Flatpak runtime) and
            # the sandbox only gets the PulseAudio socket, not PipeWire's
            # native one, so it could not reach the session manager anyway.
            # Disable up front: no PATH probe, no crash-file access.
            self._wpctl = None
            logger.info("GainController: running inside Flatpak, gain control disabled")
            return
        self._wpctl = shutil.which("wpctl")
        if self._wpctl:
            logger.debug(f"GainController: wpctl found at {self._wpctl}")
        else:
            logger.info("GainController: wpctl not found, gain control disabled")

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def gain_control_available(self) -> bool:
        """Whether hardware mic gain raise/restore is actually usable right
        now (``wpctl`` resolved and not disabled by test mode/Flatpak
        sandboxing). WHISP-93: surfaced via ``WhisperAloudService.GetStatus``
        (``mic_gain_control``) so a silent, structural no-op (e.g. every
        Flatpak install, WHISP-61) is an observable condition instead of
        something a user can only infer from truncated transcripts -- the
        disable itself is still logged at INFO in ``__post_init__`` above,
        this just makes the resulting state queryable afterwards too.
        """
        return bool(self._wpctl)

    def raise_to(self, target_linear: float) -> bool:
        """
        Save current gain and raise to target_linear if current < target.

        Returns True if gain was saved (caller should call restore() later).
        """
        if not self._wpctl:
            return False

        target = max(0.0, min(1.5, target_linear))

        node_id = self._resolve_default_source_id()
        if node_id is None:
            logger.debug("GainController: could not resolve default source node id")
            return False

        snapshot = self._get_volume(node_id)
        if snapshot is None:
            return False

        if snapshot.muted:
            logger.info("GainController: mic is muted, skipping gain raise (privacy)")
            return False

        self._snapshot = snapshot

        if snapshot.volume >= target:
            logger.debug(
                f"GainController: current gain {snapshot.volume:.3f} >= target {target:.3f}, "
                "no adjustment needed"
            )
            # Still save snapshot so restore() works correctly
        else:
            if not self._set_volume(node_id, target):
                self._snapshot = None
                return False
            logger.info(
                f"GainController: raised gain {snapshot.volume:.3f} → {target:.3f} "
                f"on node {node_id}"
            )

        if not self._atexit_registered:
            atexit.register(self._atexit_restore)
            self._atexit_registered = True

        self._persist_snapshot(snapshot)
        return True

    def restore(self) -> None:
        """Restore the saved gain. Idempotent — safe to call multiple times."""
        snapshot = self._snapshot
        self._snapshot = None
        self._clear_recovery_file()

        if snapshot is None or not self._wpctl:
            return

        self._set_volume(snapshot.node_id, snapshot.volume)
        logger.info(
            f"GainController: restored gain to {snapshot.volume:.3f} on node {snapshot.node_id}"
        )

    def restore_from_crash_file(self) -> None:
        """Attempt to restore gain from a previous crash recovery file."""
        if not self._wpctl:
            return

        try:
            with open(_RECOVERY_FILE) as f:
                data = json.load(f)
            snapshot = GainSnapshot(
                node_id=str(data["node_id"]),
                volume=float(data["volume"]),
                muted=bool(data.get("muted", False)),
            )
        except FileNotFoundError:
            return
        except Exception as e:
            logger.debug(f"GainController: could not read recovery file: {e}")
            self._clear_recovery_file()
            return

        if not snapshot.muted:
            self._set_volume(snapshot.node_id, snapshot.volume)
            logger.info(
                f"GainController: crash-recovery restored gain to {snapshot.volume:.3f} "
                f"on node {snapshot.node_id}"
            )
        self._clear_recovery_file()

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _resolve_default_source_id(self) -> Optional[str]:
        """Resolve @DEFAULT_AUDIO_SOURCE@ to a concrete node ID."""
        try:
            result = subprocess.run(
                [self._wpctl, "inspect", "@DEFAULT_AUDIO_SOURCE@"],
                capture_output=True, text=True, timeout=3
            )
            # Look for "id <number>," or "id = <number>"
            match = re.search(r'\bid\s*[=,]?\s*(\d+)', result.stdout)
            if match:
                return match.group(1)
            # Fallback: use the alias directly
            return "@DEFAULT_AUDIO_SOURCE@"
        except Exception as e:
            logger.debug(f"GainController: inspect failed: {e}")
            return "@DEFAULT_AUDIO_SOURCE@"

    def _get_volume(self, node_id: str) -> Optional[GainSnapshot]:
        """Parse current volume and mute state from wpctl get-volume."""
        try:
            result = subprocess.run(
                [self._wpctl, "get-volume", node_id],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode != 0:
                logger.debug(f"GainController: get-volume failed: {result.stderr.strip()}")
                return None

            output = result.stdout.strip()
            # Format: "Volume: 0.90" or "Volume: 0.90 [MUTED]"
            # Multi-channel: "Volume: 0.90 0.90 [MUTED]"
            muted = "MUTED" in output.upper()
            numbers = re.findall(r'\d+\.\d+|\d+', output.replace("Volume:", ""))
            if not numbers:
                logger.debug(f"GainController: could not parse volume from: {output!r}")
                return None

            volume = float(numbers[0])
            return GainSnapshot(node_id=node_id, volume=volume, muted=muted)
        except Exception as e:
            logger.debug(f"GainController: get-volume exception: {e}")
            return None

    def _set_volume(self, node_id: str, volume: float) -> bool:
        """Set volume via wpctl. Returns True on success."""
        try:
            result = subprocess.run(
                [self._wpctl, "set-volume", node_id, f"{volume:.4f}"],
                capture_output=True, text=True, timeout=3
            )
            if result.returncode != 0:
                logger.debug(f"GainController: set-volume failed: {result.stderr.strip()}")
                return False
            return True
        except Exception as e:
            logger.debug(f"GainController: set-volume exception: {e}")
            return False

    def _persist_snapshot(self, snapshot: GainSnapshot) -> None:
        """Write recovery blob so a crash doesn't leave mic boosted."""
        try:
            with open(_RECOVERY_FILE, "w") as f:
                json.dump({
                    "node_id": snapshot.node_id,
                    "volume": snapshot.volume,
                    "muted": snapshot.muted,
                }, f)
        except Exception as e:
            logger.debug(f"GainController: could not write recovery file: {e}")

    def _clear_recovery_file(self) -> None:
        try:
            os.unlink(_RECOVERY_FILE)
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.debug(f"GainController: could not remove recovery file: {e}")

    def _atexit_restore(self) -> None:
        """Best-effort restore on clean process exit."""
        try:
            self.restore()
        except Exception:
            pass
