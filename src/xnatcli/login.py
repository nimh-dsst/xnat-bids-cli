import argparse
import configparser
import getpass
import os
import sys
from pathlib import Path

from pyxnat import Interface

CONFIG_DIR = Path.home() / ".xnatcli"
CONFIG_PATH = CONFIG_DIR / "credentials.cfg"


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


def _write_credentials(server: str, username: str, password: str) -> None:
    config = configparser.ConfigParser()
    config["xnatcli"] = {
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
