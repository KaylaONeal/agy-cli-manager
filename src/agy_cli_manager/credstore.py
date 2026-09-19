"""OS credential-store access for the live Antigravity CLI credential.

Recent `agy` releases no longer keep the OAuth credential in
``~/.gemini/antigravity-cli/antigravity-oauth-token``.  They store it in the
native OS credential store through go-keyring:

    Linux    Secret Service over D-Bus   (service=gemini, username=antigravity)
    macOS    Keychain generic password   (service=gemini, account=antigravity)

The stored value is the exact same JSON document the token file used to hold,
so the manager keeps the token file as its on-disk profile format and uses this
module only to move that document between a profile and the live keyring slot.
"""

from __future__ import annotations

import base64
import binascii
import os
import shutil
import subprocess
import sys

DEFAULT_SERVICE = "gemini"
DEFAULT_USERNAME = "antigravity"

# go-keyring base64-encodes values that are not valid UTF-8 and marks them with
# this prefix.  Reads must strip it; writes emit plain text, which go-keyring
# returns unchanged.
GO_KEYRING_BASE64_PREFIX = "go-keyring-base64:"

BACKEND_NONE = "none"
BACKEND_SECRET_SERVICE = "secret-service"
BACKEND_KEYCHAIN = "keychain"

_SUBPROCESS_TIMEOUT = 20


class CredentialStoreError(RuntimeError):
    """The credential store exists but the requested operation failed."""


def keyring_service() -> str:
    return os.getenv("AGY_KEYRING_SERVICE", "").strip() or DEFAULT_SERVICE


def keyring_username() -> str:
    return os.getenv("AGY_KEYRING_USERNAME", "").strip() or DEFAULT_USERNAME


def _forced_backend() -> str | None:
    forced = os.getenv("AGY_CREDENTIAL_BACKEND", "").strip().lower()
    if not forced or forced == "auto":
        return None
    if forced in {BACKEND_NONE, "file", "off", "disabled"}:
        return BACKEND_NONE
    if forced in {BACKEND_SECRET_SERVICE, "secretservice", "libsecret", "linux"}:
        return BACKEND_SECRET_SERVICE
    if forced in {BACKEND_KEYCHAIN, "macos", "darwin"}:
        return BACKEND_KEYCHAIN
    raise CredentialStoreError(
        f"Unknown AGY_CREDENTIAL_BACKEND value: {forced!r} "
        f"(use auto, {BACKEND_SECRET_SERVICE}, {BACKEND_KEYCHAIN}, or {BACKEND_NONE})"
    )


def _dbus_env() -> dict:
    """Environment for secret-tool, with a session-bus fallback.

    Daemonised callers (``agy-cli-manager watch`` under systemd, cron) often
    inherit no DBUS_SESSION_BUS_ADDRESS.  The well-known per-user socket is the
    right default when it exists.
    """
    env = os.environ.copy()
    if env.get("DBUS_SESSION_BUS_ADDRESS", "").strip():
        return env
    socket_path = f"/run/user/{os.getuid()}/bus"
    if os.path.exists(socket_path):
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={socket_path}"
    return env


def backend_name() -> str:
    forced = _forced_backend()
    if forced is not None:
        return forced
    if sys.platform == "darwin":
        return BACKEND_KEYCHAIN if shutil.which("security") else BACKEND_NONE
    if sys.platform.startswith("linux"):
        if not shutil.which("secret-tool"):
            return BACKEND_NONE
        env = _dbus_env()
        if not env.get("DBUS_SESSION_BUS_ADDRESS", "").strip():
            return BACKEND_NONE
        return BACKEND_SECRET_SERVICE
    return BACKEND_NONE


def is_available() -> bool:
    return backend_name() != BACKEND_NONE


def describe() -> str:
    backend = backend_name()
    if backend == BACKEND_NONE:
        return "none (token-file profiles)"
    return f"{backend} ({keyring_service()}/{keyring_username()})"


def unavailable_hint() -> str:
    if sys.platform.startswith("linux"):
        if not shutil.which("secret-tool"):
            return (
                "secret-tool was not found. Install libsecret "
                "(Arch/Omarchy: sudo pacman -S libsecret, Debian/Ubuntu: "
                "sudo apt install libsecret-tools)."
            )
        return (
            "No D-Bus session bus is reachable, so the Secret Service is "
            "unavailable. Run from inside your desktop session, or set "
            "DBUS_SESSION_BUS_ADDRESS."
        )
    if sys.platform == "darwin":
        return "The macOS `security` command was not found."
    return f"No supported OS credential store on platform {sys.platform}."


def _decode(value: str) -> str:
    if value.startswith(GO_KEYRING_BASE64_PREFIX):
        encoded = value[len(GO_KEYRING_BASE64_PREFIX):]
        try:
            return base64.b64decode(encoded).decode("utf-8")
        except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
            raise CredentialStoreError(
                "Stored credential is base64-tagged but could not be decoded."
            ) from exc
    return value


def _run(args: list[str], *, env: dict | None = None, stdin: str | None = None):
    try:
        return subprocess.run(
            args,
            input=stdin,
            env=env,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise CredentialStoreError(f"{args[0]} not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise CredentialStoreError(
            f"{args[0]} timed out after {_SUBPROCESS_TIMEOUT}s; the credential "
            "store may be locked and waiting for a passphrase prompt."
        ) from exc


def read_active() -> str | None:
    """Return the live agy credential, or None when the slot is empty."""
    backend = backend_name()
    if backend == BACKEND_NONE:
        return None

    if backend == BACKEND_SECRET_SERVICE:
        proc = _run(
            [
                "secret-tool", "lookup",
                "service", keyring_service(),
                "username", keyring_username(),
            ],
            env=_dbus_env(),
        )
        # secret-tool exits non-zero both for "not found" and for real errors,
        # but only prints to stderr in the latter case.
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            if stderr:
                raise CredentialStoreError(f"secret-tool lookup failed: {stderr}")
            return None
        value = (proc.stdout or "").rstrip("\n")
    else:
        proc = _run(
            [
                "security", "find-generic-password",
                "-s", keyring_service(),
                "-a", keyring_username(),
                "-w",
            ]
        )
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            if "could not be found" in stderr or proc.returncode == 44:
                return None
            raise CredentialStoreError(f"security find-generic-password failed: {stderr}")
        value = (proc.stdout or "").rstrip("\n")

    value = value.strip()
    if not value:
        return None
    return _decode(value)


def write_active(value: str) -> None:
    """Replace the live agy credential."""
    backend = backend_name()
    if backend == BACKEND_NONE:
        raise CredentialStoreError(unavailable_hint())
    if not value.strip():
        raise CredentialStoreError("Refusing to store an empty credential.")

    if backend == BACKEND_SECRET_SERVICE:
        proc = _run(
            [
                "secret-tool", "store",
                "--label", f"{keyring_service()}: {keyring_username()}",
                "service", keyring_service(),
                "username", keyring_username(),
            ],
            env=_dbus_env(),
            stdin=value,
        )
        if proc.returncode != 0:
            raise CredentialStoreError(
                (proc.stderr or "").strip() or "secret-tool store failed."
            )
        return

    # macOS: `security` has no stdin path for the secret, so it goes through
    # argv and is briefly visible to `ps` for other processes of this user.
    proc = _run(
        [
            "security", "add-generic-password",
            "-U",
            "-s", keyring_service(),
            "-a", keyring_username(),
            "-w", value,
        ]
    )
    if proc.returncode != 0:
        raise CredentialStoreError(
            (proc.stderr or "").strip() or "security add-generic-password failed."
        )


def clear_active() -> None:
    """Delete the live agy credential, so the next `agy` run logs in fresh."""
    backend = backend_name()
    if backend == BACKEND_NONE:
        return

    if backend == BACKEND_SECRET_SERVICE:
        _run(
            [
                "secret-tool", "clear",
                "service", keyring_service(),
                "username", keyring_username(),
            ],
            env=_dbus_env(),
        )
        return

    _run(
        [
            "security", "delete-generic-password",
            "-s", keyring_service(),
            "-a", keyring_username(),
        ]
    )
