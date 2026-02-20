"""Validation helper functions for configuration sanitization."""

from typing import Optional


def sanitize_language_code(language: str) -> Optional[str]:
    """Sanitize language code to valid ISO code or None if invalid.

    Args:
        language: Language code string to validate

    Returns:
        Valid lowercase language code ("auto" or 2-letter ISO) or None if invalid
    """
    if not isinstance(language, str):
        return None

    language = language.strip().lower()

    if language == "auto":
        return "auto"

    # Must be exactly 2 alphabetic characters
    if len(language) == 2 and language.isalpha():
        return language

    return None
