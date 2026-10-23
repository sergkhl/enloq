"""Viewing eligibility and bounded local MP4/WebM container verification.

Eligibility is presentation metadata: these reads never change a document's
source kind or content identity. Verification opens only the complete local
file; it neither converts codecs nor promises that a browser can decode them.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import BinaryIO

from ..inference.runner import bounded_process
from ..media_tools import FFPROBE
from .models import Message, Unavailable

VIDEO_BYTES = 50 * 1024 * 1024
_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm")
_MP4_BRANDS = frozenset(
    {
        b"isom",
        b"iso2",
        b"iso3",
        b"iso4",
        b"iso5",
        b"iso6",
        b"iso7",
        b"iso8",
        b"iso9",
        b"mp41",
        b"mp42",
        b"avc1",
        b"dash",
        b"MSNV",
        b"M4V ",
        b"M4VH",
        b"M4VP",
        b"cmfc",
        b"cmfs",
    }
)
_MAX_ELEMENTS = 8192
_EBML = 0x1A45DFA3
_DOCTYPE = 0x4282
_SEGMENT = 0x18538067
_TRACKS = 0x1654AE6B
_CLUSTER = 0x1F43B675


def video_candidate(message: Message) -> bool:
    """Native ordinary videos, or video-looking documents, may offer Play.

    Circles, GIFs and stickers retain their separate existing presentation even
    when their MIME or filename is video-shaped. Access/currentness gates are
    enforced by the caller, including for read-only observed versions.
    """
    if message.media == "video":
        return True
    if message.media != "document" or message.media_info is None:
        return False
    info = message.media_info
    mime = (info.mime or "").partition(";")[0].strip().lower()
    return bool(
        (mime.startswith("video/") and len(mime) > len("video/"))
        or (info.file_name or "").lower().endswith(_EXTENSIONS)
    )


def _mp4(stream: BinaryIO, size: int) -> bool:
    """Check complete top-level box bounds and an actual MP4 file-type brand.

    ffprobe shares a demuxer for MP4, QuickTime, 3GP and JPEG 2000; its format
    name alone cannot distinguish those containers. Empty magic-only files and
    truncated boxes are rejected before probing their track/packet evidence.
    """
    found = set()
    for _ in range(_MAX_ELEMENTS):
        start = stream.tell()
        if start == size:
            return {b"ftyp", b"moov", b"mdat"} <= found
        header = stream.read(8)
        if len(header) != 8:
            return False
        length, kind = int.from_bytes(header[:4], "big"), header[4:]
        header_size = 8
        if length == 1:
            extended = stream.read(8)
            if len(extended) != 8:
                return False
            length, header_size = int.from_bytes(extended, "big"), 16
        elif length == 0:
            length = size - start
        if length < header_size or start + length > size:
            return False
        if kind == b"ftyp":
            if kind in found or not 8 <= length - header_size <= 4096:
                return False
            payload = stream.read(length - header_size)
            compatible = {payload[i : i + 4] for i in range(8, len(payload), 4)}
            if len(payload) % 4 or not (
                payload[:4] in _MP4_BRANDS or compatible & {b"mp41", b"mp42"}
            ):
                return False
            found.add(kind)
        elif kind in (b"moov", b"mdat") and length > header_size:
            found.add(kind)
        stream.seek(start + length)
    return False


def _vint(stream: BinaryIO, *, identifier: bool) -> int | None:
    first = stream.read(1)
    if not first or not first[0]:
        raise ValueError("Invalid EBML integer")
    length = 9 - first[0].bit_length()
    if length > (4 if identifier else 8):
        raise ValueError("EBML integer exceeds bounds")
    tail = stream.read(length - 1)
    if len(tail) != length - 1:
        raise ValueError("Truncated EBML integer")
    value = int.from_bytes(first + tail, "big")
    if identifier:
        return value
    value &= (1 << (7 * length)) - 1
    return None if value == (1 << (7 * length)) - 1 else value


def _element(stream: BinaryIO, end: int) -> tuple[int, int | None]:
    kind = _vint(stream, identifier=True)
    length = _vint(stream, identifier=False)
    if stream.tell() > end or (length is not None and stream.tell() + length > end):
        raise ValueError("EBML element exceeds complete file")
    return kind, length


def _webm(stream: BinaryIO, size: int) -> bool:
    kind, length = _element(stream, size)
    if kind != _EBML or length is None or length > 4096:
        return False
    header_end, doctype = stream.tell() + length, None
    while stream.tell() < header_end:
        kind, length = _element(stream, header_end)
        if length is None:
            return False
        if kind == _DOCTYPE:
            if doctype is not None or length != 4:
                return False
            doctype = stream.read(length)
        else:
            stream.seek(length, 1)
    if doctype != b"webm":
        return False
    kind, length = _element(stream, size)
    if kind != _SEGMENT:
        return False
    segment_end = size if length is None else stream.tell() + length
    if segment_end != size:
        return False
    found = set()
    for _ in range(_MAX_ELEMENTS):
        if stream.tell() == segment_end:
            return {_TRACKS, _CLUSTER} <= found
        kind, length = _element(stream, segment_end)
        if kind in (_TRACKS, _CLUSTER) and length != 0:
            found.add(kind)
        if length is None:
            # Streaming WebM allows an unknown-sized Cluster. ffprobe must
            # still prove a video track and real packet within the local file.
            return kind == _CLUSTER and {_TRACKS, _CLUSTER} <= found
        stream.seek(length, 1)
    return False


async def video_mime(path: Path) -> str | None:
    """Return an inline MIME only for a bounded, verified local video container.

    Unsupported/corrupt containers stay opaque for Download. An unavailable
    probe raises a content-free Unavailable so deliberate Retry can reverify
    retained complete bytes without treating absent proof as a format verdict.
    The existing subprocess guard bounds process lifetime and captured output;
    no source tags, packet contents or plaintext diagnostics leave this function.
    Browser codec/decode support remains a separate playback check.
    """
    path = Path(path).absolute()
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= VIDEO_BYTES:
            return None
        with path.open("rb") as stream:
            header = stream.read(8)
            stream.seek(0)
            webm = header[:4] == _EBML.to_bytes(4, "big")
            if not (
                _webm(stream, before.st_size) if webm else _mp4(stream, before.st_size)
            ):
                return None
        demuxer, mime = ("matroska", "video/webm") if webm else ("mov", "video/mp4")
        argv = [
            FFPROBE,
            "-v",
            "error",
            "-max_alloc",
            "16777216",
            "-protocol_whitelist",
            "file",
            "-format_whitelist",
            demuxer,
            "-f",
            demuxer,
            "-probesize",
            "1048576",
            "-analyzeduration",
            "1000000",
            "-max_probe_packets",
            "64",
            "-threads",
            "1",
        ]
        if not webm:
            argv += [
                "-enable_drefs",
                "0",
                "-use_absolute_path",
                "0",
                "-ignore_chapters",
                "1",
            ]
        argv += [
            "-read_intervals",
            "%+#64",
            "-select_streams",
            "v:0",
            "-show_entries",
            (
                "format=format_name:stream=index,codec_type,width,height:"
                "stream_disposition=attached_pic:packet=stream_index,pos,size"
            ),
            "-of",
            "json",
            "-i",
            str(path),
        ]
        try:
            stdout, stderr = await bounded_process(
                argv,
                stdin=b"",
                cwd=path.parent,
                env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "LANG": "C"},
                timeout=10,
                stdout_limit=65536,
                stderr_limit=8192,
            )
        except (OSError, Unavailable, TimeoutError):
            # The shared guard intentionally withholds exit diagnostics. A
            # nonzero probe cannot distinguish corruption from missing tooling;
            # keep it retryable instead of caching an unsupported-format verdict.
            raise Unavailable("Video verification unavailable") from None
        after = path.lstat()
        if stderr or any(
            getattr(after, field) != getattr(before, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
                "st_mode",
            )
        ):
            return None
        data = json.loads(stdout)
        formats = set(data["format"]["format_name"].split(","))
        if ("webm" if webm else "mp4") not in formats:
            return None
        track = data["streams"][0]
        if (
            track["codec_type"] != "video"
            or not (track["width"] > 0 and track["height"] > 0)
            or track.get("disposition", {}).get("attached_pic", 0)
        ):
            return None
        return (
            mime
            if any(
                packet["stream_index"] == track["index"]
                and 0 <= int(packet["pos"]) < before.st_size
                and 0 < int(packet["size"]) <= before.st_size - int(packet["pos"])
                for packet in data["packets"]
            )
            else None
        )
    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        IndexError,
        AttributeError,
    ):
        return None
