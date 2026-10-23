"""Offline mode never opens populated configuration or credential references."""

from __future__ import annotations

import ipaddress
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from .domain.models import Refused

ROOT = Path(__file__).resolve().parents[3]


def private_directory(path: Path) -> Path:
    if path.is_symlink():
        raise Refused("Private directory cannot be a symlink")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)
    return path


def private_read(path: Path, *, max_bytes: int = 65536) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_mode & 0o077
            or info.st_uid != os.getuid()
        ):
            raise Refused("Private file must be owned by this user and mode 0600")
        if info.st_size > max_bytes:
            raise Refused("Private file exceeds size limit")
        return os.read(fd, max_bytes + 1).decode().strip()
    finally:
        os.close(fd)


def private_write(path: Path, text: str) -> None:
    private_directory(path.parent)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        # fdopen owns the descriptor once constructed.
        raise


def parse_config(path: Path) -> dict[str, str]:
    values = {}
    for line in private_read(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if not separator or not key.startswith("ENLOQ_"):
            raise Refused("Configuration contains an unsupported entry")
        values[key.removeprefix("ENLOQ_")] = value.strip().strip("\"'")
    return values


def public_origin(value: str) -> str:
    """An exact https origin for a service reached through a TLS proxy."""
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or value != f"https://{parts.netloc.lower()}"
    ):
        raise Refused("Public origin must be an exact https:// origin")
    return value


PROJECT = r"[a-z][a-z0-9-]{4,28}[a-z0-9]"


def fcm_settings(values: dict[str, str], refs: dict[str, str]) -> dict[str, str]:
    """An exact Firebase project, its service account and an optional key ID."""
    project = values.get("FCM_PROJECT", "")
    account = values.get("FCM_ACCOUNT", "")
    key_id = values.get("FCM_KEY_ID", "")
    if not (project or account or key_id):
        return {}
    if (
        not re.fullmatch(PROJECT, project)
        or not re.fullmatch(
            rf"[a-z][a-z0-9-]{{4,28}}[a-z0-9]@{project}\.iam\.gserviceaccount\.com",
            account,
        )
        or (key_id and not re.fullmatch(r"[0-9a-f]{40}", key_id))
        or not refs.get("FCM_KEY_REF")
    ):
        raise Refused("FCM needs a project, its service account and FCM_KEY_REF")
    return {"fcm_project": project, "fcm_account": account, "fcm_key_id": key_id}


@dataclass(frozen=True)
class Settings:
    mode: Literal["offline", "personal"] = "offline"
    data: Path = ROOT / ".local" / "offline"
    port: int = 8765
    timezone: str = "Asia/Ho_Chi_Minh"
    refs: dict[str, str] = field(default_factory=dict, repr=False)
    # ADR 40: a VPS service binds beyond loopback only behind a public origin,
    # and trusts forwarded client addresses only from its named edge proxy.
    listen_host: str = "127.0.0.1"
    public_origin: str = ""
    trusted_proxy: str = ""
    # ADR 38: Firebase Cloud Messaging carries pushes
    # to the Enloq app on Android, sent for this service account. Its private
    # key is the FCM_KEY_REF secret, generated on the VPS.
    fcm_project: str = ""
    fcm_account: str = ""
    fcm_key_id: str = ""
    # ADR 43 Q4: the Owner Account, whose own Saved Messages receive Saved
    # Builds and Telegram pairing codes; 0 when none is set.
    owner_account: int = 0

    @property
    def origin(self) -> str:
        return self.public_origin or f"http://127.0.0.1:{self.port}"

    @property
    def remote(self) -> bool:
        return bool(self.public_origin)

    @classmethod
    def load(cls, mode: str = "offline", *, root: Path = ROOT, port: int = 8765):
        if mode not in {"offline", "personal"} or not 1024 <= port <= 65535:
            raise Refused("Invalid mode or loopback port")
        # Explicit mode namespaces cannot be redirected into another mode's store.
        if mode == "offline":
            return cls(data=root / ".local/offline", port=port)
        values = parse_config(root / "config/app.env")
        origin = values.get("PUBLIC_ORIGIN", "")
        if origin:
            public_origin(origin)
        listen_host = values.get("LISTEN_HOST", "127.0.0.1")
        try:
            ipaddress.ip_address(listen_host)
        except ValueError as error:
            raise Refused("Listen host must be an IP address") from error
        if listen_host != "127.0.0.1" and not origin:
            raise Refused("Only loopback binding is supported without a public origin")
        proxy = values.get("TRUSTED_PROXY", "")
        if proxy and (
            not origin or not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,62}", proxy)
        ):
            raise Refused("A trusted proxy needs a public origin and a plain host name")
        timezone = values.get("TIMEZONE", "Asia/Ho_Chi_Minh")
        ZoneInfo(timezone)
        refs = {
            key: str((root / value).resolve())
            for key, value in values.items()
            if key.endswith("_REF") and not key.startswith("CODEX_") and value
        }
        if mode == "personal":
            for role in ("API_ID", "API_HASH"):
                refs["TELEGRAM_" + role + "_REF"] = refs.get(
                    "PERSONAL_TELEGRAM_" + role + "_REF", ""
                )
        refs["EXPECTED_ACCOUNT_ID"] = values.get("EXPECTED_ACCOUNT_ID", "")
        fcm = fcm_settings(values, refs)
        owner = values.get("OWNER_ACCOUNT", "")
        if owner and not re.fullmatch(r"[1-9][0-9]{0,18}", owner):
            raise Refused("The Owner Account must be a Telegram user ID")
        return cls(
            mode=mode,
            data=root / ".local" / mode,
            port=port,
            timezone=timezone,
            refs=refs,
            listen_host=listen_host,
            public_origin=origin,
            trusted_proxy=proxy,
            owner_account=int(owner or 0),
            **fcm,
        )

    def secret(self, key: str) -> str:
        if self.mode == "offline" or not self.refs.get(key):
            raise Refused("Credential reference unavailable in this mode")
        return private_read(Path(self.refs[key]), max_bytes=8192)
