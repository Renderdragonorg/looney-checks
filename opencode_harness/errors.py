class OpenCodeError(Exception):
    """Raised when opencode itself reports a failure (non-zero exit, HTTP error, etc.)."""


class HarnessTimeout(OpenCodeError):
    """Raised when an operation exceeds its configured timeout."""
