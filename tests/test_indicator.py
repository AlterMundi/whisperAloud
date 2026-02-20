"""Tests for WhisperAloud system tray indicator."""
from unittest.mock import MagicMock, patch


class TestIndicatorCreation:
    """Tests for indicator creation and availability."""

    def test_indicator_available_with_ayatana(self):
        """Indicator should be available when AyatanaAppIndicator3 is present."""
        mock_gtk = MagicMock()
        mock_ai3 = MagicMock()
        mock_indicator_instance = MagicMock()
        mock_ai3.Indicator.new.return_value = mock_indicator_instance
        mock_ai3.IndicatorCategory.APPLICATION_STATUS = 0
        mock_ai3.IndicatorStatus.ACTIVE = 1

        with patch('whisper_aloud.service.indicator.HAS_INDICATOR', True), \
             patch('whisper_aloud.service.indicator.AyatanaAppIndicator3', mock_ai3), \
             patch('whisper_aloud.service.indicator.Gtk', mock_gtk):
            from whisper_aloud.service.indicator import WhisperAloudIndicator
            indicator = WhisperAloudIndicator(on_toggle=lambda: None)
            assert indicator.available is True

    def test_indicator_unavailable_graceful(self):
        """Indicator should gracefully degrade when Ayatana not present."""
        with patch('whisper_aloud.service.indicator.HAS_INDICATOR', False):
            from whisper_aloud.service.indicator import WhisperAloudIndicator
            indicator = WhisperAloudIndicator(on_toggle=lambda: None)
            assert indicator.available is False
            # These should not raise
            indicator.set_state("recording")
            indicator.set_last_text("hello")


class TestIndicatorState:
    """Tests for indicator state management."""

    def test_set_state_changes_icon(self):
        """set_state should update the indicator icon."""
        mock_ai3 = MagicMock()
        mock_indicator_instance = MagicMock()
        mock_ai3.Indicator.new.return_value = mock_indicator_instance
        mock_ai3.IndicatorCategory.APPLICATION_STATUS = 0
        mock_ai3.IndicatorStatus.ACTIVE = 1

        with patch('whisper_aloud.service.indicator.HAS_INDICATOR', True), \
             patch('whisper_aloud.service.indicator.AyatanaAppIndicator3', mock_ai3), \
             patch('whisper_aloud.service.indicator.Gtk', MagicMock()):
            from whisper_aloud.service.indicator import WhisperAloudIndicator
            indicator = WhisperAloudIndicator(on_toggle=lambda: None)
            indicator.set_state("recording")
            mock_indicator_instance.set_icon_full.assert_called_with(
                "media-record-symbolic", "WhisperAloud: recording"
            )

    def test_set_state_unknown_falls_back_to_idle(self):
        """set_state with unknown state should fall back to idle icon."""
        mock_ai3 = MagicMock()
        mock_indicator_instance = MagicMock()
        mock_ai3.Indicator.new.return_value = mock_indicator_instance
        mock_ai3.IndicatorCategory.APPLICATION_STATUS = 0
        mock_ai3.IndicatorStatus.ACTIVE = 1

        with patch('whisper_aloud.service.indicator.HAS_INDICATOR', True), \
             patch('whisper_aloud.service.indicator.AyatanaAppIndicator3', mock_ai3), \
             patch('whisper_aloud.service.indicator.Gtk', MagicMock()):
            from whisper_aloud.service.indicator import WhisperAloudIndicator
            indicator = WhisperAloudIndicator(on_toggle=lambda: None)
            indicator.set_state("unknown_state")
            mock_indicator_instance.set_icon_full.assert_called_with(
                "audio-input-microphone-symbolic", "WhisperAloud: unknown_state"
            )

    def test_set_last_text_truncates(self):
        """set_last_text should truncate long text."""
        mock_ai3 = MagicMock()
        mock_indicator_instance = MagicMock()
        mock_ai3.Indicator.new.return_value = mock_indicator_instance
        mock_ai3.IndicatorCategory.APPLICATION_STATUS = 0
        mock_ai3.IndicatorStatus.ACTIVE = 1

        with patch('whisper_aloud.service.indicator.HAS_INDICATOR', True), \
             patch('whisper_aloud.service.indicator.AyatanaAppIndicator3', mock_ai3), \
             patch('whisper_aloud.service.indicator.Gtk', MagicMock()):
            from whisper_aloud.service.indicator import WhisperAloudIndicator
            indicator = WhisperAloudIndicator(on_toggle=lambda: None)
            long_text = "A" * 100
            indicator.set_last_text(long_text)
            label = indicator._last_text_item.set_label.call_args[0][0]
            assert "..." in label
            assert len(label) < 70  # "Last: " + 50 chars + "..."

    def test_set_last_text_short_no_truncation(self):
        """set_last_text should not truncate short text."""
        mock_ai3 = MagicMock()
        mock_indicator_instance = MagicMock()
        mock_ai3.Indicator.new.return_value = mock_indicator_instance
        mock_ai3.IndicatorCategory.APPLICATION_STATUS = 0
        mock_ai3.IndicatorStatus.ACTIVE = 1

        with patch('whisper_aloud.service.indicator.HAS_INDICATOR', True), \
             patch('whisper_aloud.service.indicator.AyatanaAppIndicator3', mock_ai3), \
             patch('whisper_aloud.service.indicator.Gtk', MagicMock()):
            from whisper_aloud.service.indicator import WhisperAloudIndicator
            indicator = WhisperAloudIndicator(on_toggle=lambda: None)
            indicator.set_last_text("hello world")
            label = indicator._last_text_item.set_label.call_args[0][0]
            assert "..." not in label
            assert "hello world" in label


class TestIndicatorMenu:
    """Tests for context menu."""

    def test_menu_has_toggle_item(self):
        """Menu should have Toggle Recording item."""
        mock_ai3 = MagicMock()
        mock_indicator_instance = MagicMock()
        mock_ai3.Indicator.new.return_value = mock_indicator_instance
        mock_ai3.IndicatorCategory.APPLICATION_STATUS = 0
        mock_ai3.IndicatorStatus.ACTIVE = 1
        mock_gtk = MagicMock()

        with patch('whisper_aloud.service.indicator.HAS_INDICATOR', True), \
             patch('whisper_aloud.service.indicator.AyatanaAppIndicator3', mock_ai3), \
             patch('whisper_aloud.service.indicator.Gtk', mock_gtk):
            from whisper_aloud.service.indicator import WhisperAloudIndicator
            WhisperAloudIndicator(
                on_toggle=lambda: None,
                on_quit=lambda: None,
            )
            # Menu should have been created and set
            mock_indicator_instance.set_menu.assert_called_once()

    def test_toggle_callback_fires(self):
        """Clicking toggle should call the on_toggle callback."""
        toggle_called = []
        mock_ai3 = MagicMock()
        mock_indicator_instance = MagicMock()
        mock_ai3.Indicator.new.return_value = mock_indicator_instance
        mock_ai3.IndicatorCategory.APPLICATION_STATUS = 0
        mock_ai3.IndicatorStatus.ACTIVE = 1

        mock_gtk = MagicMock()
        # Capture MenuItem instances to test callbacks
        menu_items = []

        def fake_menu_item(label=""):
            item = MagicMock()
            item._label = label
            item._callback = None

            def capture_connect(signal, callback):
                item._callback = callback
            item.connect = capture_connect
            menu_items.append(item)
            return item

        mock_gtk.MenuItem = fake_menu_item
        mock_gtk.Menu.return_value = MagicMock()
        mock_gtk.SeparatorMenuItem.return_value = MagicMock()

        with patch('whisper_aloud.service.indicator.HAS_INDICATOR', True), \
             patch('whisper_aloud.service.indicator.AyatanaAppIndicator3', mock_ai3), \
             patch('whisper_aloud.service.indicator.Gtk', mock_gtk):
            from whisper_aloud.service.indicator import WhisperAloudIndicator
            WhisperAloudIndicator(
                on_toggle=lambda: toggle_called.append(True),
                on_quit=lambda: None,
            )
            # Find the toggle item and fire its callback
            toggle_item = menu_items[0]  # First item is Toggle
            assert toggle_item._label == "Toggle Recording"
            toggle_item._callback(None)
            assert len(toggle_called) == 1

    def test_quit_callback_fires(self):
        """Clicking quit should call the on_quit callback."""
        quit_called = []
        mock_ai3 = MagicMock()
        mock_indicator_instance = MagicMock()
        mock_ai3.Indicator.new.return_value = mock_indicator_instance
        mock_ai3.IndicatorCategory.APPLICATION_STATUS = 0
        mock_ai3.IndicatorStatus.ACTIVE = 1

        mock_gtk = MagicMock()
        menu_items = []

        def fake_menu_item(label=""):
            item = MagicMock()
            item._label = label
            item._callback = None

            def capture_connect(signal, callback):
                item._callback = callback
            item.connect = capture_connect
            menu_items.append(item)
            return item

        mock_gtk.MenuItem = fake_menu_item
        mock_gtk.Menu.return_value = MagicMock()
        mock_gtk.SeparatorMenuItem.return_value = MagicMock()

        with patch('whisper_aloud.service.indicator.HAS_INDICATOR', True), \
             patch('whisper_aloud.service.indicator.AyatanaAppIndicator3', mock_ai3), \
             patch('whisper_aloud.service.indicator.Gtk', mock_gtk):
            from whisper_aloud.service.indicator import WhisperAloudIndicator
            WhisperAloudIndicator(
                on_toggle=lambda: None,
                on_quit=lambda: quit_called.append(True),
            )
            # Find the quit item (last MenuItem with a callback)
            quit_item = [i for i in menu_items if i._label == "Quit"][0]
            quit_item._callback(None)
            assert len(quit_called) == 1
