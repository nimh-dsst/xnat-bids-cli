import getpass


def get_system_username() -> str:
    """Return the current OS-level username.

    Uses :func:`getpass.getuser`, which checks the ``LOGNAME``, ``USER``,
    ``LNAME``, and ``USERNAME`` environment variables (in that order)
    before falling back to the password database, so it works the same
    way on Linux, macOS, and Windows.
    """
    return getpass.getuser()
