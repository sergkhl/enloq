"""Authored exact-byte GIF/sticker proof, renderer limits and probe outages."""

import asyncio
import copy
import gzip
import hashlib
import io
import json
import subprocess
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from enloq.domain import selection_formats as formats
from enloq.domain.models import MediaTooLarge, Refused, Unavailable
from enloq.media_tools import FFMPEG
from PIL import Image


def animation():
    transform = {
        "o": {"a": 0, "k": 100},
        "r": {"a": 0, "k": 0},
        "p": {"a": 0, "k": [256, 256, 0]},
        "a": {"a": 0, "k": [0, 0, 0]},
        "s": {"a": 0, "k": [100, 100, 100]},
    }
    return {
        "v": "5.7.4",
        "fr": 60,
        "ip": 0,
        "op": 60,
        "w": 512,
        "h": 512,
        "nm": "Authored square: ё",
        "ddd": 0,
        "assets": [],
        "layers": [
            {
                "ty": 4,
                "ind": 1,
                "ip": 0,
                "op": 60,
                "st": 0,
                "ks": transform,
                "shapes": [
                    {
                        "ty": "gr",
                        "it": [
                            {
                                "ty": "rc",
                                "p": {"a": 0, "k": [0, 0]},
                                "s": {"a": 0, "k": [128, 128]},
                                "r": {"a": 0, "k": 8},
                            },
                            {
                                "ty": "fl",
                                "c": {"a": 0, "k": [0.1, 0.3, 1, 1]},
                                "o": {"a": 0, "k": 100},
                                "r": 1,
                            },
                            {"ty": "tr", **copy.deepcopy(transform)},
                        ],
                    }
                ],
            }
        ],
    }


def mask(mode="a", expansion=0):
    return {
        "mode": mode,
        "inv": False,
        "o": {"a": 0, "k": 100},
        "x": {"a": 0, "k": expansion},
        "pt": {
            "a": 0,
            "k": {
                "i": [[0, 0]] * 4,
                "o": [[0, 0]] * 4,
                "v": [[-64, -64], [64, -64], [64, 64], [-64, 64]],
                "c": True,
            },
        },
    }


def gradient(stops=2):
    return {
        "ty": "gf",
        "o": {"a": 0, "k": 100},
        "t": 1,
        "s": {"a": 0, "k": [-64, 0]},
        "e": {"a": 0, "k": [64, 0]},
        "g": {
            "p": stops,
            "k": {
                "a": 0,
                "k": [item for i in range(stops) for item in (i / stops, 0.2, 0.4, 1)],
            },
        },
    }


def animated(start, end, *, easing=1):
    return {
        "a": 1,
        "k": [
            {
                "t": 0,
                "s": start if isinstance(start, list) else [start],
                "e": end if isinstance(end, list) else [end],
                "o": {"x": 1 / 3, "y": easing},
                "i": {"x": 2 / 3, "y": easing},
            },
            {"t": 60, "s": end if isinstance(end, list) else [end]},
        ],
    }


def precomp(data, *, copies=1, scale=100):
    asset = {"id": "vector", "layers": copy.deepcopy(data["layers"])}
    transform = copy.deepcopy(data["layers"][0]["ks"])
    transform["s"]["k"] = [scale, scale, 100]
    data["assets"] = [asset]
    data["layers"] = [
        {
            "ty": 0,
            "ind": i + 1,
            "refId": "vector",
            "ks": copy.deepcopy(transform),
            "ip": 0,
            "op": 60,
        }
        for i in range(copies)
    ]


def write_animation(tmp_path, data=None, *, raw=None):
    path = tmp_path / "authored.not-tgs"
    if raw is None:
        raw = json.dumps(
            animation() if data is None else data, ensure_ascii=False
        ).encode()
    path.write_bytes(gzip.compress(raw, mtime=0))
    return path


def raster(tmp_path, *, kind="gif", size=(32, 24), frames=3):
    images = [
        Image.new("RGBA", size, color) for color in ("red", "blue", "green")[:frames]
    ]
    path = tmp_path / "misleading.mp4"
    kwargs = (
        {"save_all": True, "append_images": images[1:], "duration": 100, "loop": 0}
        if frames > 1
        else {}
    )
    images[0].save(path, format="GIF" if kind == "gif" else "WEBP", **kwargs)
    return path


@pytest.fixture(scope="module")
def containers(tmp_path_factory):
    """Generated blue rectangles and silence, with no correspondence or network."""
    directory = tmp_path_factory.mktemp("authored-selected-containers")

    def generate(name, *, codec, size="512x128", fps=10, duration="0.3", audio=False):
        path = directory / name
        args = [
            FFMPEG,
            "-v",
            "error",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=color=blue:size={size}:rate={fps}",
        ]
        if audio:
            args += [
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=mono:sample_rate=8000",
            ]
        args += ["-t", duration, "-c:v", codec]
        if codec == "libx264":
            args += ["-pix_fmt", "yuv420p"]
        if codec.startswith("libvpx"):
            args += ["-deadline", "realtime", "-cpu-used", "8"]
        args += (
            ["-c:a", "libopus" if name.endswith("webm") else "aac"]
            if audio
            else ["-an"]
        )
        subprocess.run(
            [*args, str(path)],
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return path

    return {
        "mp4": generate("silent.mp4", codec="libx264", size="160x120"),
        "mp4_audio": generate("audible.mp4", codec="libx264", audio=True),
        "wrong_mp4_codec": generate("mpeg4.mp4", codec="mpeg4"),
        "webm": generate("silent.webm", codec="libvpx-vp9", fps=30),
        "webm_audio": generate("audible.webm", codec="libvpx-vp9", audio=True),
        "wrong_webm_codec": generate("vp8.webm", codec="libvpx"),
        "wrong_dimensions": generate("small.webm", codec="libvpx-vp9", size="256x256"),
        "too_long": generate("long.webm", codec="libvpx-vp9", duration="3.2", fps=10),
        "too_fast": generate("fast.webm", codec="libvpx-vp9", fps=60),
        "native_edge": generate("edge.webm", codec="libvpx-vp9", duration="3", fps=30),
    }


async def test_actual_gif_is_fully_decoded_and_preserved(tmp_path):
    path = raster(tmp_path)
    before = path.read_bytes()
    facts = await formats.validate_selection(path, "gif")
    assert (facts.format, facts.mime, facts.width, facts.height, facts.frames) == (
        "gif",
        "image/gif",
        32,
        24,
        3,
    )
    assert facts.duration == pytest.approx(0.3)
    assert facts.fps == pytest.approx(10)
    assert facts.sha256 == hashlib.sha256(before).hexdigest()
    assert facts.size == len(before)
    assert path.read_bytes() == before
    with pytest.raises(FrozenInstanceError):
        facts.width = 20


async def test_single_frame_gif_is_valid_without_invented_timing(tmp_path):
    facts = await formats.validate_selection(raster(tmp_path, frames=1), "gif")
    assert facts.frames == 1 and facts.duration == 0 and facts.fps is None


async def test_static_webp_preserves_transparency_and_original_bytes(tmp_path):
    image = Image.new("RGBA", (512, 128), (15, 60, 120, 0))
    path = tmp_path / "not-a-sticker.bin"
    image.save(path, format="WEBP", lossless=True)
    before = path.read_bytes()
    facts = await formats.validate_selection(path, "sticker")
    assert (facts.format, facts.sticker, facts.width, facts.height, facts.frames) == (
        "webp",
        "static",
        512,
        128,
        1,
    )
    assert facts.duration is None and facts.animation_json is None
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "failure",
    [
        "truncated_gif",
        "corrupt_gif",
        "wrong_webp_size",
        "animated_webp",
        "truncated_webp",
        "webp_trailing",
        "png",
    ],
)
async def test_raster_spoofs_corruption_and_wrong_native_format_refuse(
    tmp_path, failure
):
    kind = "gif" if failure.endswith("gif") else "sticker"
    path = raster(
        tmp_path,
        kind=kind,
        size=(512, 128) if kind == "sticker" else (32, 24),
        frames=2 if failure == "animated_webp" else 1,
    )
    body = path.read_bytes()
    if failure.startswith("truncated"):
        path.write_bytes(body[:-1])
    elif failure == "corrupt_gif":
        path.write_bytes(body[:20] + b"\xff" * 12 + b";")
    elif failure == "wrong_webp_size":
        raster(tmp_path, kind="sticker", size=(256, 256), frames=1)
    elif failure == "webp_trailing":
        path.write_bytes(body + b"authored-extra")
    elif failure == "png":
        Image.new("RGB", (512, 128), "blue").save(path, format="PNG")
    with pytest.raises(Refused) as caught:
        await formats.validate_selection(path, kind)
    assert not isinstance(caught.value, Unavailable)


@pytest.mark.parametrize(
    "budget,value",
    [
        ("MAX_GIF_FRAMES", 2),
        ("MAX_DECODED_PIXELS", 32 * 24 * 2),
        ("MAX_GIF_DIMENSION", 20),
    ],
)
async def test_raster_decode_and_allocation_budgets(
    tmp_path, monkeypatch, budget, value
):
    path = raster(tmp_path)
    monkeypatch.setattr(formats, budget, value)
    with pytest.raises(Refused):
        await formats.validate_selection(path, "gif")


async def test_gif_deadline_is_a_between_frame_soft_check(tmp_path, monkeypatch):
    path = raster(tmp_path)
    times = iter([0, 0, 11])
    monkeypatch.setattr(
        formats, "time", SimpleNamespace(monotonic=lambda: next(times, 11))
    )
    with pytest.raises(Refused):
        await formats.validate_selection(path, "gif")


@pytest.mark.parametrize(
    "name,kind,format_name,sticker",
    [
        ("mp4", "gif", "mp4", None),
        ("webm", "sticker", "webm", "video"),
        ("native_edge", "sticker", "webm", "video"),
    ],
)
async def test_complete_native_video_formats_keep_exact_bytes(
    tmp_path, containers, name, kind, format_name, sticker
):
    body = containers[name].read_bytes()
    path = tmp_path / "not-the-declared-format.bin"
    path.write_bytes(body)
    facts = await formats.validate_selection(path, kind)
    assert facts.format == format_name and facts.sticker == sticker
    assert facts.frames == (90 if name == "native_edge" else 9 if name == "webm" else 3)
    assert facts.fps == (30 if kind == "sticker" else 10)
    assert facts.sha256 == hashlib.sha256(body).hexdigest()
    assert path.read_bytes() == body


@pytest.mark.parametrize(
    "name,kind",
    [
        ("mp4_audio", "gif"),
        ("wrong_mp4_codec", "gif"),
        ("webm_audio", "sticker"),
        ("wrong_webm_codec", "sticker"),
        ("wrong_dimensions", "sticker"),
        ("too_long", "sticker"),
        ("too_fast", "sticker"),
        ("mp4", "sticker"),
        ("webm", "gif"),
    ],
)
async def test_audio_wrong_codecs_and_native_bounds_are_definitive(
    containers, name, kind
):
    with pytest.raises(Refused) as caught:
        await formats.validate_selection(containers[name], kind)
    assert not isinstance(caught.value, Unavailable)


async def test_normal_tgs_masks_gradients_precomps_and_unusual_keyframes(tmp_path):
    data = animation()
    layer = data["layers"][0]
    layer["masksProperties"] = [mask(), mask("s", 2)]
    layer["shapes"][0]["it"][1] = gradient()
    layer["ks"]["r"] = {
        "a": 1,
        "k": [
            {
                "t": 0,
                "s": [-12.5],
                "e": [8.5],
                "o": {"x": 0.3, "y": 0},
                "i": {"x": 0.7, "y": 1},
            },
            {"t": 60, "s": [8.5]},
        ],
    }
    data["ip"], data["op"] = 12.5, 72.5
    data["fonts"] = {"list": []}
    precomp(data)
    path = write_animation(tmp_path, data)
    before = path.read_bytes()
    facts = await formats.validate_selection(path, "sticker")
    assert (
        facts.format,
        facts.sticker,
        facts.width,
        facts.height,
        facts.fps,
        facts.duration,
    ) == ("tgs", "animated", 512, 512, 60, 1)
    assert json.loads(facts.animation_json) == data
    assert set(facts.features) == {"non_additive_mask", "expanded_mask"}
    assert facts.sha256 == hashlib.sha256(before).hexdigest()
    assert path.read_bytes() == before


@pytest.mark.parametrize(
    "mode,supported",
    [
        ("a", True),
        ("n", True),
        ("s", True),
        ("i", True),
        ("d", False),
        ("f", False),
        ("l", False),
    ],
)
async def test_tgs_mask_support_is_precise_for_svg(tmp_path, mode, supported):
    data = animation()
    data["layers"][0]["masksProperties"] = [mask(mode)]
    facts = await formats.validate_selection(write_animation(tmp_path, data), "sticker")
    assert ("unsupported_mask_mode" not in facts.features) is supported


@pytest.mark.parametrize(
    "matte,supported", [(1, True), (2, True), (3, True), (4, False)]
)
async def test_tgs_matte_support_and_dependency_proof(tmp_path, matte, supported):
    data = animation()
    second = copy.deepcopy(data["layers"][0])
    second.update(ind=2, tt=matte)
    data["layers"].append(second)
    facts = await formats.validate_selection(write_animation(tmp_path, data), "sticker")
    assert "track_matte" in facts.features
    assert ("inverted_luminance_matte" not in facts.features) is supported


@pytest.mark.parametrize(
    "feature",
    [
        "expressions",
        "effects",
        "3d",
        "segments",
        "image",
        "raster_data",
        "external",
        "font",
        "text",
        "audio",
        "repeaters",
        "merge_paths",
        "bad_canvas",
        "too_long",
        "fps",
        "bad_path",
        "many_star_points",
        "wide_stroke",
        "expanded_mask",
        "huge_scale",
        "parent_cycle",
        "matte_cycle",
        "precomp_cycle",
        "missing_asset",
        "bad_gradient",
    ],
)
async def test_unsafe_or_malformed_tgs_is_definitively_rejected(tmp_path, feature):
    data = animation()
    layer = data["layers"][0]
    if feature == "expressions":
        layer["ks"]["r"]["x"] = "authoredExpression()"
    elif feature == "effects":
        layer["ef"] = [{"ty": 29, "v": 10}]
    elif feature == "3d":
        layer["ddd"] = 1
    elif feature == "segments":
        data["segments"] = [{"time": 1}]
    elif feature in ("image", "raster_data", "external"):
        data["assets"] = [
            {
                "id": "raster",
                "layers": [],
                "p": "https://example.invalid/authored.png"
                if feature == "external"
                else "data:image/png;base64,authored"
                if feature == "raster_data"
                else "square.png",
            }
        ]
    elif feature == "font":
        data["fonts"] = {
            "list": [{"fName": "authored", "fPath": "https://example.invalid/font"}]
        }
    elif feature in ("text", "audio"):
        layer["ty"] = 5 if feature == "text" else 6
    elif feature in ("repeaters", "merge_paths"):
        layer["shapes"] = [
            {"ty": "rp" if feature == "repeaters" else "mm", "c": {"a": 0, "k": 100}}
        ]
    elif feature == "bad_canvas":
        data["w"] = 511
    elif feature == "too_long":
        data["op"] = 181
    elif feature == "fps":
        data["fr"] = 30
    elif feature == "bad_path":
        layer["masksProperties"] = [mask()]
        layer["masksProperties"][0]["pt"]["k"]["i"].pop()
    elif feature == "many_star_points":
        layer["shapes"] = [{"ty": "sr", "pt": {"a": 0, "k": 1000}}]
    elif feature == "wide_stroke":
        layer["shapes"].append({"ty": "st", "w": {"a": 0, "k": 129}})
    elif feature == "expanded_mask":
        layer["masksProperties"] = [mask(expansion=65)]
    elif feature == "huge_scale":
        layer["ks"]["s"]["k"] = [401, 100, 100]
    elif feature == "parent_cycle":
        layer["parent"] = 1
    elif feature == "matte_cycle":
        layer.update(tt=1, tp=1)
    elif feature in ("precomp_cycle", "missing_asset"):
        precomp(data)
        if feature == "precomp_cycle":
            data["assets"][0]["layers"] = copy.deepcopy(data["layers"])
        else:
            data["assets"] = []
    elif feature == "bad_gradient":
        layer["shapes"][0]["it"][1] = gradient()
        layer["shapes"][0]["it"][1]["g"]["k"]["k"] = [0, 1]
    with pytest.raises(Refused) as caught:
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")
    assert str(caught.value) == formats._INVALID
    assert not isinstance(caught.value, Unavailable)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"v":"5.7.4","v":"5.7.4"}',
        b'{"v":"5.7.4","__proto__":{}}',
        b'{"v":"5.7.4","constructor":{}}',
        b'{"v":"5.7.4","fr":NaN}',
        b'{"v":"5.7.4","fr":Infinity}',
        b'{"v":"5.7.4","fr":1e999}',
        b"{" * 65 + b"}" * 65,
        b"\xff",
    ],
)
async def test_ambiguous_nonfinite_deep_and_invalid_json_refuse(tmp_path, raw):
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, raw=raw), "sticker")


@pytest.mark.parametrize(
    "damage", ["bomb", "crc", "truncated", "concatenated", "trailing"]
)
async def test_gzip_bombs_and_noncomplete_streams_refuse(tmp_path, damage):
    path = write_animation(tmp_path)
    body = path.read_bytes()
    if damage == "bomb":
        path.write_bytes(gzip.compress(b" " * (formats.ANIMATION_BYTES + 1), mtime=0))
    elif damage == "crc":
        path.write_bytes(body[:-8] + bytes([body[-8] ^ 1]) + body[-7:])
    elif damage == "truncated":
        path.write_bytes(body[:-1])
    elif damage == "concatenated":
        path.write_bytes(body + body)
    else:
        path.write_bytes(body + b"authored")
    with pytest.raises(Refused):
        await formats.validate_selection(path, "sticker")


@pytest.mark.parametrize(
    "budget,value",
    [
        ("MAX_JSON_NODES", 20),
        ("MAX_SHAPES", 2),
        ("MAX_JSON_STRING", 10),
        ("MAX_EXPANDED_NODES", 150),
        ("MAX_EXPANDED_LAYERS", 3),
        ("MAX_EXPANDED_SHAPES", 8),
        ("MAX_EXPANDED_MASKS", 2),
        ("MAX_EXPANDED_GRADIENT_STOPS", 4),
    ],
)
async def test_tgs_real_and_expanded_resource_budgets(
    tmp_path, monkeypatch, budget, value
):
    data = animation()
    data["layers"][0]["masksProperties"] = [mask()]
    data["layers"][0]["shapes"][0]["it"][1] = gradient()
    precomp(data, copies=3)
    monkeypatch.setattr(formats, budget, value)
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")


async def test_noncyclic_precomp_expansion_cannot_hide_exponential_work(tmp_path):
    data = animation()
    base = copy.deepcopy(data["layers"])
    transform = copy.deepcopy(base[0]["ks"])
    data["assets"] = [{"id": "node0", "layers": base}]
    for level in range(1, 12):
        data["assets"].append(
            {
                "id": f"node{level}",
                "layers": [
                    {
                        "ty": 0,
                        "ind": i + 1,
                        "refId": f"node{level - 1}",
                        "ks": copy.deepcopy(transform),
                    }
                    for i in range(2)
                ],
            }
        )
    data["layers"] = [{"ty": 0, "ind": 1, "refId": "node11", "ks": transform}]
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")


async def test_transform_expansion_across_precomp_and_parent_is_bounded(tmp_path):
    data = animation()
    data["layers"][0]["ks"]["s"]["k"] = [400, 400, 100]
    precomp(data, scale=400)
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")


@pytest.mark.parametrize("parent_scale,accepted", [(200, True), (400, False)])
async def test_child_group_transform_uses_child_after_null_parent_walk(
    tmp_path, parent_scale, accepted
):
    data = animation()
    child = data["layers"][0]
    child.update(ind=2, parent=1)
    child["shapes"][0]["it"][-1]["s"]["k"] = [300, 300, 100]
    parent = {"ty": 3, "ind": 1, "ks": copy.deepcopy(child["ks"])}
    parent["ks"]["s"]["k"] = [parent_scale, parent_scale, 100]
    data["layers"] = [parent, child]
    path = write_animation(tmp_path, data)
    if accepted:
        assert (await formats.validate_selection(path, "sticker")).format == "tgs"
    else:
        with pytest.raises(Refused):
            await formats.validate_selection(path, "sticker")


async def test_direct_shape_transform_composes_with_layer_scale(tmp_path):
    data = animation()
    layer = data["layers"][0]
    layer["ks"]["s"]["k"] = [300, 300, 100]
    transform = copy.deepcopy(layer["shapes"][0]["it"][-1])
    transform["s"]["k"] = [400, 400, 100]
    layer["shapes"].append(transform)
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")


@pytest.mark.parametrize(
    "skew,accepted",
    [(0, True), (20, True), (-30, True), (89.999, False), (90, False), (270, False)],
)
async def test_skew_uses_actual_shear_norm_and_rejects_singular_angles(
    tmp_path, skew, accepted
):
    data = animation()
    data["layers"][0]["ks"].update(sk={"a": 0, "k": skew}, sa={"a": 0, "k": 45})
    path = write_animation(tmp_path, data)
    if accepted:
        assert (await formats.validate_selection(path, "sticker")).format == "tgs"
    else:
        with pytest.raises(Refused):
            await formats.validate_selection(path, "sticker")


@pytest.mark.parametrize(
    "property_name",
    ["scale", "skew", "mask", "stroke", "position", "motion", "rectangle"],
)
async def test_interpolated_overshoot_is_bounded_beyond_endpoints(
    tmp_path, property_name
):
    data = animation()
    layer = data["layers"][0]
    if property_name == "scale":
        layer["ks"]["s"] = animated([100, 100, 100], [400, 400, 100], easing=4096)
    elif property_name == "skew":
        layer["ks"].update(sk=animated(0, 30, easing=4), sa={"a": 0, "k": 0})
    elif property_name == "mask":
        layer["masksProperties"] = [mask()]
        layer["masksProperties"][0]["x"] = animated(0, -64, easing=4096)
    elif property_name == "stroke":
        layer["shapes"].append({"ty": "st", "w": animated(1, 128, easing=4096)})
    elif property_name == "position":
        layer["ks"]["p"] = animated([0, 0, 0], [512, 512, 0], easing=4096)
    elif property_name == "motion":
        layer["ks"]["p"] = animated([0, 0, 0], [512, 512, 0])
        layer["ks"]["p"]["k"][0].update(to=[4096, 0, 0], ti=[4096, 0, 0])
    else:
        layer["shapes"][0]["it"][0]["s"] = animated([64, 64], [512, 512], easing=4096)
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")


async def test_ordinary_animated_skew_and_small_easing_overshoot_remain_supported(
    tmp_path,
):
    data = animation()
    layer = data["layers"][0]
    layer["ks"].update(sk=animated(0, 15, easing=1.1), sa={"a": 0, "k": 0})
    layer["ks"]["s"] = animated([100, 100, 100], [110, 110, 100], easing=1.2)
    layer["masksProperties"] = [mask()]
    layer["masksProperties"][0]["x"] = animated(0, 2, easing=1.1)
    assert (
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")
    ).format == "tgs"


@pytest.mark.parametrize(
    "damage", ["collinear_overshoot", "hold_motion", "nonlinear_overshoot"]
)
async def test_motion_optimizer_and_hold_flags_cannot_bypass_rendered_range(
    tmp_path, damage
):
    data = animation()
    prop = animated(
        [0, 0, 0], [512, 512, 0], easing=4096 if damage != "hold_motion" else 1
    )
    key = prop["k"][0]
    key.update(to=[100, 100, 0], ti=[-100, -100, 0])
    if damage == "hold_motion":
        key.update(h=1, to=[1_000_000, 0, 0])
    elif damage == "nonlinear_overshoot":
        key["to"] = [100, 200, 0]
    data["layers"][0]["ks"]["p"] = prop
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")


async def test_ordinary_motion_controls_remain_supported(tmp_path):
    data = animation()
    prop = animated([0, 0, 0], [128, 128, 0])
    prop["k"][0].update(to=[32, 64, 0], ti=[-32, -64, 0])
    data["layers"][0]["ks"]["p"] = prop
    assert (
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")
    ).format == "tgs"


async def test_equal_endpoint_hold_scale_cannot_hide_spatial_expansion(tmp_path):
    data = animation()
    prop = animated([100, 100, 100], [100, 100, 100])
    prop["k"][0].update(h=1, to=[4000, 4000, 0], ti=[4000, 4000, 0])
    data["layers"][0]["ks"]["s"] = prop
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")


async def test_ordinary_nonspatial_hold_keyframe_remains_supported(tmp_path):
    data = animation()
    prop = animated([100, 100, 100], [200, 200, 100])
    prop["k"][0] = {"t": 0, "s": [100, 100, 100], "h": 1}
    data["layers"][0]["ks"]["s"] = prop
    assert (
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")
    ).format == "tgs"


@pytest.mark.parametrize(
    "damage",
    [
        "x_outside",
        "duplicate_time",
        "missing_easing",
        "different_width",
        "path_overshoot",
    ],
)
async def test_malformed_easing_and_keyframes_cannot_defeat_range_proof(
    tmp_path, damage
):
    data = animation()
    prop = animated([100, 100, 100], [110, 110, 100])
    data["layers"][0]["ks"]["s"] = prop
    if damage == "x_outside":
        prop["k"][0]["o"]["x"] = 2
    elif damage == "duplicate_time":
        prop["k"][1]["t"] = 0
    elif damage == "missing_easing":
        del prop["k"][0]["o"]
    elif damage == "different_width":
        prop["k"][1]["s"].pop()
    else:
        path = mask()["pt"]["k"]
        data["layers"][0]["masksProperties"] = [mask()]
        data["layers"][0]["masksProperties"][0]["pt"] = {
            "a": 1,
            "k": [
                {
                    "t": 0,
                    "s": [path],
                    "e": [path],
                    "o": {"x": 0.3, "y": 2},
                    "i": {"x": 0.7, "y": 2},
                },
                {"t": 60, "s": [path]},
            ],
        }
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")


@pytest.mark.parametrize("flag", [True, False, 1.0, "1", None])
async def test_json_hold_flags_match_the_renderers_strict_numeric_semantics(
    tmp_path, flag
):
    data = animation()
    prop = animated([100, 100, 100], [400, 400, 100], easing=4096)
    prop["k"][0]["h"] = flag
    data["layers"][0]["ks"]["s"] = prop
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")


@pytest.mark.parametrize("flag", [True, False, 1.0, "1", None])
async def test_animation_flag_is_an_unambiguous_integer(tmp_path, flag):
    data = animation()
    data["layers"][0]["ks"]["s"]["a"] = flag
    with pytest.raises(Refused):
        await formats.validate_selection(write_animation(tmp_path, data), "sticker")


async def test_late_webm_track_definition_is_not_hidden_by_video_selection(
    tmp_path, containers
):
    body = containers["webm"].read_bytes()
    stream = io.BytesIO(body)
    _, length = formats._element(stream, len(body))
    stream.seek(length, 1)
    segment_start = stream.tell()
    _, length = formats._element(stream, len(body))
    content_start = stream.tell()
    assert length is not None
    duplicate = None
    while stream.tell() < len(body):
        start = stream.tell()
        kind, length = formats._element(stream, len(body))
        stop = stream.tell() + length
        if kind == 0x1654AE6B:
            duplicate = body[start:stop]
        stream.seek(stop)
    assert duplicate is not None
    updated = bytearray(body + duplicate)
    size_width = content_start - segment_start - 4
    updated[segment_start + 4 : content_start] = (
        (1 << (size_width * 7)) | (len(updated) - content_start)
    ).to_bytes(size_width, "big")
    path = tmp_path / "late-tracks.webm"
    path.write_bytes(updated)
    with pytest.raises(Refused) as caught:
        await formats.validate_selection(path, "sticker")
    assert not isinstance(caught.value, Unavailable)


@pytest.mark.parametrize(
    "kind,limit", [("gif", formats.GIF_BYTES), ("sticker", formats.STICKER_BYTES)]
)
async def test_file_cap_precedes_every_parser_and_probe(
    tmp_path, monkeypatch, kind, limit
):
    path = tmp_path / "too-large.bin"
    with path.open("wb") as file:
        file.truncate(limit + 1)

    async def unexpected(*args, **kwargs):
        raise AssertionError("oversize bytes must not probe")

    monkeypatch.setattr(formats, "bounded_process", unexpected)
    with pytest.raises(MediaTooLarge):
        await formats.validate_selection(path, kind)


@pytest.mark.parametrize("item", ["missing", "empty", "directory", "symlink"])
async def test_only_complete_regular_private_files_are_candidates(tmp_path, item):
    path = tmp_path / item
    if item == "empty":
        path.touch()
    elif item == "directory":
        path.mkdir()
    elif item == "symlink":
        path.symlink_to(write_animation(tmp_path))
    with pytest.raises(Refused):
        await formats.validate_selection(path, "sticker")


async def test_expected_digest_binds_dispatch_and_renderer_reads(tmp_path):
    path = write_animation(tmp_path)
    proof = await formats.validate_selection(path, "sticker")
    assert (
        await formats.validate_selection(path, "sticker", expected_sha256=proof.sha256)
        == proof
    )
    with pytest.raises(Refused, match="integrity changed"):
        await formats.validate_selection(path, "sticker", expected_sha256="0" * 64)
    data = animation()
    data["nm"] = "Second authored revision"
    write_animation(tmp_path, data)
    with pytest.raises(Refused, match="integrity changed"):
        await formats.validate_selection(path, "sticker", expected_sha256=proof.sha256)


async def test_file_replacement_during_probe_refuses_changed_bytes(
    tmp_path, containers, monkeypatch
):
    path = tmp_path / "replaced.bin"
    path.write_bytes(containers["mp4"].read_bytes())
    original = formats._probe

    async def changing(*args, **kwargs):
        result = await original(*args, **kwargs)
        if kwargs.get("complete"):
            path.write_bytes(path.read_bytes() + b"authored change")
        return result

    monkeypatch.setattr(formats, "_probe", changing)
    with pytest.raises(Refused, match="integrity changed"):
        await formats.validate_selection(path, "gif")


@pytest.mark.parametrize(
    "outage",
    [
        FileNotFoundError("private path"),
        TimeoutError("private frame"),
        Unavailable("private diagnostic"),
    ],
)
async def test_transient_probe_failure_keeps_bytes_and_can_retry(
    containers, monkeypatch, outage
):
    path = containers["mp4"]
    before = path.read_bytes()
    original = formats.bounded_process

    async def fail(*args, **kwargs):
        raise outage

    monkeypatch.setattr(formats, "bounded_process", fail)
    with pytest.raises(Unavailable, match="^Media verification unavailable$"):
        await formats.validate_selection(path, "gif")
    assert path.read_bytes() == before
    monkeypatch.setattr(formats, "bounded_process", original)
    assert (await formats.validate_selection(path, "gif")).sha256 == hashlib.sha256(
        before
    ).hexdigest()


async def test_probe_cancellation_propagates_without_a_format_verdict(
    containers, monkeypatch
):
    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError

    monkeypatch.setattr(formats, "bounded_process", cancel)
    with pytest.raises(asyncio.CancelledError):
        await formats.validate_selection(containers["mp4"], "gif")


async def test_probe_restrictions_prove_all_streams_and_bound_complete_decode(
    containers, monkeypatch
):
    original = formats.bounded_process
    calls = []

    async def inspect(argv, **kwargs):
        calls.append((argv, kwargs))
        return await original(argv, **kwargs)

    monkeypatch.setattr(formats, "bounded_process", inspect)
    await formats.validate_selection(containers["mp4"], "gif")
    await formats.validate_selection(containers["webm"], "sticker")
    assert len(calls) == 4
    for argv, options in calls:
        assert "-select_streams" not in argv and "-read_intervals" not in argv
        assert argv[argv.index("-protocol_whitelist") + 1] == "file"
        assert argv[argv.index("-max_alloc") + 1] == "16777216"
        assert options["stdout_limit"] == 65536 and options["stderr_limit"] == 8192
        assert options["timeout"] in (10, 20)
        assert options["env"] == {
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
            "LANG": "C",
        }
    assert "-count_frames" in calls[1][0]
    assert "-show_frames" in calls[3][0]
    assert calls[0][0][calls[0][0].index("-enable_drefs") + 1] == "0"
    assert calls[0][0][calls[0][0].index("-use_absolute_path") + 1] == "0"


async def test_successful_but_corrupt_probe_output_is_content_free(
    tmp_path, containers, monkeypatch
):
    async def malformed(*args, **kwargs):
        return b"private non-JSON", b""

    monkeypatch.setattr(formats, "bounded_process", malformed)
    with pytest.raises(Unavailable, match="^Media verification unavailable$"):
        await formats.validate_selection(containers["mp4"], "gif")

    async def decoder_error(*args, **kwargs):
        return b"{}", b"private corrupt frame body"

    monkeypatch.setattr(formats, "bounded_process", decoder_error)
    with pytest.raises(
        Refused, match="^Media format is unsupported, unsafe or corrupt$"
    ):
        await formats.validate_selection(containers["mp4"], "gif")


async def test_duplicate_movie_and_late_track_tables_cannot_hide_audio(
    tmp_path, containers
):
    original = containers["mp4"].read_bytes()
    offset, movie = 0, None
    while offset < len(original):
        length = int.from_bytes(original[offset : offset + 4], "big")
        if original[offset + 4 : offset + 8] == b"moov":
            movie = original[offset : offset + length]
        offset += length
    assert movie is not None
    path = tmp_path / "duplicate.mp4"
    path.write_bytes(original + movie)
    with pytest.raises(Refused) as caught:
        await formats.validate_selection(path, "gif")
    assert not isinstance(caught.value, Unavailable)


async def test_corrupt_container_spoof_is_not_a_magic_only_format_claim(tmp_path):
    path = tmp_path / "magic.mp4"

    def box(kind, body):
        return (8 + len(body)).to_bytes(4, "big") + kind + body

    path.write_bytes(
        box(b"ftyp", b"isom\x00\x00\x00\x00mp42")
        + box(b"moov", b"authored")
        + box(b"mdat", b"not h264")
    )
    with pytest.raises(Refused):
        await formats.validate_selection(path, "gif")


async def test_animated_webp_is_not_reclassified_as_a_tgs_or_gif(tmp_path):
    path = raster(tmp_path, kind="sticker", size=(512, 128), frames=3)
    before = path.read_bytes()
    with pytest.raises(Refused):
        await formats.validate_selection(path, "sticker")
    with pytest.raises(Refused):
        await formats.validate_selection(path, "gif")
    assert path.read_bytes() == before


def test_pillow_webp_uses_installed_decoder_for_authored_bytes():
    target = io.BytesIO()
    Image.new("RGBA", (512, 64), "blue").save(target, format="WEBP")
    target.seek(0)
    with Image.open(target) as image:
        image.load()
        assert image.format == "WEBP" and image.size == (512, 64)
