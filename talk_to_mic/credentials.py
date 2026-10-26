"""The Claude API key entered in Settings, kept in Windows Credential Manager.

ANTHROPIC_API_KEY in the environment still wins over the stored key.
"""

from __future__ import annotations

import logging

import pywintypes
import win32cred

log = logging.getLogger(__name__)

TARGET = "talk-to-mic/anthropic"
STORE_NAME = "Windows Credential Manager"
ERROR_NOT_FOUND = 1168


def read_api_key(target: str = TARGET) -> str | None:
    try:
        cred = win32cred.CredRead(target, win32cred.CRED_TYPE_GENERIC)
    except pywintypes.error as exc:
        if exc.winerror != ERROR_NOT_FOUND:
            log.warning("Could not read the stored API key: %s", exc.strerror)
        return None
    blob = cred["CredentialBlob"] or b""
    key = blob.decode("utf-16-le") if isinstance(blob, bytes) else str(blob)
    return key.strip() or None


def write_api_key(key: str, target: str = TARGET) -> None:
    win32cred.CredWrite(
        {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": target,
            "UserName": "anthropic",
            "CredentialBlob": key.strip(),
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            "Comment": "Claude API key used by talk-to-mic",
        },
        0,
    )


def delete_api_key(target: str = TARGET) -> None:
    try:
        win32cred.CredDelete(target, win32cred.CRED_TYPE_GENERIC)
    except pywintypes.error as exc:
        if exc.winerror != ERROR_NOT_FOUND:
            raise
