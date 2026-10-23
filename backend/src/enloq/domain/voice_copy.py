"""The AAC copy of a voice note, for iPhones (ADR 38 Q13, ADR 46 CU1).

iOS's players read neither Ogg nor Opus, so the service converts a voice note
once, on the first request from an iPhone, and caches the copy beside its
source under the same 30-day rule. Only the container the media sniff named is
read, the input is never probed as anything else, and the shared process guard
bounds time and captured output. No tags, chapters or other streams are copied.
"""

from pathlib import Path

from ..inference.runner import bounded_process
from ..media_tools import FFMPEG
from .models import Refused, Unavailable

# A voice note source's sniffed MIME type and the one demuxer allowed to read it.
DEMUXERS = {
    "audio/ogg": "ogg",
    "audio/webm": "matroska",
    "audio/mpeg": "mp3",
    "audio/mp4": "mov",
    "audio/wav": "wav",
    "audio/flac": "flac",
}
MIME = "audio/mp4"


async def aac_copy(source: Path, destination: Path, mime: str | None) -> None:
    """Write `source` as mono AAC in an MP4 container that starts playing early."""
    demuxer = DEMUXERS.get(mime or "")
    if demuxer is None:
        raise Refused("This voice note can't be converted for this phone")
    try:
        await bounded_process(
            [
                FFMPEG,
                "-nostdin",
                "-v",
                "error",
                "-y",
                "-protocol_whitelist",
                "file",
                "-format_whitelist",
                demuxer,
                "-f",
                demuxer,
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-vn",
                "-sn",
                "-dn",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-ac",
                "1",
                "-c:a",
                "aac",
                "-b:a",
                "64k",
                "-threads",
                "1",
                "-movflags",
                "+faststart",
                "-f",
                "mp4",
                str(destination),
            ],
            stdin=b"",
            cwd=destination.parent,
            env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "LANG": "C"},
            timeout=120,
            stdout_limit=65536,
            stderr_limit=65536,
        )
        with destination.open("rb") as stream:
            converted = stream.read(8)[4:8] == b"ftyp"
    except (OSError, Unavailable, TimeoutError):
        # The guard withholds diagnostics: a failed conversion stays retryable.
        raise Unavailable("Voice copy unavailable; try again shortly") from None
    if not converted:
        raise Unavailable("Voice copy unavailable; try again shortly")
