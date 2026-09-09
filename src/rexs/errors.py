class RexsError(Exception):
    """Base error for invalid input or an unsupported required translation."""


class ConfigurationError(RexsError):
    """Raised when a profile or experiment cannot be loaded."""


class TranslationError(RexsError):
    """Raised when an experiment cannot be translated safely."""
