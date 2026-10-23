# Adapted from hercules-support-relay, the author's earlier private project; see docs/extraction.md.
"""Bounded image validation and scratch files that never outlive their lease.

Telegram is the customer-visible message history, so the gateway keeps digests
and coordinates rather than bytes.  Every image therefore exists only inside one
lease, whose file is unlinked on the way out, and is trusted only after its own
bytes have been sniffed, sized, measured, and digested against both the fixed
deployment ceiling and the narrower source limit.  Platform metadata is never
the authority: the declared width of a photo is checked against the width its
header actually carries.
"""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from PIL import Image

from ..domain.delivery import SendImage
from ..domain.models import Refused, Unreadable

MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_IMAGE_DIMENSION = 4096
MAX_IMAGE_PIXELS = 16_777_216
IMAGE_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MEDIA_FILE_PREFIX = "reply-assistant-media-"
# Comfortably longer than any single lease: a download is bounded by the media
# timeout and an upload by Telegram itself, so a file older than this belongs
# to a process that is gone.
ORPHAN_MAX_AGE_SECONDS = 900.0

_SUFFIXES = {"image/jpeg": ".jpg", "image/png": ".png", "image/webp": ".webp"}
_JPEG_START_OF_FRAME = frozenset(
    {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
)
_JPEG_STANDALONE = frozenset({0x01, *range(0xD0, 0xDA)})


class MediaValidationError(RuntimeError):
    """One content-free reason an image cannot enter or leave the channel."""

    def __init__(self, code: str) -> None:
        super().__init__(f"channel media failed validation: {code}")
        self.code = code


@dataclass(frozen=True)
class MediaBounds:
    """The ceiling every image crosses, in both directions."""

    mime_types: frozenset[str]
    max_bytes: int
    max_dimension: int
    max_pixels: int

    def __post_init__(self) -> None:
        if not self.mime_types or not self.mime_types <= IMAGE_MIME_TYPES:
            raise ValueError("image MIME bounds are outside the supported set")
        if (
            not 1 <= self.max_bytes <= MAX_IMAGE_BYTES
            or not 1 <= self.max_dimension <= MAX_IMAGE_DIMENSION
            or not 1 <= self.max_pixels <= MAX_IMAGE_PIXELS
        ):
            raise ValueError("image bounds are outside the deployment ceiling")

    @classmethod
    def narrowed(
        cls,
        *,
        mime_types: frozenset[str],
        image_bytes: int,
        image_dimension: int,
        image_pixels: int,
    ) -> MediaBounds:
        """Take the tighter of the deployment ceiling and the source limit."""
        return cls(
            mime_types=frozenset(mime_types) & IMAGE_MIME_TYPES,
            max_bytes=min(MAX_IMAGE_BYTES, image_bytes),
            max_dimension=min(MAX_IMAGE_DIMENSION, image_dimension),
            max_pixels=min(MAX_IMAGE_PIXELS, image_pixels),
        )

    def admits(
        self, *, mime_type: str, size_bytes: int, width: int, height: int
    ) -> bool:
        return (
            mime_type in self.mime_types
            and 1 <= size_bytes <= self.max_bytes
            and 1 <= width <= self.max_dimension
            and 1 <= height <= self.max_dimension
            and width * height <= self.max_pixels
        )


@dataclass(frozen=True)
class ImageFacts:
    """What one image's own bytes say about it."""

    mime_type: str
    size_bytes: int
    sha256: str
    width: int
    height: int

    def attachment(self, attachment_id: str) -> dict[str, Any]:
        """Render the frozen V1 attachment projection for these bytes."""
        return {
            "attachmentId": attachment_id,
            "mimeType": self.mime_type,
            "sizeBytes": self.size_bytes,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
        }


class MediaWorkspace:
    """One private directory whose files exist only for a single lease."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def prepare(self) -> None:
        """Create the private work directory, or refuse to use what is there."""
        try:
            self._path.mkdir(parents=True, exist_ok=True, mode=0o700)
            if self._path.is_symlink() or not self._path.is_dir():
                raise MediaValidationError("media_workspace_unavailable")
            self._path.chmod(0o700)
        except OSError as exc:
            raise MediaValidationError("media_workspace_unavailable") from exc

    def cleanup_orphans(self, *, older_than_seconds: float = 0.0) -> int:
        """Unlink leased files an interrupted run left behind.

        The age bound is what keeps this callable while the gateway is running:
        a sweep with no bound deletes the file a concurrent lease is still
        writing into, which drops that image with no ticket, no event, and
        nothing an operator can see. Only a start-up sweep, which by definition
        races nothing, passes zero.
        """
        if older_than_seconds < 0:
            raise ValueError("the orphan age bound must not be negative")
        cutoff = time.time() - older_than_seconds
        removed = 0
        for path in self._path.glob(f"{MEDIA_FILE_PREFIX}*"):
            try:
                status = path.lstat()
                if not stat.S_ISREG(status.st_mode) or status.st_mtime > cutoff:
                    continue
                path.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    @contextmanager
    def lease(self, *, mime_type: str | None = None) -> Iterator[Path]:
        """Yield one empty mode-600 file that is unlinked on every exit."""
        self.prepare()
        descriptor, raw_path = tempfile.mkstemp(
            prefix=MEDIA_FILE_PREFIX,
            suffix=_SUFFIXES.get(mime_type or "", ".bin"),
            dir=self._path,
        )
        path = Path(raw_path)
        try:
            os.fchmod(descriptor, 0o600)
            os.close(descriptor)
            yield path
        finally:
            path.unlink(missing_ok=True)


def validate_image(path: Path, bounds: MediaBounds) -> ImageFacts:
    """Read one image's true facts, trusting nothing that travelled beside it."""
    try:
        status = path.stat()
        if not stat.S_ISREG(status.st_mode) or path.is_symlink():
            raise MediaValidationError("image_not_a_regular_file")
        size_bytes = status.st_size
        with path.open("rb") as stream:
            mime_type = _sniff(stream.read(16))
            if mime_type is None or mime_type not in bounds.mime_types:
                raise MediaValidationError("image_type_unsupported")
            stream.seek(0)
            width, height = _dimensions(stream, mime_type)
            digest = hashlib.sha256()
            stream.seek(0)
            for chunk in iter(lambda: stream.read(64 * 1024), b""):
                digest.update(chunk)
    except MediaValidationError:
        raise
    except OSError as exc:
        raise MediaValidationError("image_unreadable") from exc
    if not bounds.admits(
        mime_type=mime_type, size_bytes=size_bytes, width=width, height=height
    ):
        raise MediaValidationError("image_outside_bounds")
    return ImageFacts(mime_type, size_bytes, digest.hexdigest(), width, height)


def _sniff(header: bytes) -> str | None:
    if header.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if header.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if header[:4] == b"RIFF" and header[8:12] == b"WEBP":
        return "image/webp"
    return None


def _dimensions(stream: BinaryIO, mime_type: str) -> tuple[int, int]:
    if mime_type == "image/png":
        return _png_dimensions(stream)
    if mime_type == "image/webp":
        return _webp_dimensions(stream)
    return _jpeg_dimensions(stream)


def _png_dimensions(stream: BinaryIO) -> tuple[int, int]:
    header = stream.read(24)
    if len(header) != 24 or header[12:16] != b"IHDR":
        raise MediaValidationError("image_signature_mismatch")
    return (
        int.from_bytes(header[16:20], "big"),
        int.from_bytes(header[20:24], "big"),
    )


def _webp_dimensions(stream: BinaryIO) -> tuple[int, int]:
    header = stream.read(30)
    if len(header) < 30:
        raise MediaValidationError("image_signature_mismatch")
    kind, data = header[12:16], header[20:]
    if kind == b"VP8X":
        return (
            1 + int.from_bytes(data[4:7], "little"),
            1 + int.from_bytes(data[7:10], "little"),
        )
    if kind == b"VP8L" and data[0] == 0x2F:
        bits = int.from_bytes(data[1:5], "little")
        return 1 + (bits & 0x3FFF), 1 + ((bits >> 14) & 0x3FFF)
    if kind == b"VP8 " and data[3:6] == b"\x9d\x01\x2a":
        return (
            int.from_bytes(data[6:8], "little") & 0x3FFF,
            int.from_bytes(data[8:10], "little") & 0x3FFF,
        )
    raise MediaValidationError("image_signature_mismatch")


def _jpeg_dimensions(stream: BinaryIO) -> tuple[int, int]:
    """Walk JPEG segments to the first frame header, which owns the true size."""
    stream.seek(2)
    while True:
        byte = stream.read(1)
        if not byte:
            break
        if byte != b"\xff":
            continue
        marker = stream.read(1)
        while marker == b"\xff":
            marker = stream.read(1)
        if not marker:
            break
        code = marker[0]
        if code in _JPEG_STANDALONE:
            continue
        raw_length = stream.read(2)
        if len(raw_length) != 2:
            break
        length = int.from_bytes(raw_length, "big")
        if length < 2:
            break
        if code in _JPEG_START_OF_FRAME:
            frame = stream.read(5)
            if len(frame) != 5:
                break
            # A start-of-frame carries precision, then lines, then samples per
            # line: height precedes width, and reading them the other way round
            # silently transposes every image that is not square.
            return (
                int.from_bytes(frame[3:5], "big"),
                int.from_bytes(frame[1:3], "big"),
            )
        stream.seek(length - 2, os.SEEK_CUR)
    raise MediaValidationError("image_signature_mismatch")


__all__ = [
    "IMAGE_MIME_TYPES",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_DIMENSION",
    "MAX_IMAGE_PIXELS",
    "ImageFacts",
    "MediaBounds",
    "MediaValidationError",
    "MediaWorkspace",
    "validate_image",
]


BOUNDS = MediaBounds.narrowed(
    mime_types=frozenset({"image/jpeg", "image/png", "image/webp"}),
    image_bytes=5 * 1024 * 1024,
    image_dimension=4096,
    image_pixels=16_777_216,
)


def image_facts(path):
    facts = validate_image(path, BOUNDS)
    try:
        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            image.load()
    except Exception:
        raise Unreadable("Image bytes cannot be decoded") from None
    return facts


def validate_send_image(image: SendImage):
    path = image.path
    if path.parent != image.directory or path.is_symlink() or not path.is_file():
        raise Refused("Draft image file unavailable")
    facts = image_facts(path)
    if facts.sha256 != image.sha256:
        raise Refused("Draft image bytes changed; attach it again")
    return facts
