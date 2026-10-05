"""HIG-01: the app stylesheet must not re-implement Adwaita.

libadwaita (loaded by Adw.Application) owns the look of header bars, buttons,
lists, entries, windows and colours. ui/styles.py may only keep rules for
app-specific widgets that have no libadwaita equivalent, scoped to ``wa-*``
classes, with no literal or GTK3-era (``@theme_*``) colours so light and dark
style both follow Adw.StyleManager.
"""

from __future__ import annotations

import re
from pathlib import Path

from whisper_aloud.ui.styles import APP_CSS

UI_DIR = Path(__file__).resolve().parents[1] / "src" / "whisper_aloud" / "ui"

_CSS = APP_CSS.decode() if isinstance(APP_CSS, bytes) else APP_CSS
_CSS_NO_COMMENTS = re.sub(r"/\*.*?\*/", "", _CSS, flags=re.S)


def _selectors() -> list[str]:
    selectors = []
    for block in re.findall(r"([^{}]+)\{", _CSS_NO_COMMENTS):
        selectors.extend(s.strip() for s in block.split(",") if s.strip())
    return selectors


def _css_classes_defined() -> set[str]:
    return set(re.findall(r"\.(wa-[a-z0-9-]+)", _CSS_NO_COMMENTS))


def _css_classes_used_in_ui() -> set[str]:
    used: set[str] = set()
    for path in UI_DIR.glob("*.py"):
        if path.name == "styles.py":
            continue
        used.update(
            re.findall(r"""add_css_class\(\s*["'](wa-[a-z0-9-]+)["']""", path.read_text())
        )
    return used


def test_no_gtk3_era_theme_colours():
    assert "@theme_" not in _CSS


def test_no_literal_colours():
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b", _CSS_NO_COMMENTS)
    assert not re.search(r"\brgba?\(", _CSS_NO_COMMENTS)
    assert not re.search(r"\b(black|white)\b", _CSS_NO_COMMENTS)


def test_every_selector_is_scoped_to_an_app_class():
    """No rule may target Adwaita-owned nodes (headerbar, button, entry,
    window, paned, list rows...) except through an app-specific wa-* class
    on the node itself."""
    for selector in _selectors():
        assert re.fullmatch(r"[a-z]*\.wa-[a-z0-9-]+", selector), selector


def test_every_app_class_is_both_defined_and_used():
    """No dead CSS rules, and no wa-* marker classes left on widgets without
    a rule (the custom look they selected is gone)."""
    assert _css_classes_defined() == _css_classes_used_in_ui()
