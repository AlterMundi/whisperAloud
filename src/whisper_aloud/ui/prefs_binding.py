"""Instant-apply binding for the Preferences dialog (HIG-07, WHISP-102).

Pure logic, no GTK. Each Preferences row is bound to one or more flat config
keys ("section.field", the same keys as the daemon's GetConfig/SetConfig):

- ``apply(changes)`` validates the resulting config locally (the same
  ``WhisperAloudConfig.validate()`` the daemon runs), then sends only the keys
  that actually changed through ``client.set_config``. A local validation
  failure or a daemon rejection puts the affected rows back (their watchers
  are called with the current value) and reports a message through
  ``on_error``; nothing else is touched, so other settings are never lost.
- ``config_changed(changes)`` handles the daemon's ConfigChanged signal: rows
  watching a key that changed elsewhere (CLI, another window, the main
  window's language selector) are updated. The echo of this dialog's own
  change is a no-op because ``apply`` already stored the value. An empty
  payload (ReloadConfig) re-reads GetConfig.

There is no Save/Cancel: the daemon persists every accepted change at once.
Model changes go through the daemon's existing deferred-reload logic.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Set

from ..config import WhisperAloudConfig

logger = logging.getLogger(__name__)

# a{sv} cannot carry None: SetConfig treats a negative audio.device_id as
# "System Default" (device_id = None) and ConfigChanged echoes it back.
DEVICE_ID_KEY = "audio.device_id"
DEVICE_ID_DEFAULT = -1

NOT_CONNECTED_MESSAGE = "Couldn't apply the change: the background service is not connected"
REJECTED_MESSAGE = "Couldn't apply the change: the background service rejected it"


def _unpack(value: Any) -> Any:
    """Plain Python value for a GLib.Variant (duck-typed) or a plain value."""
    if hasattr(value, "unpack") and callable(value.unpack):
        return value.unpack()
    return value


def decode_value(key: str, value: Any) -> Any:
    """Normalize one value received from the daemon (variant, device sentinel)."""
    value = _unpack(value)
    if key == DEVICE_ID_KEY and isinstance(value, int) and value < 0:
        return None
    return value


def encode_changes(changes: Mapping[str, Any]) -> Dict[str, Any]:
    """Changes ready for client.set_config (None microphone -> sentinel)."""
    encoded = {}
    for key, value in changes.items():
        if key == DEVICE_ID_KEY and value is None:
            value = DEVICE_ID_DEFAULT
        encoded[key] = value
    return encoded


def flatten_config(config: WhisperAloudConfig) -> Dict[str, Any]:
    """{"section.field": value} for every config field (None values kept)."""
    flat: Dict[str, Any] = {}
    for section, values in config.to_dict().items():
        if not isinstance(values, dict):
            continue
        for key, value in values.items():
            flat[f"{section}.{key}"] = value
    return flat


def unflatten(flat: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    """Nested dict for WhisperAloudConfig.from_dict()."""
    nested: Dict[str, Dict[str, Any]] = {}
    for key, value in flat.items():
        if "." not in key:
            continue
        section, name = key.split(".", 1)
        nested.setdefault(section, {})[name] = value
    return nested


def validate_values(flat: Mapping[str, Any]) -> Optional[str]:
    """None when the config built from ``flat`` validates, else the message."""
    try:
        WhisperAloudConfig.from_dict(unflatten(flat)).validate()
    except Exception as e:  # ConfigurationError, or a wrong value type
        return str(e) or e.__class__.__name__
    return None


def apply_flat_changes(
    config: WhisperAloudConfig, changes: Mapping[str, Any]
) -> WhisperAloudConfig:
    """A new config equal to ``config`` with daemon-style flat ``changes``."""
    flat = flatten_config(config)
    for key, value in changes.items():
        flat[key] = decode_value(key, value)
    return WhisperAloudConfig.from_dict(unflatten(flat))


def initial_values(config: WhisperAloudConfig, client) -> Dict[str, Any]:
    """Starting values: the daemon's GetConfig over the local config copy.

    GetConfig omits keys whose value is None; for audio.device_id that means
    "System Default", so a stale local device id is not shown.
    """
    values = flatten_config(config)
    remote = None
    if client is not None:
        try:
            remote = client.get_config()
        except Exception as e:
            logger.warning("Preferences: GetConfig failed, using the local config: %s", e)
    if isinstance(remote, dict) and remote:
        for key, value in remote.items():
            values[key] = decode_value(key, value)
        if DEVICE_ID_KEY not in remote:
            values[DEVICE_ID_KEY] = None
    return values


@dataclass
class ApplyResult:
    ok: bool
    error: Optional[str] = None
    sent: Dict[str, Any] = field(default_factory=dict)


Watcher = Callable[[Any], None]


class PrefsBinding:
    """Flat config values + rows watching them + the daemon client."""

    def __init__(
        self,
        client,
        values: Mapping[str, Any],
        *,
        on_error: Optional[Callable[[str], None]] = None,
        on_applied: Optional[Callable[[Dict[str, Any]], None]] = None,
        validate: Callable[[Mapping[str, Any]], Optional[str]] = validate_values,
    ) -> None:
        self.client = client
        self._values: Dict[str, Any] = dict(values)
        self._watchers: Dict[str, list] = {}
        self._on_error = on_error
        self._on_applied = on_applied
        self._validate = validate

    # ── reading ─────────────────────────────────────────────────────────────

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    @property
    def values(self) -> Dict[str, Any]:
        return dict(self._values)

    def watch(self, key: str, callback: Watcher) -> None:
        """Call ``callback(value)`` whenever ``key`` changes outside the row
        (ConfigChanged) or the row must be put back (rejected change)."""
        self._watchers.setdefault(key, []).append(callback)

    def _notify(self, key: str) -> None:
        for callback in self._watchers.get(key, ()):
            try:
                callback(self._values.get(key))
            except Exception:
                logger.exception("Preferences row update failed for %s", key)

    # ── writing ─────────────────────────────────────────────────────────────

    def apply(self, changes: Mapping[str, Any]) -> ApplyResult:
        """Validate and send the changed keys to the daemon right away."""
        delta = {k: v for k, v in changes.items() if self._values.get(k) != v}
        if not delta:
            return ApplyResult(ok=True)

        candidate = dict(self._values)
        candidate.update(delta)
        error = self._validate(candidate)
        if error is None:
            error = self._send(delta)
        if error is not None:
            for key in delta:
                self._notify(key)  # put the row back to the stored value
            if self._on_error is not None:
                self._on_error(error)
            return ApplyResult(ok=False, error=error)

        self._values.update(delta)
        if self._on_applied is not None:
            self._on_applied(dict(delta))
        return ApplyResult(ok=True, sent=dict(delta))

    def _send(self, delta: Mapping[str, Any]) -> Optional[str]:
        if self.client is None:
            return NOT_CONNECTED_MESSAGE
        try:
            accepted = self.client.set_config(encode_changes(delta))
        except Exception as e:
            logger.warning("Preferences: SetConfig failed: %s", e)
            return REJECTED_MESSAGE
        if not accepted:
            connected = getattr(self.client, "is_connected", True)
            return REJECTED_MESSAGE if connected is not False else NOT_CONNECTED_MESSAGE
        return None

    # ── daemon → rows ───────────────────────────────────────────────────────

    def config_changed(self, changes: Mapping[str, Any]) -> Set[str]:
        """Apply a ConfigChanged payload; returns the keys whose value moved."""
        if not changes:
            return self.refresh()
        moved = set()
        for key, raw in changes.items():
            value = decode_value(key, raw)
            if self._values.get(key) != value:
                self._values[key] = value
                moved.add(key)
        for key in moved:
            self._notify(key)
        return moved

    def refresh(self) -> Set[str]:
        """Re-read every value from GetConfig (after a ReloadConfig)."""
        if self.client is None:
            return set()
        try:
            remote = self.client.get_config()
        except Exception as e:
            logger.warning("Preferences: GetConfig failed: %s", e)
            return set()
        if not isinstance(remote, dict) or not remote:
            return set()
        full = dict(remote)
        full.setdefault(DEVICE_ID_KEY, None)
        return self.config_changed(full)
