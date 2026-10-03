"""Unit tests for ui/download_logic.py (WHISP-68)."""
from __future__ import annotations

from dataclasses import dataclass

from whisper_aloud.ui import download_logic as dl


@dataclass(frozen=True)
class _FakeEntry:
    """Minimal stand-in for stt.catalog.ModelEntry -- only the fields
    requires_license_consent() actually reads."""

    license: str = "MIT"
    needs_review: bool = False


def test_button_label_with_known_size():
    assert dl.button_label_for(145) == "Download (145 MB)"


def test_button_label_with_unknown_size():
    assert dl.button_label_for(None) == "Download"


def test_progress_fraction_known_total():
    assert dl.progress_fraction_for((50, 100)) == 0.5


def test_progress_fraction_clamped():
    assert dl.progress_fraction_for((150, 100)) == 1.0
    assert dl.progress_fraction_for((-10, 100)) == 0.0


def test_progress_fraction_unknown_total_is_indeterminate():
    assert dl.progress_fraction_for((10, 0)) is None
    assert dl.progress_fraction_for(None) is None


def test_downloading_state_hides_button_shows_progress_and_cancel():
    state = dl.compute_ui_state(
        downloaded=False, is_downloading=True, size_mb=671, progress=(300, 671_000_000)
    )
    assert state.button_visible is False
    assert state.progress_visible is True
    assert state.cancel_visible is True
    assert state.error_visible is False
    assert state.progress_fraction == 300 / 671_000_000


def test_downloading_state_without_total_is_indeterminate():
    state = dl.compute_ui_state(downloaded=False, is_downloading=True, progress=(10, 0))
    assert state.progress_fraction is None
    assert state.progress_visible is True


def test_already_downloaded_state_hides_everything():
    state = dl.compute_ui_state(downloaded=True, is_downloading=False, size_mb=145)
    assert state.button_visible is False
    assert state.progress_visible is False
    assert state.cancel_visible is False
    assert state.error_visible is False


def test_not_downloaded_state_shows_button():
    state = dl.compute_ui_state(downloaded=False, is_downloading=False, size_mb=75)
    assert state.button_visible is True
    assert state.button_sensitive is True
    assert state.button_label == "Download (75 MB)"
    assert state.progress_visible is False
    assert state.error_visible is False


def test_not_downloaded_state_with_error_shows_error_and_retry_button():
    state = dl.compute_ui_state(
        downloaded=False, is_downloading=False, error="network exploded"
    )
    assert state.button_visible is True
    assert state.error_visible is True
    assert state.error_text == "network exploded"


def test_requires_license_consent_false_for_standard_license():
    assert dl.requires_license_consent(_FakeEntry(license="MIT", needs_review=False)) is False
    assert dl.requires_license_consent(_FakeEntry(license="Apache-2.0")) is False
    assert dl.requires_license_consent(_FakeEntry(license="CC-BY-4.0")) is False
    assert dl.requires_license_consent(_FakeEntry(license="BSD-3-Clause")) is False


def test_requires_license_consent_true_for_nonstandard_license():
    assert dl.requires_license_consent(_FakeEntry(license="OpenMDW-1.1")) is True
    assert dl.requires_license_consent(_FakeEntry(license="GPL-3.0")) is True


def test_requires_license_consent_true_for_needs_review_even_with_standard_license():
    """needs_review alone is enough, independent of the license string --
    the pending-review status itself is what needs a human decision."""
    assert dl.requires_license_consent(_FakeEntry(license="MIT", needs_review=True)) is True


def test_requires_license_consent_true_for_real_nemotron_entry_despite_review_completed():
    """WHISP-86: nemotron-streaming's OpenMDW-1.1 license review completed
    (needs_review=False), but its license is still non-standard for this app, so
    the consent dialog must still appear before a download starts."""
    from whisper_aloud.stt import catalog

    entry = catalog.get("nemotron-streaming", "nemotron-3.5-streaming-560ms")
    assert entry.needs_review is False
    assert dl.requires_license_consent(entry) is True
