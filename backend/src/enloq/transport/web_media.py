"""Privately fetch selected web GIF or poster bytes, without trusting URL/MIME.

The caller chooses the exact result, prefers an available native document and
owns the returned file's lifetime. This module only transfers its original
identity-encoded bytes; the caller must verify actual GIF/MP4 media or an actual
image for deriving a still thumbnail. No source URL is returned or logged.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import os
import re
import socket
import ssl
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

import httpcore

from enloq.domain.models import MediaTooLarge, Refused, Unavailable

GIF_BYTES = 50 * 1024**2
THUMBNAIL_BYTES = 5 * 1024**2
_URL_BYTES = 8192
_HEADER_BYTES = 16 * 1024
_HEADER_COUNT = 64
_DNS_ADDRESSES = 32
_REDIRECTS = 3
_TOTAL_SECONDS = 60.0
_IO_SECONDS = 10.0
_REFUSAL = "Selected web GIF cannot be fetched safely"
_UNAVAILABLE = "Selected web GIF is unavailable"
_TOO_LARGE = "Selected web GIF exceeds the file limit"
_THUMBNAIL_REFUSAL = "Selected web thumbnail cannot be fetched safely"
_THUMBNAIL_UNAVAILABLE = "Selected web thumbnail is unavailable"
_THUMBNAIL_TOO_LARGE = "Selected web thumbnail exceeds the file limit"
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_MIMES = frozenset(
    {"image/gif", "video/mp4", "application/octet-stream", "binary/octet-stream"}
)
_THUMBNAIL_MIMES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "application/octet-stream",
        "binary/octet-stream",
    }
)
_DOMAIN_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_BAD_ESCAPE = re.compile(r"%(?![0-9a-fA-F]{2})")
_TRANSLATION_NETS = (
    ipaddress.IPv6Network("64:ff9b::/96"),
    ipaddress.IPv6Network("64:ff9b:1::/48"),
)
_PRIVATE_FETCH = ContextVar("private_selected_web_media_fetch", default=False)


class _PrivateFetchLogFilter(logging.Filter):
    def filter(self, record):
        # HTTPCore DEBUG events include Location headers and remote exception
        # text. Suppress them only in this fetch's context, not in other calls.
        return not _PRIVATE_FETCH.get()


for _logger_name in ("httpcore.connection", "httpcore.http11"):
    logging.getLogger(_logger_name).addFilter(_PrivateFetchLogFilter())


@dataclass(frozen=True)
class WebMediaFile:
    size: int
    # Admission metadata only. The verifier must inspect the retained bytes.
    mime: str | None


@dataclass(frozen=True, repr=False)
class _Target:
    url: str
    scheme: str
    host: str
    port: int
    authority: str
    resource: str


def _public_ip(value: str):
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        raise Refused(_REFUSAL) from None
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
        or address.is_loopback
        or address.is_link_local
    ):
        raise Refused(_REFUSAL)
    if isinstance(address, ipaddress.IPv6Address) and (
        address.scope_id is not None
        or address.is_site_local
        or address.ipv4_mapped is not None
        or address.sixtofour is not None
        or address.teredo is not None
        or any(address in network for network in _TRANSLATION_NETS)
    ):
        raise Refused(_REFUSAL)
    return address


def _target(url: str) -> _Target:
    try:
        if (
            not isinstance(url, str)
            or len(url.encode("utf-8")) > _URL_BYTES
            or any(ord(char) <= 32 or ord(char) == 127 for char in url)
            or "\\" in url
            or _BAD_ESCAPE.search(url)
        ):
            raise ValueError
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.netloc
            or parts.username is not None
            or parts.password is not None
            or parts.fragment
        ):
            raise ValueError
        host = parts.hostname
        if not host or "%" in host:
            raise ValueError
        port = 443 if parts.scheme == "https" else 80
        if parts.port is not None and parts.port != port:
            raise ValueError
        host = host.rstrip(".").encode("idna").decode("ascii").lower()
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if (
                len(host) > 253
                or "." not in host
                or all(char in "0123456789." for char in host)
                or any(not _DOMAIN_LABEL.fullmatch(label) for label in host.split("."))
                or host.endswith(
                    (".localhost", ".local", ".internal", ".home", ".lan", ".onion")
                )
            ):
                raise ValueError
            authority = host
        else:
            _public_ip(str(address))
            host = str(address)
            authority = f"[{host}]" if address.version == 6 else host
        path = quote(parts.path or "/", safe="/:@!$&'()*+,;=-._~%")
        query = quote(parts.query, safe="/:@!$&'()*+,;=?-._~%")
        has_query = "?" in url.split("#", 1)[0]
        normalized = urlunsplit((parts.scheme, authority, path, query, ""))
        if has_query and not query:
            normalized += "?"
        if len(normalized.encode("ascii")) > _URL_BYTES:
            raise ValueError
        return _Target(
            normalized,
            parts.scheme,
            host,
            port,
            authority,
            path + ("?" + query if has_query else ""),
        )
    except (ValueError, UnicodeError):
        raise Refused(_REFUSAL) from None


def validate_web_url(url: str) -> str:
    """Admit obvious HTTP(S) URLs without DNS; fetching revalidates everything.

    Only the scheme's ordinary port is supported. The returned normalization
    preserves the selected path/query rather than selecting another result.
    Raises a content-free Refused for unsupported syntax or obvious local URLs.
    """
    return _target(url).url


async def _resolve(host: str, port: int) -> list[str]:
    # to_thread does not put the hostname in asyncio's getaddrinfo DEBUG logs.
    records = await asyncio.to_thread(
        socket.getaddrinfo,
        host,
        port,
        socket.AF_UNSPEC,
        socket.SOCK_STREAM,
        socket.IPPROTO_TCP,
    )
    if len(records) > _DNS_ADDRESSES:
        raise Refused(_REFUSAL)
    return list(
        dict.fromkeys(
            record[4][0]
            for record in records
            if record[0] in {socket.AF_INET, socket.AF_INET6}
        )
    )


async def _destination(target: _Target) -> str:
    try:
        ipaddress.ip_address(target.host)
    except ValueError:
        addresses = await _resolve(target.host, target.port)
    else:
        addresses = [target.host]
    if not addresses or len(addresses) > _DNS_ADDRESSES:
        raise Refused(_REFUSAL)
    # Reject a mixed public/private DNS answer as well as an entirely local one.
    validated = [_public_ip(value) for value in addresses]
    return str(validated[0])


def _network_backend() -> httpcore.AsyncNetworkBackend:
    return httpcore.AnyIOBackend()


class _PinnedBackend(httpcore.AsyncNetworkBackend):
    def __init__(self, target: _Target, address: str):
        self.target = target
        self.address = address
        self.backend = _network_backend()

    async def connect_tcp(
        self, host, port, timeout=None, local_address=None, socket_options=None
    ):
        if host != self.target.host or port != self.target.port or local_address:
            raise Refused(_REFUSAL)
        stream = await self.backend.connect_tcp(
            # Pass a numeric address, never the hostname, to the TCP connector.
            self.address,
            port,
            timeout=timeout,
            socket_options=socket_options,
        )
        try:
            peer = stream.get_extra_info("server_addr")
            if (
                not isinstance(peer, tuple)
                or len(peer) < 2
                or _public_ip(peer[0]) != _public_ip(self.address)
                or peer[1] != port
            ):
                raise Refused(_REFUSAL)
        except BaseException:
            await stream.aclose()
            raise
        # HTTPCore upgrades this stream using target.host for TLS SNI and
        # certificate verification, while the already-open TCP peer stays pinned.
        return stream

    async def connect_unix_socket(self, *args, **kwargs):
        raise Refused(_REFUSAL)

    async def sleep(self, seconds):
        await asyncio.sleep(seconds)


def _headers(response: httpcore.Response) -> dict[bytes, list[bytes]]:
    if (
        len(response.headers) > _HEADER_COUNT
        or sum(len(key) + len(value) + 4 for key, value in response.headers)
        > _HEADER_BYTES
    ):
        raise Refused(_REFUSAL)
    headers: dict[bytes, list[bytes]] = {}
    for key, value in response.headers:
        headers.setdefault(key.lower(), []).append(value)
    return headers


def _one(headers, name, default=None):
    values = headers.get(name, [])
    if len(values) > 1:
        raise Refused(_REFUSAL)
    return values[0].strip() if values else default


def _content(headers, limit, purpose):
    encoding = _one(headers, b"content-encoding", b"identity").lower()
    if encoding != b"identity":
        raise Refused(_REFUSAL)
    declared = _one(headers, b"content-length")
    if declared is not None:
        if not declared or not all(48 <= value <= 57 for value in declared):
            raise Refused(_REFUSAL)
        # Bound parsing too: arbitrarily long decimal integers are unnecessary.
        if len(declared) > 10:
            raise MediaTooLarge(_TOO_LARGE)
        declared = int(declared)
        if declared > limit:
            raise MediaTooLarge(_TOO_LARGE)
        if declared == 0:
            raise Refused(_REFUSAL)
    mime = _one(headers, b"content-type")
    if mime is not None:
        try:
            mime = mime.split(b";", 1)[0].strip().decode("ascii").lower()
        except UnicodeError:
            raise Refused(_REFUSAL) from None
        allowed = _THUMBNAIL_MIMES if purpose == "thumbnail" else _MIMES
        if mime not in allowed:
            raise Refused(_REFUSAL)
    return declared, mime


async def _download(target: _Target, file, limit: int, purpose) -> WebMediaFile:
    visited = set()
    for hop in range(_REDIRECTS + 1):
        if target.url in visited:
            raise Refused(_REFUSAL)
        visited.add(target.url)
        address = await _destination(target)
        async with httpcore.AsyncConnectionPool(
            network_backend=_PinnedBackend(target, address),
            ssl_context=ssl.create_default_context(),
            max_connections=1,
            max_keepalive_connections=0,
            retries=0,
            http1=True,
            http2=False,
        ) as pool:
            url = httpcore.URL(
                scheme=target.scheme.encode("ascii"),
                host=target.host.encode("ascii"),
                port=target.port,
                target=target.resource.encode("ascii"),
            )
            async with pool.stream(
                "GET",
                url,
                headers=[
                    (b"Host", target.authority.encode("ascii")),
                    (
                        b"Accept",
                        b"image/jpeg, image/png, image/webp, application/octet-stream"
                        if purpose == "thumbnail"
                        else b"image/gif, video/mp4, application/octet-stream",
                    ),
                    (b"Accept-Encoding", b"identity"),
                    (b"Connection", b"close"),
                ],
                extensions={
                    "timeout": dict.fromkeys(
                        ("connect", "read", "write", "pool"), _IO_SECONDS
                    )
                },
            ) as response:
                headers = _headers(response)
                if response.status in _REDIRECT_STATUSES:
                    location = _one(headers, b"location")
                    if not location or len(location) > _URL_BYTES or hop == _REDIRECTS:
                        raise Refused(_REFUSAL)
                    try:
                        following = _target(
                            urljoin(target.url, location.decode("ascii"))
                        )
                    except UnicodeError:
                        raise Refused(_REFUSAL) from None
                    if target.scheme == "https" and following.scheme != "https":
                        raise Refused(_REFUSAL)
                    target = following
                    # Closing this response does not consume a redirect's body.
                    continue
                if response.status != 200:
                    raise Unavailable(_UNAVAILABLE)
                declared, mime = _content(headers, limit, purpose)
                size = 0
                async for chunk in response.aiter_stream():
                    if size + len(chunk) > limit:
                        raise MediaTooLarge(_TOO_LARGE)
                    file.write(chunk)
                    size += len(chunk)
                if not size or (declared is not None and declared != size):
                    raise Unavailable(_UNAVAILABLE)
                return WebMediaFile(size, mime)
    raise Refused(_REFUSAL)  # All redirect paths above are bounded.


async def fetch_web_media(
    url: str,
    path: Path,
    *,
    limit: int = GIF_BYTES,
    purpose: Literal["media", "thumbnail"] = "media",
) -> WebMediaFile:
    """Fetch exact selected bytes into a new mode-600 file in a private directory.

    A successful result is transfer evidence, not media/image format proof. The
    caller must supply a new path; existing files are never overwritten or
    removed. Any failed or cancelled transfer removes its own partial file.
    HTTP(S) ordinary ports, three redirects, 10-second I/O waits and a 60-second
    overall deadline are supported. No cookies, auth, proxies or decompression.
    The private thumbnail purpose admits JPEG/PNG/WebP MIME metadata and clamps
    the transfer to 5 MiB even when the caller passes the default 50 MiB limit.
    The caller must validate an actual image and derive a still frame.
    """
    if purpose not in ("media", "thumbnail"):
        raise Refused(_REFUSAL)
    refusal, unavailable, too_large = (
        (_THUMBNAIL_REFUSAL, _THUMBNAIL_UNAVAILABLE, _THUMBNAIL_TOO_LARGE)
        if purpose == "thumbnail"
        else (_REFUSAL, _UNAVAILABLE, _TOO_LARGE)
    )
    created = False
    complete = False
    token = _PRIVATE_FETCH.set(True)
    try:
        target = _target(url)
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 0 < limit <= GIF_BYTES
        ):
            raise Refused(refusal)
        if purpose == "thumbnail":
            limit = min(limit, THUMBNAIL_BYTES)
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
            0o600,
        )
        created = True
        with os.fdopen(descriptor, "wb") as file:
            os.fchmod(file.fileno(), 0o600)
            async with asyncio.timeout(_TOTAL_SECONDS):
                result = await _download(target, file, limit, purpose)
        complete = True
        return result
    except MediaTooLarge:
        raise MediaTooLarge(too_large) from None
    except Unavailable:
        raise Unavailable(unavailable) from None
    except Refused:
        raise Refused(refusal) from None
    except (
        httpcore.NetworkError,
        httpcore.TimeoutException,
        httpcore.ProtocolError,
        OSError,
        TimeoutError,
        ValueError,
    ):
        raise Unavailable(unavailable) from None
    finally:
        try:
            if created and not complete:
                path.unlink(missing_ok=True)
        except OSError:
            raise Unavailable(unavailable) from None
        finally:
            _PRIVATE_FETCH.reset(token)
