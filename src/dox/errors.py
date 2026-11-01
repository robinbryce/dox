"""Exceptions that map to dox's exit codes (ADR-0001 section 7)."""


class DoxError(Exception):
    exit_code = 1


class ConfigError(DoxError):
    """Usage or configuration error: exit 2."""

    exit_code = 2


class RemoteError(DoxError):
    """A remote source could not be reached: exit 3."""

    exit_code = 3
