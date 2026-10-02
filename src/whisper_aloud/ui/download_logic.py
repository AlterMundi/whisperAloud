"""Pure state machine for the in-app model download UI (WHISP-68). No GTK/Adw
import -- testable without a display, per this codebase's *_logic.py convention
(mirrors ui/model_picker_logic.py and ui/settings_logic.py).

settings_dialog.py is thin glue over `compute_ui_state()`: it feeds in the
currently selected Model-row entry's downloaded/downloading/progress/error
state and renders whatever `DownloadUiState` comes back (show/hide the
Download button, the progress bar's fraction, the Cancel button, and the
error label) without deciding any of that itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class DownloadUiState:
    """Everything the Settings dialog needs to render the download row for
    the currently selected Model-row entry."""

    button_visible: bool
    button_label: str
    button_sensitive: bool
    progress_visible: bool
    # Fraction in [0.0, 1.0], or None for an indeterminate/pulsing bar
    # (bytes_total unknown, e.g. no Content-Length or a pre-tqdm_class
    # huggingface_hub -- see hf_download.py).
    progress_fraction: Optional[float]
    cancel_visible: bool
    error_visible: bool
    error_text: str


def button_label_for(size_mb: Optional[int]) -> str:
    """"Download (145 MB)", or plain "Download" when size is unknown."""
    return f"Download ({size_mb} MB)" if size_mb is not None else "Download"


def progress_fraction_for(progress: Optional[Tuple[int, int]]) -> Optional[float]:
    """bytes_done / bytes_total, clamped to [0.0, 1.0]; None (indeterminate)
    when progress is absent or bytes_total isn't known yet (0 or negative)."""
    if progress is None:
        return None
    bytes_done, bytes_total = progress
    if bytes_total <= 0:
        return None
    return max(0.0, min(1.0, bytes_done / bytes_total))


def compute_ui_state(
    *,
    downloaded: bool,
    is_downloading: bool,
    size_mb: Optional[int] = None,
    progress: Optional[Tuple[int, int]] = None,
    error: Optional[str] = None,
) -> DownloadUiState:
    """Compute the download row's visual state for the currently selected
    Model-row entry.

    - **Downloading**: Download button hidden, progress bar + Cancel shown.
      `progress_fraction` is `bytes_done / bytes_total` when a total is
      known, else `None` (render an indeterminate/pulsing bar).
    - **Already downloaded, not downloading**: everything hidden -- nothing
      to show (matches the existing not-downloaded notice's own visibility
      rule in model_picker_logic.needs_download_note).
    - **Not downloaded, not downloading**: Download button shown/sensitive.
      `error` (if set, from a previous failed/cancelled attempt for this
      same entry) is shown underneath so the user knows why a prior attempt
      didn't finish, with the button offering an obvious retry.
    """
    if is_downloading:
        return DownloadUiState(
            button_visible=False,
            button_label=button_label_for(size_mb),
            button_sensitive=False,
            progress_visible=True,
            progress_fraction=progress_fraction_for(progress),
            cancel_visible=True,
            error_visible=False,
            error_text="",
        )
    if downloaded:
        return DownloadUiState(
            button_visible=False,
            button_label=button_label_for(size_mb),
            button_sensitive=True,
            progress_visible=False,
            progress_fraction=None,
            cancel_visible=False,
            error_visible=False,
            error_text="",
        )
    return DownloadUiState(
        button_visible=True,
        button_label=button_label_for(size_mb),
        button_sensitive=True,
        progress_visible=False,
        progress_fraction=None,
        cancel_visible=False,
        error_visible=bool(error),
        error_text=error or "",
    )


def should_accept_license(license_notice_visible: bool) -> bool:
    """Whether a Download click should pass accept_license=True to
    DownloadModel(). The Settings dialog already shows a license notice
    (model_picker_logic.is_nonstandard_license) for any selected entry with
    a non-standard or needs_review license *before* the user can click
    Download -- so if that notice is visible, clicking Download is the
    user's explicit acknowledgement of it. A standard-license entry never
    needs this (accept_license is simply ignored by the daemon for
    needs_review=False entries), so False is always safe there."""
    return license_notice_visible
