"""The error prometheus raises for what its caller must fix (a module of its own: every other one imports it)."""


class PrometheusError(Exception):
    """Something the caller must fix: no such agent or release, a missing tool, a refused step."""

    def __init__(self, message, details=None):
        super().__init__(message)
        self.details = details or {}

