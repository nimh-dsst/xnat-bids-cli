import argparse
import configparser
import getpass
import os
import sys
from pathlib import Path

from pyxnat import Interface

CONFIG_DIR = Path.home() / ".xnatbidscli"
CONFIG_PATH = CONFIG_DIR / "credentials.cfg"

# Pre-rename location (the package was named "xnatcli" before the 1.x rename
# to "xnatbidscli"); migrated automatically the first time credentials are read.
_LEGACY_CONFIG_DIR = Path.home() / ".xnatcli"
_LEGACY_CONFIG_PATH = _LEGACY_CONFIG_DIR / "credentials.cfg"
_LEGACY_SECTION = "xnatcli"


def _prompt(label: str) -> str:
    value = input(f"{label}: ").strip()
    if not value:
        sys.exit(f"Error: {label.lower()} is required.")
    return value


def _verify(server: str, username: str, password: str) -> None:
    interface = None
    try:
        interface = Interface(server=server, user=username, password=password)
        # Force a real authenticated request so bad creds/URLs fail here.
        interface.select.projects().get()
    except Exception as e:
        msg = str(e)
        lowered = msg.lower()
        if "401" in lowered or "unauthorized" in lowered or "authentication" in lowered:
            sys.exit(
                f"Error: authentication failed for user '{username}' on {server}. "
                "Check the username and password and try again."
            )
        if (
            "connection" in lowered
            or "timeout" in lowered
            or "not found" in lowered
            or "name or service" in lowered
            or "resolve" in lowered
        ):
            sys.exit(
                f"Error: could not reach {server}. Check the URL and your network connection."
            )
        sys.exit(f"Error: could not verify credentials on {server}: {msg}")
    finally:
        if interface is not None:
            try:
                interface.disconnect()
            except Exception:
                pass


def _migrate_legacy_credentials() -> None:
    """Move a pre-rename ``~/.xnatcli/credentials.cfg`` to the new location.

    No-op if the new config already exists, the legacy file doesn't, or the
    legacy file is malformed. Reuses ``_write_credentials`` so the migrated
    file gets the same ``0o600`` permissions as a freshly written one.
    """
    if CONFIG_PATH.exists() or not _LEGACY_CONFIG_PATH.exists():
        return
    legacy = configparser.ConfigParser()
    legacy.read(_LEGACY_CONFIG_PATH)
    if _LEGACY_SECTION not in legacy:
        return
    section = legacy[_LEGACY_SECTION]
    server, username, password = (
        section.get("server"),
        section.get("username"),
        section.get("password"),
    )
    if not (server and username and password):
        return

    _write_credentials(server, username, password)
    _LEGACY_CONFIG_PATH.unlink()
    try:
        _LEGACY_CONFIG_DIR.rmdir()
    except OSError:
        pass  # directory has other files; leave it
    print(f"Migrated credentials from {_LEGACY_CONFIG_PATH} to {CONFIG_PATH}")


def load_credentials() -> tuple[str, str, str]:
    _migrate_legacy_credentials()
    if not CONFIG_PATH.exists():
        sys.exit(
            f"Error: no credentials found at {CONFIG_PATH}. "
            "Run 'xnatbidscli login' first."
        )
    config = configparser.ConfigParser()
    config.read(CONFIG_PATH)
    if "xnatbidscli" not in config:
        sys.exit(
            f"Error: {CONFIG_PATH} is missing the [xnatbidscli] section. "
            "Run 'xnatbidscli login' again."
        )
    section = config["xnatbidscli"]
    for key in ("server", "username", "password"):
        if not section.get(key):
            sys.exit(
                f"Error: {CONFIG_PATH} is missing '{key}'. "
                "Run 'xnatbidscli login' again."
            )
    return section["server"], section["username"], section["password"]


def _write_credentials(server: str, username: str, password: str) -> None:
    config = configparser.ConfigParser()
    config["xnatbidscli"] = {
        "server": server,
        "username": username,
        "password": password,
    }

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(CONFIG_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        config.write(f)
    # os.open's mode is masked by umask; force 0o600 explicitly.
    os.chmod(CONFIG_PATH, 0o600)


def login_cmd(args: argparse.Namespace) -> int:
    server = _prompt("XNAT server URL").rstrip("/")
    username = _prompt("Username")
    password = getpass.getpass("Password: ")
    if not password:
        sys.exit("Error: password is required.")

    _verify(server, username, password)
    _write_credentials(server, username, password)

    print(f"Credentials verified and saved to {CONFIG_PATH}")
    return 0
