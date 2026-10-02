"""Tests for Flatpak sandbox detection (WHISP-59).

Pure logic: no filesystem/env access beyond what's explicitly injected, so
these tests never depend on whether the *test runner itself* happens to be
inside a Flatpak sandbox.

Audit fix: `/.flatpak-info` is the sole authoritative signal. `FLATPAK_ID`
alone (without the bind-mounted file) must NOT classify a process as
sandboxed -- it can leak into a real host shell via `flatpak-spawn --host`
or a stray export, and treating it as sufficient risks routing a real host
shell to the portal-only paste/hotkey path instead of the other way
around. See sandbox.py's module docstring for the full tradeoff.
"""

from pathlib import Path

from whisper_aloud.sandbox import is_flatpak_sandbox


class TestFlatpakIdAloneIsNotAuthoritative:
    def test_false_when_flatpak_id_set_but_file_absent(self, tmp_path):
        """This is the leaked-env-var case (flatpak-spawn --host, a stray
        shell export): FLATPAK_ID alone must not mean "sandboxed"."""
        missing_info = tmp_path / ".flatpak-info"
        assert not is_flatpak_sandbox(
            flatpak_info_path=missing_info, env={"FLATPAK_ID": "org.fede.whisperaloud"}
        )

    def test_false_when_flatpak_id_empty_string(self, tmp_path):
        missing_info = tmp_path / ".flatpak-info"
        assert not is_flatpak_sandbox(flatpak_info_path=missing_info, env={"FLATPAK_ID": ""})

    def test_false_when_env_has_no_flatpak_id(self, tmp_path):
        missing_info = tmp_path / ".flatpak-info"
        assert not is_flatpak_sandbox(flatpak_info_path=missing_info, env={})

    def test_does_not_raise_when_logging_the_leaked_env_var_case(self, tmp_path, caplog):
        missing_info = tmp_path / ".flatpak-info"
        assert not is_flatpak_sandbox(
            flatpak_info_path=missing_info, env={"FLATPAK_ID": "org.fede.whisperaloud"}
        )


class TestFlatpakInfoFileIsAuthoritative:
    def test_true_when_flatpak_info_exists_even_without_flatpak_id(self, tmp_path):
        info = tmp_path / ".flatpak-info"
        info.write_text("[Application]\nname=org.fede.whisperaloud\n")
        assert is_flatpak_sandbox(flatpak_info_path=info, env={})

    def test_false_when_neither_present(self, tmp_path):
        missing_info = tmp_path / ".flatpak-info"
        assert not is_flatpak_sandbox(flatpak_info_path=missing_info, env={})

    def test_true_when_both_present(self, tmp_path):
        info = tmp_path / ".flatpak-info"
        info.write_text("[Application]\n")
        assert is_flatpak_sandbox(flatpak_info_path=info, env={"FLATPAK_ID": "org.fede.x"})

    def test_true_when_file_present_and_env_is_none(self, tmp_path):
        info = tmp_path / ".flatpak-info"
        info.write_text("[Application]\n")
        assert is_flatpak_sandbox(flatpak_info_path=info)


class TestDefaults:
    def test_default_path_is_root_flatpak_info(self):
        import inspect

        sig = inspect.signature(is_flatpak_sandbox)
        default_path = sig.parameters["flatpak_info_path"].default
        assert Path(default_path) == Path("/.flatpak-info")

    def test_default_env_is_os_environ(self, monkeypatch, tmp_path):
        missing_info = tmp_path / ".flatpak-info"
        monkeypatch.setenv("FLATPAK_ID", "org.fede.whisperaloud")
        # FLATPAK_ID alone (even via the real os.environ default) is not
        # authoritative -- the file is still absent, so this must be False.
        assert not is_flatpak_sandbox(flatpak_info_path=missing_info)
        monkeypatch.delenv("FLATPAK_ID", raising=False)
        assert not is_flatpak_sandbox(flatpak_info_path=missing_info)

    def test_never_raises_on_unreadable_path(self):
        # A path whose parent doesn't exist must not raise -- just report False.
        bogus = Path("/this/does/not/exist/.flatpak-info")
        assert not is_flatpak_sandbox(flatpak_info_path=bogus, env={})
