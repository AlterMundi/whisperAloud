"""Tests for restore_token persistence under XDG_DATA_HOME (WHISP-59).

conftest.py already points XDG_DATA_HOME at a throwaway temp dir for every
test in this suite, so these never touch the real
~/.local/share/whisper_aloud.
"""

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
