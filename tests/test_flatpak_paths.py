"""Config/data directory resolution inside a Flatpak sandbox (WHISP-61).

Inside Flatpak only ~/.var/app/<app-id>/ is persistent and writable; the
rest of $HOME is not exposed, so anything written to ~/.config or
~/.local/share is lost when the sandbox exits. Flatpak points
XDG_CONFIG_HOME/XDG_DATA_HOME at ~/.var/app/<app-id>/{config,data}, so the
sandboxed app must resolve its directories from those variables. Outside
the sandbox the historical ~/.config / ~/.local/share paths are kept
unchanged (native installs and the existing test suite depend on them).
"""

from pathlib import Path

import pytest

from whisper_aloud import config as config_mod
from whisper_aloud.config import (
    PersistenceConfig,
    WhisperAloudConfig,
    user_config_dir,
    user_data_dir,
)


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    xdg_config = tmp_path / "var-app" / "config"
    xdg_data = tmp_path / "var-app" / "data"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_config))
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg_data))
    return home, xdg_config, xdg_data


def _sandboxed(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(config_mod, "is_flatpak_sandbox", lambda: value)


def test_native_ignores_xdg_and_keeps_home_paths(fake_home, monkeypatch):
    home, _, _ = fake_home
    _sandboxed(monkeypatch, False)
    assert user_config_dir() == home / ".config" / "whisper_aloud"
    assert user_data_dir() == home / ".local" / "share" / "whisper_aloud"


def test_sandbox_uses_xdg_dirs(fake_home, monkeypatch):
    _, xdg_config, xdg_data = fake_home
    _sandboxed(monkeypatch, True)
    assert user_config_dir() == xdg_config / "whisper_aloud"
    assert user_data_dir() == xdg_data / "whisper_aloud"


@pytest.mark.parametrize("bad", ["", "relative/dir"])
def test_sandbox_falls_back_to_home_on_unset_or_relative_xdg(fake_home, monkeypatch, bad):
    home, _, _ = fake_home
    _sandboxed(monkeypatch, True)
    monkeypatch.setenv("XDG_CONFIG_HOME", bad)
    monkeypatch.setenv("XDG_DATA_HOME", bad)
    assert user_config_dir() == home / ".config" / "whisper_aloud"
    assert user_data_dir() == home / ".local" / "share" / "whisper_aloud"


def test_sandbox_config_save_and_load_round_trip_under_xdg(fake_home, monkeypatch):
    home, xdg_config, _ = fake_home
    _sandboxed(monkeypatch, True)
    cfg = WhisperAloudConfig()
    cfg.model.name = "tiny"
    saved = cfg.save()
    assert saved == xdg_config / "whisper_aloud" / "config.json"
    assert saved.exists()
    assert not (home / ".config").exists()
    assert WhisperAloudConfig.load().model.name == "tiny"


def test_sandbox_persistence_defaults_under_xdg_data(fake_home, monkeypatch):
    _, _, xdg_data = fake_home
    _sandboxed(monkeypatch, True)
    p = PersistenceConfig()
    assert Path(p.db_path) == xdg_data / "whisper_aloud" / "history.db"
    assert Path(p.audio_archive_path) == xdg_data / "whisper_aloud" / "audio"


def test_sandbox_database_default_path_under_xdg_data(fake_home, monkeypatch):
    from whisper_aloud.persistence.database import TranscriptionDatabase

    _, _, xdg_data = fake_home
    _sandboxed(monkeypatch, True)
    db = TranscriptionDatabase()
    try:
        assert db.db_path == xdg_data / "whisper_aloud" / "history.db"
        assert db.db_path.exists()
    finally:
        close = getattr(db, "close", None)
        if close:
            close()
