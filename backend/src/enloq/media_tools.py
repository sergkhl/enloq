"""Fixed media tool paths, never resolved through PATH.

Homebrew provides ffmpeg on the Mac; the Debian package provides it in the VPS
image (ADR 40).
"""

from pathlib import Path

DIRECTORIES = ("/opt/homebrew/bin", "/usr/bin")


def media_tool(name: str) -> str:
    for directory in DIRECTORIES:
        path = Path(directory) / name
        if path.is_file():
            return str(path)
    return f"{DIRECTORIES[-1]}/{name}"


FFMPEG = media_tool("ffmpeg")
FFPROBE = media_tool("ffprobe")
