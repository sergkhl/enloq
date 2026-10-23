"""Local exact-byte and decoded-geometry proof for authored browser assets.

No server/browser/native/provider activity. TGS structure is validated here;
actual browser animation/rendering remains a separate GS4/GS6 gate.
"""

import hashlib
import json

import pytest
from enloq.domain.models import Refused
from enloq.domain.selection_formats import (
    GIF_BYTES,
    STICKER_BYTES,
    validate_selection,
)
from enloq.inference.runner import bounded_process
from enloq.media_tools import FFMPEG
from PIL import Image

from scripts.gif_sticker_fixture import (
    ANIMATION_INSTANCES,
    SIZE,
    build_assets,
)


async def decoded_frames(path, *, webm=False):
    """Two real decoded frames; selecting the VP9 decoder preserves alpha."""
    output, _ = await bounded_process(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-protocol_whitelist",
            "file,pipe",
            "-threads",
            "1",
            *(["-c:v", "libvpx-vp9"] if webm else []),
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            r"select=eq(n\,0)+eq(n\,15)",
            "-fps_mode",
            "passthrough",
            "-frames:v",
            "2",
            "-an",
            "-threads",
            "1",
            "-pix_fmt",
            "rgba",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
        stdin=b"",
        cwd=path.parent,
        env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "LANG": "C"},
        timeout=15,
        stdout_limit=2 * SIZE * SIZE * 4 + 1024,
        stderr_limit=4096,
    )
    frame_bytes = SIZE * SIZE * 4
    assert len(output) == 2 * frame_bytes
    return output[:frame_bytes], output[frame_bytes:]


async def test_authored_assets_preserve_exact_validated_bytes_and_decoded_geometry(
    tmp_path,
):
    bundle = await build_assets(tmp_path)
    assert {name: asset.facts.format for name, asset in bundle.media.items()} == {
        "static": "webp",
        "animated": "tgs",
        "video": "webm",
        "gif_mp4": "mp4",
        "gif": "gif",
    }
    for asset in bundle.media.values():
        source = asset.path.read_bytes()
        proof = await validate_selection(
            asset.path, asset.facts.kind, expected_sha256=asset.facts.sha256
        )
        assert proof.sha256 == hashlib.sha256(source).hexdigest()
        assert asset.path.read_bytes() == source
        assert (proof.width, proof.height) == (512, 512)
        assert proof.size < 64 * 1024
        assert asset.path.stat().st_mode & 0o777 == 0o600
    with Image.open(bundle.media["static"].path) as image:
        rgba = image.convert("RGBA")
        assert rgba.getpixel((0, 0))[3] == 0
        assert rgba.getpixel((256, 256))[3] == 255
        assert rgba.getpixel((256, 68)) != rgba.getpixel((256, 256))
    for name in ("video", "gif_mp4"):
        asset = bundle.media[name]
        assert (asset.facts.duration, asset.facts.fps, asset.facts.frames) == (
            2,
            30,
            60,
        )
        first, moved = await decoded_frames(asset.path, webm=name == "video")
        assert first != moved
        if name == "video":
            # VP9's lossy alpha may round a transparent corner to 1/255.
            assert max(first[3], moved[3]) <= 2
    with Image.open(bundle.media["gif"].path) as image:
        assert image.n_frames == 3 and image.info["loop"] == 0
        first = image.convert("RGBA").tobytes()
        image.seek(1)
        assert image.convert("RGBA").tobytes() != first
    animation = bundle.media["animated"].facts
    assert (animation.duration, animation.fps, animation.frames) == (2, 60, 120)
    document = json.loads(animation.animation_json)
    assert tuple(layer["nm"] for layer in document["layers"]) == ANIMATION_INSTANCES
    assert len({layer["ind"] for layer in document["layers"]}) == 2
    assert len({layer["refId"] for layer in document["layers"]}) == 1
    moving = document["assets"][0]["layers"][0]
    assert moving["masksProperties"][0]["mode"] == "a"
    assert moving["ks"]["p"]["a"] == 1
    assert any(shape["ty"] == "gf" for shape in moving["shapes"][0]["it"])
    for sample in bundle.failures.values():
        assert sample.path.stat().st_size < 64 * 1024
        if sample.validate_bytes:
            with pytest.raises(Refused):
                await validate_selection(sample.path, sample.kind)
        else:
            limit = GIF_BYTES if sample.kind == "gif" else STICKER_BYTES
            assert sample.declared_size > limit
            assert sample.failure == "too_large"
