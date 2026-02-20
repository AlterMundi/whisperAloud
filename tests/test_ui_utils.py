"""Tests for UI utilities."""


from whisper_aloud.ui.utils import (
    AppState,
    format_confidence,
    format_duration,
    format_file_size,
)


class TestAppState:
    """Test application state enum."""

    def test_all_states_exist(self):
        """Test that all expected states are defined."""
        assert hasattr(AppState, 'IDLE')
        assert hasattr(AppState, 'RECORDING')
        assert hasattr(AppState, 'TRANSCRIBING')
        assert hasattr(AppState, 'READY')
        assert hasattr(AppState, 'ERROR')

    def test_state_values(self):
        """Test state enum values."""
        assert AppState.IDLE.value == "idle"
        assert AppState.RECORDING.value == "recording"
        assert AppState.TRANSCRIBING.value == "transcribing"
        assert AppState.READY.value == "ready"
        assert AppState.ERROR.value == "error"


class TestFormatDuration:
    """Test duration formatting."""

    def test_zero_seconds(self):
        """Test formatting zero seconds."""
        assert format_duration(0) == "0:00"

    def test_seconds_only(self):
        """Test formatting seconds only."""
        assert format_duration(5) == "0:05"
        assert format_duration(45) == "0:45"

    def test_minutes_and_seconds(self):
        """Test formatting minutes and seconds."""
        assert format_duration(65) == "1:05"
        assert format_duration(125) == "2:05"
        assert format_duration(599) == "9:59"

    def test_hours_minutes_seconds(self):
        """Test formatting with hours."""
        assert format_duration(3661) == "1:01:01"
        assert format_duration(7200) == "2:00:00"
        assert format_duration(3723) == "1:02:03"

    def test_large_duration(self):
        """Test formatting very large durations."""
        assert format_duration(36000) == "10:00:00"

    def test_fractional_seconds(self):
        """Test that fractional seconds are truncated."""
        assert format_duration(65.9) == "1:05"
        assert format_duration(125.1) == "2:05"


class TestFormatConfidence:
    """Test confidence formatting."""

    def test_perfect_confidence(self):
        """Test formatting 100% confidence."""
        assert format_confidence(1.0) == "100%"

    def test_high_confidence(self):
        """Test formatting high confidence."""
        assert format_confidence(0.95) == "95%"
        assert format_confidence(0.9) == "90%"

    def test_medium_confidence(self):
        """Test formatting medium confidence."""
        assert format_confidence(0.75) == "75%"
        assert format_confidence(0.5) == "50%"

    def test_low_confidence(self):
        """Test formatting low confidence."""
        assert format_confidence(0.25) == "25%"
        assert format_confidence(0.1) == "10%"

    def test_zero_confidence(self):
        """Test formatting zero confidence."""
        assert format_confidence(0.0) == "0%"

    def test_fractional_rounding(self):
        """Test that fractional percentages are rounded down."""
        assert format_confidence(0.8912) == "89%"
        assert format_confidence(0.8999) == "89%"
        assert format_confidence(0.7555) == "75%"


class TestFormatFileSize:
    """Test file size formatting."""

    def test_bytes(self):
        """Test formatting bytes."""
        assert format_file_size(0) == "0.0 B"
        assert format_file_size(512) == "512.0 B"
        assert format_file_size(1023) == "1023.0 B"

    def test_kilobytes(self):
        """Test formatting kilobytes."""
        assert format_file_size(1024) == "1.0 KB"
        assert format_file_size(2048) == "2.0 KB"
        assert format_file_size(1536) == "1.5 KB"

    def test_megabytes(self):
        """Test formatting megabytes."""
        assert format_file_size(1024 * 1024) == "1.0 MB"
        assert format_file_size(1572864) == "1.5 MB"
        assert format_file_size(5242880) == "5.0 MB"

    def test_gigabytes(self):
        """Test formatting gigabytes."""
        assert format_file_size(1024 * 1024 * 1024) == "1.0 GB"
        assert format_file_size(1610612736) == "1.5 GB"

    def test_terabytes(self):
        """Test formatting terabytes."""
        assert format_file_size(1024 * 1024 * 1024 * 1024) == "1.0 TB"
