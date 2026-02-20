"""Tests for validation helper functions."""


from whisper_aloud.utils.validation_helpers import sanitize_language_code


class TestSanitizeLanguageCode:
    """Test sanitize_language_code function."""

    def test_sanitize_language_code_valid_passes(self):
        """Test that valid 2-letter codes are returned unchanged."""
        assert sanitize_language_code("en") == "en"
        assert sanitize_language_code("ES") == "es"  # Should lowercase
        assert sanitize_language_code("fr") == "fr"
        assert sanitize_language_code("  de  ") == "de"  # Should strip
        assert sanitize_language_code("auto") == "auto"

    def test_sanitize_language_code_invalid_returns_none(self):
        """Test that invalid codes return None."""
        assert sanitize_language_code("english") is None  # Too long
        assert sanitize_language_code("e") is None  # Too short
        assert sanitize_language_code("") is None  # Empty
        assert sanitize_language_code(" ") is None  # Whitespace only

    def test_sanitize_language_code_empty_returns_none(self):
        """Test that empty/None inputs return None."""
        assert sanitize_language_code("") is None
        assert sanitize_language_code("   ") is None
        assert sanitize_language_code(None) is None

    def test_sanitize_language_code_three_letter_invalid(self):
        """Test that 3-letter codes are rejected."""
        assert sanitize_language_code("eng") is None
        assert sanitize_language_code("spa") is None

    def test_sanitize_language_code_numeric_invalid(self):
        """Test that numeric codes are rejected."""
        assert sanitize_language_code("12") is None
        assert sanitize_language_code("1") is None
        assert sanitize_language_code("123") is None

    def test_sanitize_language_code_special_chars_invalid(self):
        """Test that codes with special characters are rejected."""
        assert sanitize_language_code("e!") is None
        assert sanitize_language_code("en-") is None
        assert sanitize_language_code("e n") is None
