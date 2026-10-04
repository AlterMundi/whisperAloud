"""AutostartController: the GUI-side "Start in background at login" logic
(WHISP-87). Pure Python, no GTK, no bus: the portal is a fake that keeps
each request pending until the test answers it."""

from pathlib import Path

import pytest

from whisper_aloud import autostart
from whisper_aloud.background_portal import BackgroundResult, interpret_response
from whisper_aloud.ui.startup_logic import AutostartController


class _FakePortal:
    def __init__(self):
        self.requests = []
        self.closed = False

    def request(self, autostart, reason, on_done, *, previous, parent_window="", commandline=()):
        self.requests.append(
            {"autostart": autostart, "reason": reason, "on_done": on_done, "previous": previous}
        )

    def answer(self, response, background=None, autostart_result=None, which=-1):
        req = self.requests[which]
        results = {}
        if background is not None:
            results["background"] = background
        if autostart_result is not None:
            results["autostart"] = autostart_result
        req["on_done"](
            interpret_response(req["autostart"], response, results, previous=req["previous"])
        )

    def close(self):
        self.closed = True


class _Persist:
    def __init__(self, fail=False):
        self.values = []
        self.fail = fail

    def __call__(self, enabled):
        if self.fail:
            raise RuntimeError("daemon gone and disk read-only")
        self.values.append(enabled)


def _flatpak(initial=False, persist=None):
    portal = _FakePortal()
    persist = persist or _Persist()
    ctl = AutostartController(
        sandboxed=True, initial=initial, persist=persist, portal_factory=lambda: portal
    )
    return ctl, portal, persist


class TestFlatpak:
    def test_initial_state_comes_from_config(self):
        ctl, _portal, _ = _flatpak(initial=True)
        assert ctl.enabled is True
        assert ctl.pending is False
        assert ctl.mode == "portal"

    def test_turning_on_asks_the_portal_and_waits(self):
        ctl, portal, persist = _flatpak()
        seen = []
        ctl.subscribe(lambda c: seen.append((c.enabled, c.pending)))
        assert ctl.set_enabled(True) is True
        assert portal.requests[0]["autostart"] is True
        assert portal.requests[0]["previous"] is False
        assert "background" in portal.requests[0]["reason"].lower()
        assert ctl.pending is True
        assert ctl.enabled is False  # nothing confirmed yet
        assert persist.values == []
        assert seen[-1] == (False, True)

    def test_granted(self):
        ctl, portal, persist = _flatpak()
        ctl.set_enabled(True)
        portal.answer(0, background=True, autostart_result=True)
        assert ctl.enabled is True
        assert ctl.pending is False
        assert persist.values == [True]
        assert ctl.error is False

    def test_denied(self):
        ctl, portal, persist = _flatpak()
        ctl.set_enabled(True)
        portal.answer(0, background=False, autostart_result=False)
        assert ctl.enabled is False
        assert ctl.pending is False
        assert persist.values == [False]
        assert ctl.error is True
        assert "background" in ctl.message.lower()

    def test_cancelled_keeps_the_previous_state(self):
        ctl, portal, persist = _flatpak(initial=True)
        ctl.set_enabled(False)
        portal.answer(1)
        assert ctl.enabled is True
        assert ctl.pending is False
        assert persist.values == [True]

    def test_toggle_off(self):
        ctl, portal, persist = _flatpak(initial=True)
        ctl.set_enabled(False)
        assert portal.requests[0]["autostart"] is False
        portal.answer(0, background=True, autostart_result=False)
        assert ctl.enabled is False
        assert persist.values == [False]
        assert ctl.error is False

    def test_second_request_is_refused_while_one_is_pending(self):
        ctl, portal, _ = _flatpak()
        assert ctl.set_enabled(True) is True
        assert ctl.set_enabled(False) is False
        assert len(portal.requests) == 1

    def test_setting_the_current_value_is_a_no_op(self):
        ctl, portal, _ = _flatpak(initial=True)
        assert ctl.set_enabled(True) is False
        assert portal.requests == []

    def test_late_response_after_the_settings_view_went_away_still_persists(self):
        """The dialog unsubscribes when it closes; the user may answer the
        portal dialog afterwards. The answer must still reach the config."""
        ctl, portal, persist = _flatpak()
        seen = []
        token = ctl.subscribe(lambda c: seen.append(c.enabled))
        ctl.set_enabled(True)
        ctl.unsubscribe(token)
        n = len(seen)
        portal.answer(0, background=True, autostart_result=True)
        assert persist.values == [True]
        assert ctl.enabled is True
        assert len(seen) == n  # the gone view is not called back

    def test_stale_answer_from_a_superseded_request_is_ignored(self):
        ctl, portal, persist = _flatpak()
        ctl.set_enabled(True)
        portal.answer(0, background=True, autostart_result=True)
        ctl.set_enabled(False)
        portal.answer(0, background=True, autostart_result=False)
        # A duplicate/late delivery for the first request must not flip it back.
        portal.answer(0, background=True, autostart_result=True, which=0)
        assert ctl.enabled is False
        assert persist.values == [True, False]

    def test_portal_error(self):
        ctl, portal, persist = _flatpak()
        ctl.set_enabled(True)
        portal.requests[0]["on_done"](BackgroundResult("error", autostart=False, detail="no portal"))
        assert ctl.enabled is False
        assert ctl.pending is False
        assert ctl.error is True
        assert "no portal" in ctl.message

    def test_portal_construction_failure_is_reported(self):
        def _boom():
            raise RuntimeError("no session bus")

        ctl = AutostartController(
            sandboxed=True, initial=False, persist=_Persist(), portal_factory=_boom
        )
        assert ctl.set_enabled(True) is True
        assert ctl.pending is False
        assert ctl.enabled is False
        assert ctl.error is True

    def test_persist_failure_is_reported_but_state_still_reflects_the_portal(self):
        ctl, portal, _ = _flatpak(persist=_Persist(fail=True))
        ctl.set_enabled(True)
        portal.answer(0, background=True, autostart_result=True)
        assert ctl.enabled is True
        assert ctl.error is True
        assert "save" in ctl.message.lower()

    def test_listener_exception_does_not_break_the_flow(self):
        ctl, portal, persist = _flatpak()
        ctl.subscribe(lambda c: (_ for _ in ()).throw(RuntimeError("widget destroyed")))
        ctl.set_enabled(True)
        portal.answer(0, background=True, autostart_result=True)
        assert persist.values == [True]

    def test_close_closes_the_portal(self):
        ctl, portal, _ = _flatpak()
        ctl.set_enabled(True)
        ctl.close()
        assert portal.closed is True


@pytest.fixture
def adir(tmp_path) -> Path:
    return tmp_path / "autostart"


def _native(adir, initial=False, persist=None):
    persist = persist or _Persist()
    ctl = AutostartController(
        sandboxed=False,
        initial=initial,
        persist=persist,
        native_dir=adir,
        native_command="/usr/bin/whisper-aloud",
    )
    return ctl, persist


class TestNative:
    def test_mode(self, adir):
        ctl, _ = _native(adir)
        assert ctl.mode == "native"
        assert ctl.enabled is False

    def test_turning_on_writes_the_entry_and_persists(self, adir):
        ctl, persist = _native(adir)
        assert ctl.set_enabled(True) is True
        assert (adir / autostart.MANAGED_FILENAME).exists()
        assert ctl.enabled is True
        assert ctl.pending is False
        assert persist.values == [True]

    def test_turning_off_removes_it(self, adir):
        ctl, persist = _native(adir)
        ctl.set_enabled(True)
        ctl.set_enabled(False)
        assert not (adir / autostart.MANAGED_FILENAME).exists()
        assert ctl.enabled is False
        assert persist.values == [True, False]

    def test_file_on_disk_wins_over_config_and_is_reconciled(self, adir):
        autostart.enable(adir, command="/usr/bin/whisper-aloud")
        ctl, persist = _native(adir, initial=False)
        assert ctl.enabled is True
        assert persist.values == [True]

    def test_config_says_on_but_file_gone(self, adir):
        ctl, persist = _native(adir, initial=True)
        assert ctl.enabled is False
        assert persist.values == [False]

    def test_no_reconcile_write_when_they_agree(self, adir):
        _ctl, persist = _native(adir, initial=False)
        assert persist.values == []

    def test_other_entries_are_reported(self, adir):
        adir.mkdir(parents=True)
        (adir / "whisperaloud-daemon.desktop").write_text("[Desktop Entry]\nExec=x\n")
        ctl, _ = _native(adir)
        assert "whisperaloud-daemon.desktop" in ctl.message
        ctl.set_enabled(True)
        assert "whisperaloud-daemon.desktop" in ctl.message
        ctl.set_enabled(False)
        assert "whisperaloud-daemon.desktop" in ctl.message
        assert ctl.error is True  # off, but something still starts it at login

    def test_write_failure_keeps_state_and_reports(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("not a directory")
        ctl, persist = _native(blocker / "autostart")
        assert ctl.set_enabled(True) is True
        assert ctl.enabled is False
        assert ctl.error is True
        assert persist.values == []
