"""Smart recording flow: MPRIS media pause/resume and hardware mic gain control."""

import atexit
import json
import logging
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional, Set

logger = logging.getLogger(__name__)

# Recovery file lives in /run/user/<uid>/ (tmpfs, cleared on reboot)
_RECOVERY_FILE = f"/run/user/{os.getuid()}/whisperaloud-gain.json"


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

    def set_bus(self, bus: object) -> None:
        """Inject the pydbus SessionBus instance (avoids opening a second connection)."""
        self._bus = bus

    def pause_all_playing(self) -> None:
        """Pause all MPRIS players that are currently Playing and support CanPause."""
        if self._bus is None:
            logger.debug("MprisController: no D-Bus bus, skipping media pause")
            return

        self._paused_by_us.clear()

        try:
            names = self._bus.get(".DBus").ListNames()
        except Exception as e:
            logger.debug(f"MprisController: failed to list D-Bus names: {e}")
            return

        for name in names:
            if not name.startswith("org.mpris.MediaPlayer2."):
                continue
            try:
                player = self._bus.get(name, "/org/mpris/MediaPlayer2")
                props = player["org.mpris.MediaPlayer2.Player"]
                status = props.get("PlaybackStatus", "")
                can_pause = props.get("CanPause", False)

                if status == "Playing" and can_pause:
                    player["org.mpris.MediaPlayer2.Player"].Pause()
                    self._paused_by_us.add(name)
                    logger.debug(f"MprisController: paused {name}")
            except Exception as e:
                logger.debug(f"MprisController: skipping {name}: {e}")

        if self._paused_by_us:
            logger.info(f"MprisController: paused {len(self._paused_by_us)} player(s)")

    def resume_ours(self, delay_ms: int = 0) -> None:
        """Resume players we paused, only if they are still Paused."""
        if not self._paused_by_us or self._bus is None:
            return

        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

        resumed = 0
        for name in list(self._paused_by_us):
            try:
                player = self._bus.get(name, "/org/mpris/MediaPlayer2")
                props = player["org.mpris.MediaPlayer2.Player"]
                status = props.get("PlaybackStatus", "")
                if status == "Paused":
                    player["org.mpris.MediaPlayer2.Player"].Play()
                    resumed += 1
                    logger.debug(f"MprisController: resumed {name}")
                else:
                    logger.debug(f"MprisController: {name} is {status}, not resuming")
            except Exception as e:
                logger.debug(f"MprisController: failed to resume {name}: {e}")

        self._paused_by_us.clear()
        if resumed:
            logger.info(f"MprisController: resumed {resumed} player(s)")

    def clear(self) -> None:
        """Discard tracked players without resuming (e.g. on cancel)."""
        self._paused_by_us.clear()


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
        self._wpctl = shutil.which("wpctl")
        if self._wpctl:
            logger.debug(f"GainController: wpctl found at {self._wpctl}")
        else:
            logger.info("GainController: wpctl not found, gain control disabled")

    # ── Public API ────────────────────────────────────────────────────────────

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
