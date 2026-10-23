"""Exact, local-only GIF/sticker proof for retained selection and send bytes.

These validators preserve every source byte. Declared MIME, names and native
attributes are not content proof. Safe animation JSON is a rendering derivative;
its digest still identifies the unchanged retained gzip source, never a rewrite.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import math
import stat
import time
import warnings
import zlib
from dataclasses import dataclass
from fractions import Fraction
from pathlib import Path
from typing import Literal

from PIL import Image

from ..inference.runner import bounded_process
from ..media_tools import FFPROBE
from .models import MediaTooLarge, Refused, Unavailable
from .video import _element, _mp4, _webm

GIF_BYTES = 50 * 1024 * 1024
STICKER_BYTES = 5 * 1024 * 1024
ANIMATION_BYTES = 4 * 1024 * 1024
MAX_JSON_DEPTH = 64
MAX_JSON_NODES = 100_000
MAX_JSON_STRING = 4096
MAX_ASSETS = 128
MAX_LAYERS = 256
MAX_SHAPES = 2048
MAX_POINTS = 50_000
MAX_KEYFRAMES = 20_000
MAX_MASKS = 128
MAX_GRADIENT_STOPS = 2048
MAX_EXPANDED_LAYERS = 1024
MAX_EXPANDED_SHAPES = 4096
MAX_EXPANDED_POINTS = 100_000
MAX_EXPANDED_NODES = 200_000
MAX_EXPANDED_MASKS = 256
MAX_EXPANDED_GRADIENT_STOPS = 4096
MAX_TRANSFORM_SCALE = 8
MAX_GEOMETRY_NUMBER = 4096
MAX_GIF_FRAMES = 6000
MAX_GIF_DIMENSION = 4096
MAX_GIF_PIXELS = 16_777_216
MAX_DECODED_PIXELS = 2_000_000_000
_INVALID = "Media format is unsupported, unsafe or corrupt"
_VIDEO_ENTRIES = (
    "format=format_name,duration:"
    "stream=index,codec_type,codec_name,width,height,duration,"
    "r_frame_rate,avg_frame_rate,nb_read_frames:stream_disposition=attached_pic"
)
_SHAPES = frozenset(
    {"gr", "sh", "rc", "el", "fl", "st", "gf", "gs", "tm", "tr", "rd", "sr"}
)


@dataclass(frozen=True)
class SelectionFormat:
    kind: Literal["gif", "sticker"]
    format: Literal["mp4", "gif", "webp", "tgs", "webm"]
    mime: str
    sha256: str
    size: int
    width: int
    height: int
    duration: float | None = None
    fps: float | None = None
    frames: int | None = None
    sticker: Literal["static", "animated", "video"] | None = None
    animation_json: str | None = None
    features: tuple[str, ...] = ()


def _number(value) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError("Invalid animation number")
    if abs(value) > 1_000_000:
        raise ValueError("Animation number exceeds bounds")
    return value


def _dimensions(width, height, *, sticker=False):
    if type(width) is not int or type(height) is not int:
        raise ValueError("Invalid media dimensions")
    if sticker:
        if not (0 < min(width, height) <= max(width, height) == 512):
            raise ValueError("Invalid sticker dimensions")
    elif not (
        0 < width <= MAX_GIF_DIMENSION
        and 0 < height <= MAX_GIF_DIMENSION
        and width * height <= MAX_GIF_PIXELS
    ):
        raise ValueError("GIF dimensions exceed bounds")


def _digest(path: Path, limit: int) -> tuple[str, int]:
    digest, size = hashlib.sha256(), 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(65536), b""):
            size += len(chunk)
            if size > limit:
                raise MediaTooLarge("Selected media exceeds the file limit")
            digest.update(chunk)
    return digest.hexdigest(), size


def _gif_structure(path: Path):
    """Require complete bounded GIF blocks, including every frame and trailer."""
    with path.open("rb") as source:
        size, blocks, frames = path.stat().st_size, 0, 0

        def read(length):
            value = source.read(length)
            if len(value) != length:
                raise ValueError("Incomplete GIF block")
            return value

        def skip(length):
            if source.tell() + length > size:
                raise ValueError("Incomplete GIF block")
            source.seek(length, 1)

        def subblocks():
            nonlocal blocks
            while True:
                blocks += 1
                if blocks > 262_144:
                    raise ValueError("GIF block complexity exceeds bounds")
                length = read(1)[0]
                if not length:
                    return
                skip(length)

        if read(6) not in (b"GIF87a", b"GIF89a"):
            raise ValueError("Not a GIF")
        screen = read(7)
        width, height = (
            int.from_bytes(screen[:2], "little"),
            int.from_bytes(screen[2:4], "little"),
        )
        _dimensions(width, height)
        if screen[4] & 128:
            skip(3 * (1 << ((screen[4] & 7) + 1)))
        while True:
            blocks += 1
            if blocks > 262_144:
                raise ValueError("GIF block complexity exceeds bounds")
            kind = read(1)[0]
            if kind == 0x3B:
                if not frames or source.tell() != size:
                    raise ValueError("Incomplete or trailing GIF content")
                return width, height, frames
            if kind == 0x21:
                read(1)
                subblocks()
            elif kind == 0x2C:
                descriptor = read(9)
                left, top, frame_width, frame_height = (
                    int.from_bytes(descriptor[index : index + 2], "little")
                    for index in (0, 2, 4, 6)
                )
                if not (
                    0 < frame_width <= width - left and 0 < frame_height <= height - top
                ):
                    raise ValueError("GIF frame exceeds canvas")
                frames += 1
                if frames > MAX_GIF_FRAMES:
                    raise ValueError("GIF frame count exceeds bounds")
                if descriptor[8] & 128:
                    skip(3 * (1 << ((descriptor[8] & 7) + 1)))
                if not 2 <= read(1)[0] <= 8:
                    raise ValueError("Invalid GIF code size")
                subblocks()
            else:
                raise ValueError("Invalid GIF block")


def _still_or_gif(path: Path, kind: str) -> dict:
    """Fully decode every bounded frame; a plausible header is insufficient."""
    deadline = time.monotonic() + 10
    gif = _gif_structure(path) if kind == "gif" else None
    with path.open("rb") as source:
        header = source.read(12)
        if kind == "sticker" and (
            header[:4] != b"RIFF"
            or header[8:12] != b"WEBP"
            or int.from_bytes(header[4:8], "little") + 8 != path.stat().st_size
        ):
            raise ValueError("Not a complete WebP")
    with (
        warnings.catch_warnings(
            action="error", category=Image.DecompressionBombWarning
        ),
        Image.open(path) as image,
    ):
        expected = "WEBP" if kind == "sticker" else "GIF"
        if image.format != expected:
            raise ValueError("Unexpected decoded format")
        width, height = image.size
        _dimensions(width, height, sticker=kind == "sticker")
        frames, duration = 0, 0.0
        while True:
            if frames >= (1 if kind == "sticker" else MAX_GIF_FRAMES):
                raise ValueError("Animation exceeds frame limit")
            if (
                frames + 1
            ) * width * height > MAX_DECODED_PIXELS or time.monotonic() > deadline:
                raise ValueError("Animation exceeds decode limit")
            _dimensions(*image.size, sticker=kind == "sticker")
            image.load()
            frames += 1
            delay = image.info.get("duration", 0)
            if type(delay) not in (int, float) or not math.isfinite(delay) or delay < 0:
                raise ValueError("Invalid GIF timing")
            duration += delay / 1000
            try:
                image.seek(frames)
            except EOFError:
                break
        if gif is not None and gif != (width, height, frames):
            raise ValueError("Incomplete GIF decode")
        return {
            "format": "webp" if kind == "sticker" else "gif",
            "mime": "image/webp" if kind == "sticker" else "image/gif",
            "width": width,
            "height": height,
            "frames": frames,
            "duration": None if kind == "sticker" else duration,
            "fps": None if kind == "sticker" or not duration else frames / duration,
            "sticker": "static" if kind == "sticker" else None,
        }


def _json_depth(raw: bytes):
    depth, quoted, escaped = 0, False, False
    for value in raw:
        if quoted:
            if escaped:
                escaped = False
            elif value == 92:
                escaped = True
            elif value == 34:
                quoted = False
        elif value == 34:
            quoted = True
        elif value in (91, 123):
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise ValueError("Animation nesting exceeds bounds")
        elif value in (93, 125):
            depth -= 1


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result or key in {"__proto__", "prototype", "constructor"}:
            raise ValueError("Unsafe or ambiguous animation object")
        result[key] = value
    return result


def _reject_constant(_):
    raise ValueError("Nonfinite animation number")


def _path(value):
    if not all(key in value for key in ("i", "o", "v")):
        return 0
    parts = [value[key] for key in ("i", "o", "v")]
    if (
        not all(isinstance(part, list) for part in parts)
        or len({len(part) for part in parts}) != 1
    ):
        raise ValueError("Invalid vector path")
    for part in parts:
        for point in part:
            if not isinstance(point, list) or len(point) != 2:
                raise ValueError("Invalid vector point")
            for coordinate in point:
                if abs(_number(coordinate)) > 16_384:
                    raise ValueError("Vector coordinate exceeds bounds")
    for vertex, incoming, outgoing in zip(parts[2], parts[0], parts[1]):
        if any(
            abs(position + tangent) > MAX_GEOMETRY_NUMBER
            for position, tangent in zip(vertex, incoming)
        ) or any(
            abs(position + tangent) > MAX_GEOMETRY_NUMBER
            for position, tangent in zip(vertex, outgoing)
        ):
            raise ValueError("Bezier geometry exceeds bounds")
    return len(parts[0])


def _easing(key, component=0):
    controls = []
    for side in ("o", "i"):
        value = key.get(side)
        if value is None:
            # Absent interpolation controls cannot establish a renderer range.
            raise ValueError("Missing animation easing")
        if not isinstance(value, dict):
            raise ValueError("Invalid animation easing")
        for axis in ("x", "y"):
            number = value[axis]
            if isinstance(number, list):
                if not number or len(number) > 1024:
                    raise ValueError("Invalid animation easing vector")
                number = number[component] if component < len(number) else number[0]
            number = _number(number)
            if axis == "x" and not 0 <= number <= 1:
                raise ValueError("Nonmonotonic animation easing")
            if axis == "y":
                controls.append(number)
    # A cubic Bezier remains inside its control-point convex hull. With its
    # x controls in [0,1], the renderer evaluates this y hull only for t in[0,1].
    return min(0, *controls), max(1, *controls)


def _property_ranges(value):
    """Conservative rendered ranges, including temporal/spatial overshoot."""

    def vector(item):
        if type(item) in (int, float):
            return [_number(item)]
        if not isinstance(item, list) or not 0 < len(item) <= 1024:
            raise ValueError("Invalid numeric animation value")
        return [_number(number) for number in item]

    if not isinstance(value, dict):
        return [(number, number) for number in vector(value)]
    if "a" in value and (type(value["a"]) is not int or value["a"] not in (0, 1)):
        raise ValueError("Invalid animation flag")
    if value.get("a", 0) == 0:
        return [(number, number) for number in vector(value["k"])]
    keys = value["k"]
    if not isinstance(keys, list) or not 2 <= len(keys) <= MAX_KEYFRAMES:
        raise ValueError("Invalid numeric animation")
    ranges = None
    for position, key in enumerate(keys):
        if not isinstance(key, dict):
            raise ValueError("Invalid numeric keyframe")
        if "h" in key and (type(key["h"]) is not int or key["h"] not in (0, 1)):
            raise ValueError("Invalid hold flag")
        if position and _number(key["t"]) <= _number(keys[position - 1]["t"]):
            raise ValueError("Nonmonotonic keyframe times")
        start = vector(key["s"]) if "s" in key else None
        if position == len(keys) - 1:
            if start is None:
                continue
            segment = [(number, number) for number in start]
        else:
            if start is None:
                raise ValueError("Missing numeric keyframe start")
            next_key = keys[position + 1]
            if not isinstance(next_key, dict):
                raise ValueError("Invalid numeric keyframe")
            end = vector(next_key["s"] if "s" in next_key else key["e"])
            if len(start) != len(end):
                raise ValueError("Incompatible numeric keyframe values")
            motion = key.get("to") is not None
            if motion:
                if key.get("h") == 1 or not 2 <= len(start) <= 3:
                    raise ValueError("Invalid motion keyframe")
                outgoing, incoming = vector(key["to"]), vector(key["ti"])
                if len(outgoing) != len(start) or len(incoming) != len(start):
                    raise ValueError("Incompatible motion controls")
            segment = []
            for component, (left, right) in enumerate(zip(start, end)):
                if key.get("h") == 1:
                    low, high = min(left, right), max(left, right)
                elif motion:
                    minimum, maximum = _easing(key, component)
                    # Lottie removes collinear motion controls and then uses
                    # ordinary interpolation. A unit easing hull proves both
                    # that branch and the bounded sampled Bezier branch.
                    if minimum < 0 or maximum > 1:
                        raise ValueError("Motion easing exceeds bounds")
                    low, high = (
                        min(
                            left,
                            right,
                            left + outgoing[component],
                            right + incoming[component],
                        ),
                        max(
                            left,
                            right,
                            left + outgoing[component],
                            right + incoming[component],
                        ),
                    )
                else:
                    minimum, maximum = _easing(key, component)
                    candidates = (
                        left,
                        right,
                        left + (right - left) * minimum,
                        left + (right - left) * maximum,
                    )
                    low, high = min(candidates), max(candidates)
                segment.append((low, high))
        if ranges is None:
            ranges = segment
        else:
            if len(ranges) != len(segment):
                raise ValueError("Incompatible numeric animation dimensions")
            ranges = [
                (min(old[0], current[0]), max(old[1], current[1]))
                for old, current in zip(ranges, segment)
            ]
    if ranges is None:
        raise ValueError("Missing numeric animation values")
    return ranges


def _scalar_values(value):
    if isinstance(value, dict):
        ranges = _property_ranges(value)
        if len(ranges) != 1:
            raise ValueError("Invalid scalar property")
        return list(ranges[0])
    if isinstance(value, list):
        if len(value) != 1:
            raise ValueError("Invalid scalar vector")
        return _scalar_values(value[0])
    return [_number(value)]


def _vector_values(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid transform property")
    if value.get("s") is True:
        return [
            number
            for axis in ("x", "y", "z")
            if axis in value
            for number in _scalar_values(value[axis])
        ]
    ranges = _property_ranges(value)
    if not 1 <= len(ranges) <= 3:
        raise ValueError("Invalid transform vector")
    return [number for bounds in ranges for number in bounds]


def _transform_scale(value):
    if not isinstance(value, dict):
        raise ValueError("Invalid animation transform")
    for field in ("p", "a"):
        if field in value and any(
            abs(number) > MAX_GEOMETRY_NUMBER for number in _vector_values(value[field])
        ):
            raise ValueError("Transform geometry exceeds bounds")
    scale = (
        max((abs(number) / 100 for number in _vector_values(value["s"])), default=1)
        if "s" in value
        else 1
    )
    if "sk" in value:
        skew = max(abs(number) for number in _scalar_values(value["sk"]))
        if skew > 60:
            raise ValueError("Skew angle exceeds bounds")
        shear = abs(math.tan(math.radians(skew)))
        scale *= (math.sqrt(shear * shear + 4) + shear) / 2
    if scale > 4:
        raise ValueError("Transform scale exceeds bounds")
    return max(1, scale)


def _shape_scale(shapes):
    pending, largest = [(shapes, 1)], 1
    while pending:
        items, scale = pending.pop()
        if not isinstance(items, list):
            raise ValueError("Invalid shape group")
        for item in items:
            if isinstance(item, dict) and item.get("ty") == "tr":
                scale *= _transform_scale(item)
        if scale > MAX_TRANSFORM_SCALE:
            raise ValueError("Expanded shape transform exceeds bounds")
        largest = max(largest, scale)
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid shape")
            if item.get("ty") == "gr":
                group = item.get("it")
                if not isinstance(group, list):
                    raise ValueError("Invalid shape group")
                pending.append((group, scale))
    return largest


def _gradient_stops(value):
    count, prop = value["p"], value["k"]
    if not isinstance(prop, dict):
        raise ValueError("Invalid gradient property")
    if prop.get("a", 0) == 0:
        values = [prop["k"]]
    else:
        keys = prop["k"]
        if not isinstance(keys, list) or not 0 < len(keys) <= MAX_KEYFRAMES:
            raise ValueError("Invalid gradient animation")
        values = [
            key[field]
            for key in keys
            if isinstance(key, dict)
            for field in ("s", "e")
            if field in key
        ]
    if not values:
        raise ValueError("Missing gradient colors")
    stops = 0
    for colors in values:
        if (
            not isinstance(colors, list)
            or not 4 * count <= len(colors) <= 4 * count + 256
            or (len(colors) - 4 * count) % 2
        ):
            raise ValueError("Invalid gradient colors")
        for color in colors:
            _number(color)
        stops = max(stops, count + (len(colors) - 4 * count) // 2)
    return stops


def _walk(value, *, features: set[str]) -> tuple[int, int, int, int, int]:
    pending, nodes, shapes, points, keyframes = [(value, 0, 1)], 0, 0, 0, 0
    masks = gradients = 0
    while pending:
        item, depth, scale = pending.pop()
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ValueError("Animation complexity exceeds bounds")
        if isinstance(item, dict):
            if (
                "k" in item
                and "a" in item
                and (type(item["a"]) is not int or item["a"] not in (0, 1))
            ):
                raise ValueError("Invalid animation flag")
            if item.get("a") == 1 and isinstance(item.get("k"), list):
                keys = item["k"]
                sample = next(
                    (
                        key[field]
                        for key in keys
                        if isinstance(key, dict)
                        for field in ("s", "e")
                        if field in key
                    ),
                    None,
                )
                if type(sample) in (int, float) or (
                    isinstance(sample, list)
                    and sample
                    and all(type(number) in (int, float) for number in sample)
                ):
                    if any(
                        abs(number) > MAX_GEOMETRY_NUMBER
                        for bounds in _property_ranges(item)
                        for number in bounds
                    ):
                        raise ValueError("Interpolated geometry exceeds bounds")
                else:
                    if not 2 <= len(keys) <= MAX_KEYFRAMES:
                        raise ValueError("Invalid path animation")
                    for position, key in enumerate(keys):
                        if not isinstance(key, dict) or (
                            position
                            and _number(key["t"]) <= _number(keys[position - 1]["t"])
                        ):
                            raise ValueError("Nonmonotonic path animation")
                    for key in keys[:-1]:
                        if not isinstance(key, dict):
                            raise ValueError("Invalid path keyframe")
                        if key.get("h") != 1 and _easing(key) != (0, 1):
                            raise ValueError("Path easing overshoot exceeds bounds")
            if item.get("ty") == "gr":
                for transform in item.get("it", []):
                    if isinstance(transform, dict) and transform.get("ty") == "tr":
                        scale *= _transform_scale(transform)
                if scale > MAX_TRANSFORM_SCALE:
                    raise ValueError("Expanded transform scale exceeds bounds")
            points += _path(item)
            if (
                isinstance(item.get("x"), str)
                or item.get("expression")
                or item.get("expressions")
            ):
                raise ValueError("Animation expressions are unsupported")
            if item.get("ddd") or item.get("ef") or item.get("segments"):
                raise ValueError("Unsafe animation feature")
            if item.get("tt") or item.get("td"):
                features.add("track_matte")
                if item.get("tt") == 4:
                    features.add("inverted_luminance_matte")
            if "mode" in item and "pt" in item:
                masks += 1
                if item["mode"] not in ("a", "n", "s", "i", "d", "f", "l"):
                    raise ValueError("Invalid animation mask")
                if item["mode"] not in ("a", "n"):
                    features.add("non_additive_mask")
                if item["mode"] in ("d", "f", "l"):
                    features.add("unsupported_mask_mode")
                if "x" in item:
                    expansion = _scalar_values(item["x"])
                    if any(abs(value) > 64 for value in expansion):
                        raise ValueError("Mask expansion exceeds bounds")
                    if any(expansion):
                        features.add("expanded_mask")
            if isinstance(item.get("ty"), str):
                if item["ty"] not in _SHAPES:
                    raise ValueError("Unsupported animation shape")
                shapes += 1
                if item["ty"] in ("st", "gs") and any(
                    not 0 <= number <= 128 for number in _scalar_values(item["w"])
                ):
                    raise ValueError("Stroke width exceeds bounds")
                if item["ty"] == "tr":
                    _transform_scale(item)
                if item["ty"] == "sr":
                    vertices = _scalar_values(item["pt"])
                    if not all(0 < value <= 256 for value in vertices):
                        raise ValueError("Star point count exceeds bounds")
                    points += 2 * math.ceil(max(vertices))
                if item["ty"] in ("gf", "gs"):
                    gradient = item.get("g")
                    if (
                        not isinstance(gradient, dict)
                        or type(gradient.get("p")) is not int
                        or not 1 <= gradient["p"] <= 128
                    ):
                        raise ValueError("Gradient exceeds bounds")
                    gradients += _gradient_stops(gradient)
            if "t" in item and any(key in item for key in ("s", "e", "h")):
                if "h" in item and (
                    type(item["h"]) is not int or item["h"] not in (0, 1)
                ):
                    raise ValueError("Invalid hold flag")
                _number(item["t"])
                keyframes += 1
            for key, child in item.items():
                if len(key) > MAX_JSON_STRING:
                    raise ValueError("Animation key exceeds bounds")
                pending.append((child, depth + 1, scale))
        elif isinstance(item, list):
            pending.extend((child, depth + 1, scale) for child in item)
        elif isinstance(item, str):
            if len(item) > MAX_JSON_STRING:
                raise ValueError("Animation string exceeds bounds")
        elif type(item) in (int, float):
            if abs(_number(item)) > MAX_GEOMETRY_NUMBER and depth:
                raise ValueError("Animation geometry number exceeds bounds")
        elif item is not None and type(item) is not bool:
            raise ValueError("Invalid animation value")
        if (
            shapes > MAX_SHAPES
            or points > MAX_POINTS
            or keyframes > MAX_KEYFRAMES
            or masks > MAX_MASKS
            or gradients > MAX_GRADIENT_STOPS
        ):
            raise ValueError("Animation geometry exceeds bounds")
    return shapes, points, nodes, masks, gradients


def _layers(layers):
    if not isinstance(layers, list) or len(layers) > MAX_LAYERS:
        raise ValueError("Animation layers exceed bounds")
    indices, references = {}, []
    for layer in layers:
        if (
            not isinstance(layer, dict)
            or type(layer.get("ty")) is not int
            or layer["ty"] not in (0, 3, 4)
        ):
            raise ValueError("Unsupported animation layer")
        index = layer.get("ind")
        if type(index) is not int or index < 0 or index in indices:
            raise ValueError("Invalid animation layer identity")
        indices[index] = layer
        _transform_scale(layer.get("ks", {}))
        if "sr" in layer and not 0 < _number(layer["sr"]) <= 1000:
            raise ValueError("Invalid layer timing")
        if layer["ty"] == 0:
            if not isinstance(layer.get("refId"), str):
                raise ValueError("Invalid precomposition")
            references.append((layer["refId"], index))
        elif "refId" in layer:
            raise ValueError("Invalid layer asset reference")
        if layer["ty"] == 4 and not isinstance(layer.get("shapes"), list):
            raise ValueError("Invalid shape layer")
    mattes = {}
    scales = {}
    for position, layer in enumerate(layers):
        original = layer
        matte_type = layer.get("tt", 0)
        if type(matte_type) is not int or matte_type not in (0, 1, 2, 3, 4):
            raise ValueError("Invalid matte type")
        if matte_type:
            if "tp" in layer:
                dependency = layer["tp"]
            elif position:
                dependency = layers[position - 1]["ind"]
            else:
                raise ValueError("Missing matte dependency")
            if type(dependency) is not int or dependency not in indices:
                raise ValueError("Invalid matte dependency")
            mattes[layer["ind"]] = dependency
        seen, scale = set(), _transform_scale(layer.get("ks", {}))
        index = layer["ind"]
        while "parent" in layer:
            parent = layer["parent"]
            if (
                type(parent) is not int
                or parent in seen
                or parent not in indices
                or len(seen) >= 16
            ):
                raise ValueError("Invalid recursive parent graph")
            seen.add(parent)
            layer = indices[parent]
            scale *= _transform_scale(layer.get("ks", {}))
            if scale > MAX_TRANSFORM_SCALE:
                raise ValueError("Expanded parent transform exceeds bounds")
        scales[index] = scale * (
            _shape_scale(original["shapes"]) if original["ty"] == 4 else 1
        )
        if scales[index] > MAX_TRANSFORM_SCALE:
            raise ValueError("Expanded layer geometry exceeds bounds")
    for index in mattes:
        seen = set()
        while index in mattes:
            if index in seen or len(seen) >= 16:
                raise ValueError("Invalid recursive matte graph")
            seen.add(index)
            index = mattes[index]
    return [(identity, scales[index]) for identity, index in references], max(
        scales.values(), default=1
    )


def _animation(path: Path) -> dict:
    compressed = path.read_bytes()
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    raw = inflater.decompress(compressed, ANIMATION_BYTES + 1)
    if (
        len(raw) > ANIMATION_BYTES
        or inflater.unconsumed_tail
        or not inflater.eof
        or inflater.unused_data
    ):
        raise ValueError("Animation decompression exceeds bounds")
    _json_depth(raw)
    data = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_reject_constant)
    if not isinstance(data, dict) or not isinstance(data.get("v"), str):
        raise ValueError("Invalid animation document")
    if data.get("w") != 512 or data.get("h") != 512 or _number(data.get("fr")) != 60:
        raise ValueError("Invalid animation canvas or frame rate")
    span = _number(data.get("op")) - _number(data.get("ip"))
    if not 0 < span <= 180:
        raise ValueError("Invalid animation duration")
    if data.get("chars") or data.get("fonts", {}).get("list"):
        raise ValueError("Font assets are unsupported")
    features = set()
    _walk(data, features=features)
    assets = data.get("assets", [])
    if not isinstance(assets, list) or len(assets) > MAX_ASSETS:
        raise ValueError("Animation assets exceed bounds")
    compositions = {"": data.get("layers")}
    for asset in assets:
        if (
            not isinstance(asset, dict)
            or not isinstance(asset.get("id"), str)
            or not asset["id"]
            or asset["id"] in compositions
        ):
            raise ValueError("Invalid animation asset")
        if any(key in asset for key in ("p", "u", "e")) or "layers" not in asset:
            raise ValueError("External or raster animation assets are unsupported")
        compositions[asset["id"]] = asset["layers"]
    references, counts, scales = {}, {}, {}
    for identity, layers in compositions.items():
        references[identity], scales[identity] = _layers(layers)
        counts[identity] = (len(layers), *_walk(layers, features=features))

    expanded_limits = (
        MAX_EXPANDED_LAYERS,
        MAX_EXPANDED_SHAPES,
        MAX_EXPANDED_POINTS,
        MAX_EXPANDED_NODES,
        MAX_EXPANDED_MASKS,
        MAX_EXPANDED_GRADIENT_STOPS,
    )
    expanded_cache = {}

    def expand(identity, ancestry):
        if identity not in compositions or identity in ancestry or len(ancestry) > 16:
            raise ValueError("Invalid recursive precomposition")
        if identity in expanded_cache:
            total, depth, scale = expanded_cache[identity]
            if len(ancestry) + depth > 17:
                raise ValueError("Precomposition depth exceeds bounds")
            return total, depth, scale
        total = list(counts[identity])
        depth, scale = 1, scales[identity]
        for child, child_scale in references[identity]:
            expanded, child_depth, expanded_scale = expand(child, ancestry | {identity})
            depth = max(depth, child_depth + 1)
            scale = max(scale, child_scale * expanded_scale)
            if scale > MAX_TRANSFORM_SCALE:
                raise ValueError("Expanded precomposition scale exceeds bounds")
            total = [left + right for left, right in zip(total, expanded)]
            if any(value > limit for value, limit in zip(total, expanded_limits)):
                raise ValueError("Expanded animation exceeds bounds")
        if any(value > limit for value, limit in zip(total, expanded_limits)):
            raise ValueError("Expanded animation exceeds bounds")
        expanded_cache[identity] = total, depth, scale
        return total, depth, scale

    for identity in compositions:
        expand(identity, set())
    canonical = json.dumps(
        data, ensure_ascii=False, allow_nan=False, separators=(",", ":")
    )
    if len(canonical.encode()) > ANIMATION_BYTES:
        raise ValueError("Animation output exceeds bounds")
    return {
        "format": "tgs",
        "mime": "application/x-tgsticker",
        "sticker": "animated",
        "width": 512,
        "height": 512,
        "duration": span / 60,
        "fps": 60,
        "frames": math.ceil(span),
        "animation_json": canonical,
        "features": tuple(sorted(features)),
    }


def _webm_track(path: Path, size: int):
    """One complete Tracks element/VP9 track, with no hidden audio entry."""
    with path.open("rb") as source:
        if not _webm(source, size):
            raise ValueError("Not a complete WebM")
        source.seek(0)
        _, length = _element(source, size)
        source.seek(length, 1)
        _, length = _element(source, size)
        end = size if length is None else source.tell() + length
        tracks = entries = elements = 0
        while source.tell() < end:
            elements += 1
            if elements > 8192:
                raise ValueError("Sticker container complexity exceeds bounds")
            kind, length = _element(source, end)
            if length is None:
                raise ValueError("Unbounded sticker cluster")
            stop = source.tell() + length
            if kind == 0x1654AE6B:
                tracks += 1
                while source.tell() < stop:
                    elements += 1
                    if elements > 8192:
                        raise ValueError("Sticker container complexity exceeds bounds")
                    entry, length = _element(source, stop)
                    entry_end = source.tell() + length if length is not None else None
                    if entry_end is None:
                        raise ValueError("Unbounded sticker track")
                    if entry == 0xAE:
                        entries += 1
                        track_type = codec = None
                        while source.tell() < entry_end:
                            elements += 1
                            if elements > 8192:
                                raise ValueError(
                                    "Sticker track complexity exceeds bounds"
                                )
                            field, length = _element(source, entry_end)
                            if length is None:
                                raise ValueError("Unbounded sticker track field")
                            if field == 0x83:
                                if track_type is not None or not 0 < length <= 8:
                                    raise ValueError("Invalid sticker track type")
                                track_type = int.from_bytes(source.read(length), "big")
                            elif field == 0x86:
                                if codec is not None or not 0 < length <= 128:
                                    raise ValueError("Invalid sticker codec")
                                codec = source.read(length)
                            else:
                                source.seek(length, 1)
                        if track_type != 1 or codec != b"V_VP9":
                            raise ValueError("Unsupported or audible sticker track")
                    source.seek(entry_end)
            source.seek(stop)
        if tracks != 1 or entries != 1:
            raise ValueError("Sticker requires one video track")


def _mp4_track(path: Path, size: int):
    """A single movie/track table prevents a hidden second audio definition."""
    with path.open("rb") as source:
        if not _mp4(source, size):
            raise ValueError("Not a complete MP4")
        source.seek(0)
        movies = tracks = elements = 0

        def box(end):
            nonlocal elements
            elements += 1
            if elements > 8192:
                raise ValueError("GIF container complexity exceeds bounds")
            start = source.tell()
            header = source.read(8)
            if len(header) != 8:
                raise ValueError("Incomplete MP4 box")
            length, kind, minimum = int.from_bytes(header[:4], "big"), header[4:], 8
            if length == 1:
                length, minimum = int.from_bytes(source.read(8), "big"), 16
            elif length == 0:
                length = end - start
            if length < minimum or start + length > end:
                raise ValueError("Invalid MP4 box")
            return kind, start + length

        while source.tell() < size:
            kind, end = box(size)
            if kind == b"moov":
                movies += 1
                while source.tell() < end:
                    child, stop = box(end)
                    if child == b"trak":
                        tracks += 1
                    source.seek(stop)
            source.seek(end)
        if movies != 1 or tracks != 1:
            raise ValueError("GIF requires one silent video track table")


async def _probe(path: Path, demuxer: str, *, complete=False, frames=False):
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
    if demuxer == "mov":
        argv += [
            "-enable_drefs",
            "0",
            "-use_absolute_path",
            "0",
            "-ignore_chapters",
            "1",
        ]
    entries = _VIDEO_ENTRIES
    if complete:
        argv += ["-count_frames"]
    if frames:
        argv += ["-show_frames"]
        entries += ":frame=media_type,stream_index,width,height,best_effort_timestamp_time,duration_time,pkt_duration_time"
    argv += ["-show_entries", entries, "-of", "json", "-i", str(path)]
    try:
        stdout, stderr = await bounded_process(
            argv,
            stdin=b"",
            cwd=path.parent,
            env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "LANG": "C"},
            timeout=20 if complete else 10,
            stdout_limit=65536,
            stderr_limit=8192,
        )
    except (OSError, Unavailable, TimeoutError):
        raise Unavailable("Media verification unavailable") from None
    if stderr:
        raise ValueError("Invalid decoded video content")
    try:
        result = json.loads(stdout)
        if not isinstance(result, dict):
            raise ValueError("Invalid verifier result")
        return result
    except (ValueError, UnicodeError):
        raise Unavailable("Media verification unavailable") from None


def _track(data: dict, codec: str, *, sticker=False):
    tracks = data.get("streams")
    if not isinstance(tracks, list) or len(tracks) != 1:
        raise ValueError("Media requires one silent video stream")
    track = tracks[0]
    if (
        track.get("codec_type") != "video"
        or track.get("codec_name") != codec
        or track.get("disposition", {}).get("attached_pic")
    ):
        raise ValueError("Unsupported media stream")
    _dimensions(track["width"], track["height"], sticker=sticker)
    duration = float(data["format"]["duration"])
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("Invalid media duration")
    rate = track.get("avg_frame_rate")
    if rate in (None, "0/0"):
        rate = track["r_frame_rate"]
    fps = float(Fraction(rate))
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("Invalid media frame rate")
    if sticker and (duration > 3.000001 or fps > 30.000001):
        raise ValueError("Sticker timing exceeds native bounds")
    return track, duration, fps


async def _video(path: Path, kind: str, size: int) -> dict:
    sticker = kind == "sticker"
    if sticker:
        await asyncio.to_thread(_webm_track, path, size)
    else:
        await asyncio.to_thread(_mp4_track, path, size)
    demuxer, codec = ("matroska", "vp9") if sticker else ("mov", "h264")
    initial = await _probe(path, demuxer)
    _track(initial, codec, sticker=sticker)
    complete = await _probe(path, demuxer, complete=True, frames=sticker)
    track, duration, fps = _track(complete, codec, sticker=sticker)
    frames = int(track["nb_read_frames"])
    if (
        not 0 < frames <= (90 if sticker else MAX_GIF_FRAMES)
        or frames * track["width"] * track["height"] > MAX_DECODED_PIXELS
    ):
        raise ValueError("Media decode exceeds frame/pixel bounds")
    if sticker:
        observed = complete["frames"]
        if len(observed) != frames:
            raise ValueError("Incomplete sticker frame proof")
        for frame in observed:
            if (
                frame["media_type"] != "video"
                or frame["stream_index"] != track["index"]
            ):
                raise ValueError("Audible or invalid sticker frame")
            _dimensions(frame["width"], frame["height"], sticker=True)
            start = float(frame["best_effort_timestamp_time"])
            length = float(
                frame.get("duration_time", frame.get("pkt_duration_time", 1 / fps))
            )
            if not (
                math.isfinite(start)
                and math.isfinite(length)
                and start >= 0
                and 0 <= length <= 3
                and start + length <= 3.000001
            ):
                raise ValueError("Sticker frame exceeds native duration")
    return {
        "format": "webm" if sticker else "mp4",
        "mime": "video/webm" if sticker else "video/mp4",
        "sticker": "video" if sticker else None,
        "width": track["width"],
        "height": track["height"],
        "duration": duration,
        "fps": fps,
        "frames": frames,
    }


async def validate_selection(
    path: Path, kind: Literal["gif", "sticker"], *, expected_sha256: str | None = None
) -> SelectionFormat:
    """Prove exact retained bytes; definitive refusals and outages stay distinct.

    Callers retain the original descriptor/bytes on every failure. An unavailable
    verifier can be retried deliberately without re-fetching or substituting the
    item. An expected digest binds later dispatch/render reads to selection proof.
    """
    if kind not in ("gif", "sticker"):
        raise Refused(_INVALID)
    path, limit = Path(path).absolute(), GIF_BYTES if kind == "gif" else STICKER_BYTES
    try:
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode) or not before.st_size:
            raise Refused("Selected media unavailable")
        if before.st_size > limit:
            raise MediaTooLarge("Selected media exceeds the file limit")
        digest, size = await asyncio.to_thread(_digest, path, limit)
        if size != before.st_size or (
            expected_sha256 is not None
            and not hmac.compare_digest(digest, expected_sha256)
        ):
            raise Refused("Selected media integrity changed")
        with path.open("rb") as source:
            header = source.read(16)
        if kind == "gif" and header[:6] in (b"GIF87a", b"GIF89a"):
            facts = await asyncio.to_thread(_still_or_gif, path, kind)
        elif kind == "sticker" and header[:2] == b"\x1f\x8b":
            facts = await asyncio.to_thread(_animation, path)
        elif kind == "sticker" and header[:4] == b"RIFF":
            facts = await asyncio.to_thread(_still_or_gif, path, kind)
        else:
            facts = await _video(path, kind, size)
        after = path.lstat()
        if any(
            getattr(before, field) != getattr(after, field)
            for field in (
                "st_dev",
                "st_ino",
                "st_mode",
                "st_size",
                "st_mtime_ns",
                "st_ctime_ns",
            )
        ):
            raise Refused("Selected media integrity changed")
        final_digest, final_size = await asyncio.to_thread(_digest, path, limit)
        if final_size != size or not hmac.compare_digest(final_digest, digest):
            raise Refused("Selected media integrity changed")
        return SelectionFormat(kind=kind, sha256=digest, size=size, **facts)
    except Unavailable:
        raise
    except Refused:
        raise
    except OSError:
        raise Refused("Selected media unavailable") from None
    except (
        ValueError,
        TypeError,
        KeyError,
        IndexError,
        AttributeError,
        ZeroDivisionError,
        OverflowError,
        zlib.error,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ):
        raise Refused(_INVALID) from None
