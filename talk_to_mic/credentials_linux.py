"""The Claude API key entered in Settings, kept in the desktop keyring on Linux (the Secret
Service: GNOME Keyring on Cinnamon and GNOME, KWallet on KDE).

ANTHROPIC_API_KEY in the environment still wins over the stored key.
"""

from __future__ import annotations

import logging

import keyring
from keyring.errors import KeyringError, PasswordDeleteError

log = logging.getLogger(__name__)

TARGET = "talk-to-mic/anthropic"
USERNAME = "anthropic"
STORE_NAME = "the system keyring"


def read_api_key(target: str = TARGET) -> str | None:
    try:
        key = keyring.get_password(target, USERNAME)
    except KeyringError as exc:
        log.warning("Could not read the stored API key: %s", exc)
        return None
    return (key or "").strip() or None


def write_api_key(key: str, target: str = TARGET) -> None:
    keyring.set_password(target, USERNAME, key.strip())


def delete_api_key(target: str = TARGET) -> None:
    try:
        keyring.delete_password(target, USERNAME)
    except PasswordDeleteError:
        pass  # nothing stored
