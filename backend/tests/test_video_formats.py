"""Authored VP1 eligibility, exact-container evidence and probe boundaries."""

import asyncio
import json
import subprocess

import pytest
from enloq.domain import video
from enloq.domain.models import MediaInfo, Message, Unavailable
from enloq.domain.source_revision import dependency_fingerprint, fingerprint
from enloq.media_tools import FFMPEG


def source(kind, *, mime=None, name=None):
    return Message(
        account_id=1,
        peer="user:42",
        message_id=1,
        revision=1,
        date=2_000_000_000,
        media=kind,
        attachment_id="authored:video",
        media_info=MediaInfo(mime=mime, file_name=name) if mime or name else None,
    )


@pytest.mark.parametrize(
    "kind,mime,name,expected",
    [
        ("video", None, None, True),
        ("video", "application/octet-stream", "odd.bin", True),
        ("document", "video/mp4", "opaque.bin", True),
        ("document", "VIDEO/WEBM; codecs=vp9", None, True),
        ("document", None, "clip.MP4", True),
        ("document", "application/octet-stream", "clip.m4v", True),
        ("document", None, "clip.mov", True),
        ("document", None, "clip.webm", True),
        ("document", "application/pdf", "clip.mp4.pdf", False),
        ("document", "application/video", "report.pdf", False),
        ("document", "video/", None, False),
        ("document", None, None, False),
        ("document", None, "clip.mkv", False),
        ("sticker", "video/webm", "sticker.webm", False),
        ("gif", "video/mp4", "loop.mp4", False),
        ("video_note", "video/mp4", "circle.mp4", False),
        ("audio", "video/mp4", "track.mp4", False),
        (None, None, "clip.mp4", False),
    ],
)
def test_eligibility_preserves_source_classification_and_identity(
    kind, mime, name, expected
):
    message = source(kind, mime=mime, name=name)
    before = message.model_dump()
    digest, block = dependency_fingerprint(message), fingerprint([message])
    assert video.video_candidate(message) is expected
    assert message.model_dump() == before
    assert dependency_fingerprint(message) == digest
    assert fingerprint([message]) == block


@pytest.fixture(scope="module")
def containers(tmp_path_factory):
    """Generate only a plain blue scene and silence, never correspondence."""
    directory = tmp_path_factory.mktemp("authored-video-containers")

    def generate(name, *args):
        path = directory / name
        subprocess.run(
            [
                FFMPEG,
                "-v",
                "error",
                "-nostdin",
                "-y",
                *args,
                str(path),
            ],
            check=True,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return path

    scene = [
        "-f",
        "lavfi",
        "-i",
        "color=color=blue:size=32x24:rate=4",
        "-t",
        "0.5",
        "-an",
    ]
    mp4 = generate("authored.mp4", *scene, "-c:v", "libx264", "-pix_fmt", "yuv420p")
    webm = generate(
        "authored.webm", *scene, "-c:v", "libvpx-vp9", "-deadline", "realtime"
    )
    return {
        "mp4": mp4,
        "webm": webm,
        "m4v": generate("authored.m4v", "-i", str(mp4), "-c", "copy", "-f", "ipod"),
        "other_mp4_codec": generate("other-codec.mp4", *scene, "-c:v", "mpeg4"),
        "streaming_webm": generate(
            "streaming.webm", "-i", str(webm), "-c", "copy", "-live", "1", "-f", "webm"
        ),
        "mov": generate("quicktime.mov", "-i", str(mp4), "-c", "copy", "-f", "mov"),
        "matroska": generate(
            "matroska.mkv", "-i", str(webm), "-c", "copy", "-f", "matroska"
        ),
        "audio": generate(
            "audio.mp4",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=mono:sample_rate=8000",
            "-t",
            "0.25",
            "-c:a",
            "aac",
            "-f",
            "mp4",
        ),
    }


@pytest.mark.parametrize(
    "kind,mime",
    [
        ("mp4", "video/mp4"),
        ("m4v", "video/mp4"),
        ("other_mp4_codec", "video/mp4"),
        ("webm", "video/webm"),
        ("streaming_webm", "video/webm"),
    ],
)
async def test_inline_mime_follows_complete_container_bytes_not_name(
    tmp_path, containers, kind, mime
):
    misleading = tmp_path / ("wrong.webm" if kind == "mp4" else "wrong.mp4")
    misleading.write_bytes(containers[kind].read_bytes())
    assert await video.video_mime(misleading) == mime
    assert misleading.read_bytes() == containers[kind].read_bytes()


@pytest.mark.parametrize(
    "compatible,expected", [(b"mp42", "video/mp4"), (b"isom", None)]
)
async def test_specialized_major_brand_needs_explicit_mp4_compatibility(
    tmp_path, containers, compatible, expected
):
    body = bytearray(containers["mp4"].read_bytes())
    assert body[4:8] == b"ftyp"
    ftyp_end = int.from_bytes(body[:4], "big")
    body[8:12] = b"AUTH"
    for offset in range(16, ftyp_end, 4):
        body[offset : offset + 4] = compatible
    path = tmp_path / "specialized.mp4"
    path.write_bytes(body)
    assert await video.video_mime(path) == expected


@pytest.mark.parametrize("kind", ["mov", "matroska", "audio"])
async def test_shared_demuxer_or_audio_container_is_insufficient(containers, kind):
    assert await video.video_mime(containers[kind]) is None


@pytest.mark.parametrize(
    "body",
    [
        b"\x00\x00\x00\x18ftypisom\x00\x00\x00\x00isommp42",  # no track/media
        b"\x1a\x45\xdf\xa3\x84webm",  # generic EBML magic is not a WebM header
        b"\x00\x00\x00\x01ftyp",  # truncated extended box length
        b"\x00\x00\x00\x04ftyp",  # box shorter than its header
        b"<html>authored misleading video content</html>",
        b"",
    ],
)
async def test_container_magic_and_misleading_video_extension_stay_download_only(
    tmp_path, body
):
    path = tmp_path / "authored.mp4"
    path.write_bytes(body)
    assert await video.video_mime(path) is None


@pytest.mark.parametrize("kind", ["mp4", "webm"])
async def test_truncated_complete_file_is_rejected_even_with_intact_header(
    tmp_path, containers, kind
):
    path = tmp_path / f"truncated.{kind}"
    path.write_bytes(containers[kind].read_bytes()[:-8])
    assert await video.video_mime(path) is None


async def test_valid_looking_boxes_without_real_track_packet_are_rejected(tmp_path):
    def box(kind, payload):
        return (8 + len(payload)).to_bytes(4, "big") + kind + payload

    path = tmp_path / "spoof.mp4"
    path.write_bytes(
        box(b"ftyp", b"isom\0\0\0\0isommp42")
        + box(b"moov", b"authored")
        + box(b"mdat", b"authored")
    )
    assert await video.video_mime(path) is None


async def test_missing_nonregular_symlink_and_oversized_files_never_probe(
    tmp_path, monkeypatch
):
    async def unexpected(*args, **kwargs):
        pytest.fail("ineligible local file reached ffprobe")

    monkeypatch.setattr(video, "bounded_process", unexpected)
    path = tmp_path / "large.mp4"
    with path.open("wb") as stream:
        stream.truncate(video.VIDEO_BYTES + 1)
    link = tmp_path / "symlink.mp4"
    link.symlink_to(path)
    for candidate in (tmp_path / "missing.mp4", tmp_path, path, link):
        assert await video.video_mime(candidate) is None


async def test_probe_limits_local_file_only_and_withholds_private_diagnostics(
    containers, monkeypatch, capsys
):
    calls = []

    async def unavailable(argv, **kwargs):
        calls.append((argv, kwargs))
        raise Unavailable("authored private diagnostic canary")

    monkeypatch.setattr(video, "bounded_process", unavailable)
    with pytest.raises(Unavailable, match="^Video verification unavailable$"):
        await video.video_mime(containers["mp4"])
    argv, options = calls[0]
    for flag, value in (
        ("-protocol_whitelist", "file"),
        ("-format_whitelist", "mov"),
        ("-f", "mov"),
        ("-enable_drefs", "0"),
        ("-use_absolute_path", "0"),
        ("-ignore_chapters", "1"),
        ("-probesize", "1048576"),
        ("-analyzeduration", "1000000"),
        ("-max_probe_packets", "64"),
        ("-read_intervals", "%+#64"),
    ):
        assert argv[argv.index(flag) + 1] == value
    assert argv[-2:] == ["-i", str(containers["mp4"])]
    assert options["timeout"] <= 10
    assert options["stdout_limit"] <= 65536 and options["stderr_limit"] <= 8192
    assert not any(
        "KEY" in key or key in {"FFREPORT", "HOME"} for key in options["env"]
    )
    assert options["stdin"] == b""
    captured = capsys.readouterr()
    assert "canary" not in captured.out + captured.err


@pytest.mark.parametrize("failure", [FileNotFoundError, TimeoutError, Unavailable])
async def test_probe_failure_preserves_explicit_retry(containers, monkeypatch, failure):
    async def fail(*args, **kwargs):
        raise failure("authored diagnostic")

    monkeypatch.setattr(video, "bounded_process", fail)
    with pytest.raises(Unavailable, match="^Video verification unavailable$"):
        await video.video_mime(containers["webm"])


async def test_deliberate_reverification_recovers_after_probe_timeout(
    containers, monkeypatch
):
    actual_probe, attempts = video.bounded_process, []

    async def temporary(*args, **kwargs):
        attempts.append(1)
        if len(attempts) == 1:
            raise TimeoutError("authored temporary verifier failure")
        return await actual_probe(*args, **kwargs)

    monkeypatch.setattr(video, "bounded_process", temporary)
    with pytest.raises(Unavailable, match="^Video verification unavailable$"):
        await video.video_mime(containers["mp4"])
    assert await video.video_mime(containers["mp4"]) == "video/mp4"
    assert len(attempts) == 2


@pytest.mark.parametrize(
    "output,diagnostic",
    [
        (b"{invalid authored JSON", b""),
        (b"{}", b""),
        (json.dumps({"format": {"format_name": "matroska,webm"}}).encode(), b""),
        (json.dumps({"format": {"format_name": 123}}).encode(), b""),
        (b"{}", b"authored corrupt-container error"),
    ],
)
async def test_invalid_probe_evidence_is_rejected(
    containers, monkeypatch, output, diagnostic
):
    async def fake(*args, **kwargs):
        return output, diagnostic

    monkeypatch.setattr(video, "bounded_process", fake)
    assert await video.video_mime(containers["mp4"]) is None


async def test_cancelled_probe_propagates_cancellation(containers, monkeypatch):
    async def cancelled(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr(video, "bounded_process", cancelled)
    with pytest.raises(asyncio.CancelledError):
        await video.video_mime(containers["mp4"])


async def test_file_replaced_while_probing_cannot_validate_new_bytes(
    tmp_path, containers, monkeypatch
):
    path = tmp_path / "source.mp4"
    path.write_bytes(containers["mp4"].read_bytes())
    actual_probe = video.bounded_process

    async def changed(*args, **kwargs):
        result = await actual_probe(*args, **kwargs)
        path.write_bytes(b"authored changed bytes")
        return result

    monkeypatch.setattr(video, "bounded_process", changed)
    assert await video.video_mime(path) is None
