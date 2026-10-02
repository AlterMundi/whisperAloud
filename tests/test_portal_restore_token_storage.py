"""Tests for restore_token persistence under XDG_DATA_HOME (WHISP-59).

conftest.py already points XDG_DATA_HOME at a throwaway temp dir for every
test in this suite, so these never touch the real
~/.local/share/whisper_aloud.
"""

import os
import stat

import pytest

from whisper_aloud.clipboard.portal_paste_simulator import (
    load_restore_token,
    save_restore_token,
)


class TestRestoreTokenStorage:
    def test_load_returns_none_when_nothing_saved(self):
        assert load_restore_token() is None

    def test_save_then_load_round_trips(self):
        save_restore_token("abc-123")
        assert load_restore_token() == "abc-123"

    def test_save_overwrites_previous_token(self):
        save_restore_token("first")
        save_restore_token("second")
        assert load_restore_token() == "second"

    def test_save_empty_token_is_a_noop(self):
        save_restore_token("real-token")
        save_restore_token("")
        assert load_restore_token() == "real-token"

    def test_uses_xdg_data_home_not_real_home(self, monkeypatch, tmp_path):
        isolated = tmp_path / "isolated-data-home"
        monkeypatch.setenv("XDG_DATA_HOME", str(isolated))
        save_restore_token("isolated-token")
        assert (isolated / "whisper_aloud" / "portal_restore_token").read_text() == (
            "isolated-token"
        )


def _mode_bits(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


class TestRestoreTokenPermissions:
    """Audit fix (MEDIUM): the restore_token is a long-lived RemoteDesktop
    consent credential and must be private to this user regardless of the
    process umask -- file 0600, parent dir 0700."""

    @pytest.mark.parametrize("umask", [0o002, 0o022])
    def test_file_is_0600_regardless_of_umask(self, monkeypatch, tmp_path, umask):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data-home"))
        old_umask = os.umask(umask)
        try:
            save_restore_token("secret-token")
        finally:
            os.umask(old_umask)

        token_path = tmp_path / "data-home" / "whisper_aloud" / "portal_restore_token"
        assert _mode_bits(token_path) == 0o600

    @pytest.mark.parametrize("umask", [0o002, 0o022])
    def test_parent_dir_is_0700_regardless_of_umask(self, monkeypatch, tmp_path, umask):
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data-home"))
        old_umask = os.umask(umask)
        try:
            save_restore_token("secret-token")
        finally:
            os.umask(old_umask)

        data_dir = tmp_path / "data-home" / "whisper_aloud"
        assert _mode_bits(data_dir) == 0o700

    def test_permissions_corrected_on_overwrite_of_loosely_permissioned_file(
        self, monkeypatch, tmp_path
    ):
        """If a file was somehow left world-readable (e.g. created by an
        older version of this code, or tampered with), saving again must
        still tighten it back to 0600."""
        data_home = tmp_path / "data-home"
        monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
        token_dir = data_home / "whisper_aloud"
        token_dir.mkdir(parents=True)
        token_path = token_dir / "portal_restore_token"
        token_path.write_text("old-token")
        token_path.chmod(0o644)
        token_dir.chmod(0o755)

        save_restore_token("new-token")

        assert _mode_bits(token_path) == 0o600
        assert _mode_bits(token_dir) == 0o700
        assert load_restore_token() == "new-token"
