"""Errors that stop a run."""


class InputError(Exception):
    """Bad manifest, bad library or unknown client: exit 2, message on stderr, nothing on stdout."""
