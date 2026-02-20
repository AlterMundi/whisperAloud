"""Tests for history UI components."""

import pytest
from unittest.mock import MagicMock, patch
from datetime import datetime
import os

_ui_tests_enabled = (
    os.environ.get("WHISPERALOUD_RUN_GTK_UI_TESTS") == "1"
    and bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
)

if not _ui_tests_enabled:
    pytestmark = [
        pytest.mark.requires_display,
        pytest.mark.skip(
            reason=(
                "GTK UI tests are opt-in and require display "
                "(set WHISPERALOUD_RUN_GTK_UI_TESTS=1 with DISPLAY/WAYLAND_DISPLAY)"
            )
        ),
    ]
else:
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk

    if not Gtk.init_check():
        pytestmark = [
            pytest.mark.requires_display,
            pytest.mark.skip(reason="GTK4 initialization failed in test environment"),
        ]
    else:
        pytestmark = pytest.mark.requires_display
        from whisper_aloud.ui.history_item import HistoryItem
        from whisper_aloud.ui.history_panel import HistoryPanel
        from whisper_aloud.persistence.models import HistoryEntry


@pytest.fixture
def sample_entry():
    """Sample HistoryEntry for testing."""
    return HistoryEntry(
        id=1,
        text="Test transcription",
        language="en",
        confidence=0.95,
        duration=5.2,
        processing_time=1.3,
        segments=[{"text": "Test", "start": 0.0, "end": 5.2}],
        timestamp=datetime.now(),
        favorite=False
    )


@pytest.fixture
def mock_history_manager():
    """Mock HistoryManager."""
    manager = MagicMock()
    manager.get_recent.return_value = []
    manager.search.return_value = []
    manager.get_favorites.return_value = []
    return manager


class TestHistoryItem:
    """Test HistoryItem widget."""

    def test_init(self, sample_entry):
        """Test initialization."""
        item = HistoryItem(sample_entry)
        assert item.entry == sample_entry
        
        # Check if favorite button state matches entry
        # Note: Accessing internal children is tricky in GTK4 tests without full introspection
        # but we can verify the object was created without error

    def test_favorite_toggle(self, sample_entry):
        """Test favorite toggle signal."""
        item = HistoryItem(sample_entry)
        
        # Mock signal handler
        handler = MagicMock()
        item.connect("favorite-toggled", handler)
        
        # Simulate toggle (programmatically)
        # In a real UI test we'd click the button, but here we can just emit or call the handler
        # Since we can't easily click the button in unit test, we'll verify the logic
        
        # Manually trigger the internal handler to verify logic
        # This is a bit hacky but verifies the signal emission logic
        button = MagicMock()
        button.get_active.return_value = True
        item._on_favorite_toggled(button)
        
        assert item.entry.favorite is True
        handler.assert_called_once_with(item, sample_entry.id)


class TestHistoryPanel:
    """Test HistoryPanel widget."""

    def test_init(self, mock_history_manager):
        """Test initialization."""
        panel = HistoryPanel(mock_history_manager)
        assert panel.history_manager == mock_history_manager
        
        # Verify initial load was called
        mock_history_manager.get_recent.assert_called()

    def test_search_trigger(self, mock_history_manager):
        """Test search triggering."""
        panel = HistoryPanel(mock_history_manager)
        
        # Simulate search entry
        panel._trigger_search("test query")
        
        # Verify search was called in background (we can't easily verify thread execution here)
        # But we can verify the method exists and runs without error
        
    def test_group_by_date(self, mock_history_manager, sample_entry):
        """Test date grouping logic."""
        panel = HistoryPanel(mock_history_manager)
        
        entries = [sample_entry]
        grouped = panel._group_by_date(entries)
        
        assert "Today" in grouped
        assert len(grouped["Today"]) == 1
        assert grouped["Today"][0] == sample_entry

    def test_filter_toggle(self, mock_history_manager):
        """Test filter toggle."""
        panel = HistoryPanel(mock_history_manager)
        
        button = MagicMock()
        button.get_active.return_value = True
        
        panel._on_filter_toggled(button)
        assert panel._show_favorites_only is True
        
        # Should trigger refresh
        # Since it runs in thread, we can't easily assert the manager call immediately
