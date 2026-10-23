"""Disposable authored GS6 media, without transport, API or browser wiring.

Call ``build_assets`` with an isolated fixture/temporary directory. The shapes
contain no correspondence, imported artwork, fonts, external animation assets or
audio. Fully validated source bytes remain unchanged; no binary is committed.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import math
import tempfile
import time
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from enloq.config import private_directory
from enloq.domain.selection_formats import (
    GIF_BYTES,
    STICKER_BYTES,
    SelectionFormat,
    validate_selection,
)
from enloq.inference.runner import bounded_process
from enloq.media_tools import FFMPEG
from fastapi import Request
from PIL import Image, ImageDraw

WEB_GIF_URL = "https://media.example.test/authored-loop.mp4"
SIZE, VIDEO_FRAMES, VIDEO_FPS = 512, 60, 30
ANIMATION_INSTANCES = ("authored-left-instance", "authored-right-instance")
MediaKind = Literal["gif", "sticker"]


@dataclass(frozen=True)
class AuthoredAsset:
    path: Path
    facts: SelectionFormat


@dataclass(frozen=True)
class FailureSample:
    path: Path
    kind: MediaKind
    mime: str
    declared_size: int
    failure: Literal["unsupported", "corrupt", "too_large"]
    # Cap samples deliberately contain small valid bytes. They exercise later
    # declared-size admission, not proof of the streaming byte-count limit.
    validate_bytes: bool = True


@dataclass(frozen=True)
class AuthoredAssets:
    directory: Path
    media: dict[str, AuthoredAsset]
    failures: dict[str, FailureSample]
    web_gif_url: str = WEB_GIF_URL


def scene(frame: int = 0) -> Image.Image:
    """Transparent ring, moving gold rectangle and fixed purple dot."""
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((68, 68, 444, 444), outline=(53, 160, 174, 255), width=14)
    center = round(256 + 96 * math.sin(2 * math.pi * frame / VIDEO_FRAMES))
    draw.rounded_rectangle(
        (center - 48, 220, center + 48, 292),
        radius=12,
        fill=(242, 202, 69, 255),
    )
    draw.ellipse((322, 138, 354, 170), fill=(160, 101, 198, 255))
    return image


def _property(value):
    return {"a": 0, "k": value}


def _transform(position=(0, 0, 0), *, anchor=(0, 0, 0), rotation=0):
    return {
        "o": _property(100),
        "r": _property(rotation),
        "p": _property(list(position)),
        "a": _property(list(anchor)),
        "s": _property([100, 100, 100]),
    }


def animation_document() -> dict:
    """Normal additive mask/gradient and two instances of one moving precomp.

    The shared asset intentionally reuses its internal geometry. Rendering this
    source in multiple UI surfaces must namespace generated mask/gradient IDs.
    """
    moving = _transform()
    values = ([96, 128, 0], [160, 128, 0], [96, 128, 0])
    moving["p"] = {
        "a": 1,
        "k": [
            {
                "t": index * 60,
                "s": list(value),
                **(
                    {
                        "e": list(values[index + 1]),
                        "o": {"x": 1 / 3, "y": 1 / 3},
                        "i": {"x": 2 / 3, "y": 2 / 3},
                    }
                    if index < 2
                    else {}
                ),
            }
            for index, value in enumerate(values)
        ],
    }
    gradient = {
        "ty": "gf",
        "nm": "Authored gold-to-teal gradient",
        "o": _property(100),
        "t": 1,
        "s": _property([-56, -56]),
        "e": _property([56, 56]),
        "g": {"p": 2, "k": _property([0, 0.95, 0.79, 0.27, 1, 0.21, 0.63, 0.68])},
        "r": 1,
    }
    mask = {
        "nm": "Authored additive square mask",
        "mode": "a",
        "inv": False,
        "o": _property(100),
        "x": _property(0),
        "pt": _property(
            {
                "i": [[0, 0] for _ in range(4)],
                "o": [[0, 0] for _ in range(4)],
                "v": [[-52, -52], [52, -52], [52, 52], [-52, 52]],
                "c": True,
            }
        ),
    }
    layers = [
        {
            "ty": 4,
            "ind": 11,
            "nm": "Authored masked moving rectangle",
            "ip": 0,
            "op": 120,
            "st": 0,
            "ks": moving,
            "masksProperties": [mask],
            "shapes": [
                {
                    "ty": "gr",
                    "it": [
                        {
                            "ty": "rc",
                            "p": _property([0, 0]),
                            "s": _property([112, 112]),
                            "r": _property(10),
                        },
                        gradient,
                        {"ty": "tr", **_transform()},
                    ],
                }
            ],
        },
        {
            "ty": 4,
            "ind": 22,
            "nm": "Authored fixed teal ring",
            "ip": 0,
            "op": 120,
            "st": 0,
            "ks": _transform((128, 128, 0)),
            "shapes": [
                {
                    "ty": "gr",
                    "it": [
                        {
                            "ty": "el",
                            "p": _property([0, 0]),
                            "s": _property([176, 176]),
                        },
                        {
                            "ty": "st",
                            "c": _property([0.21, 0.63, 0.68, 1]),
                            "o": _property(100),
                            "w": _property(6),
                            "lc": 2,
                            "lj": 2,
                        },
                        {"ty": "tr", **_transform()},
                    ],
                }
            ],
        },
    ]
    return {
        "v": "5.7.4",
        "tgs": 1,
        "fr": 60,
        "ip": 0,
        "op": 120,
        "w": SIZE,
        "h": SIZE,
        "nm": "Authored two-instance gradient scene",
        "ddd": 0,
        "fonts": {"list": []},
        "chars": [],
        "ef": [],
        "expressions": [],
        "assets": [{"id": "authored-vector", "w": 256, "h": 256, "layers": layers}],
        "layers": [
            {
                "ty": 0,
                "ind": index + 1,
                "nm": name,
                "refId": "authored-vector",
                "w": 256,
                "h": 256,
                "ip": 0,
                "op": 120,
                "st": 0,
                "sr": 1,
                "ks": _transform(
                    (136 + index * 240, 256, 0),
                    anchor=(128, 128, 0),
                    rotation=-10 if index == 0 else 10,
                ),
            }
            for index, name in enumerate(ANIMATION_INSTANCES)
        ],
        "markers": [],
    }


async def _encode(frames: Path, output: Path, *, sticker: bool):
    codec = (
        [
            "-c:v",
            "libvpx-vp9",
            "-pix_fmt",
            "yuva420p",
            "-b:v",
            "0",
            "-crf",
            "34",
            "-deadline",
            "realtime",
            "-cpu-used",
            "8",
            "-auto-alt-ref",
            "0",
        ]
        if sticker
        else [
            "-c:v",
            "libx264",
            "-profile:v",
            "baseline",
            "-pix_fmt",
            "yuv420p",
            "-preset",
            "ultrafast",
            "-movflags",
            "+faststart",
        ]
    )
    await bounded_process(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-framerate",
            str(VIDEO_FPS),
            "-i",
            str(frames / "frame-%03d.png"),
            "-frames:v",
            str(VIDEO_FRAMES),
            "-map",
            "0:v:0",
            "-an",
            *codec,
            "-threads",
            "1",
            "-y",
            str(output),
        ],
        stdin=b"",
        cwd=output.parent,
        env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "LANG": "C"},
        timeout=30,
        stdout_limit=1024,
        stderr_limit=4096,
    )


async def build_assets(directory: Path) -> AuthoredAssets:
    """Generate and prove exact authored source bytes in disposable paths.

    This builder performs local image/codec validation only. It does not create
    catalogs, select/send media, access a URL or change application/service state.
    """
    directory = private_directory(Path(directory).absolute())
    paths = {
        "static": (directory / "authored-pattern.webp", "sticker"),
        "animated": (directory / "authored-masked-gradient.tgs", "sticker"),
        "video": (directory / "authored-moving-sticker.webm", "sticker"),
        "gif_mp4": (directory / "authored-loop.mp4", "gif"),
        "gif": (directory / "authored-loop.gif", "gif"),
    }
    scene().save(paths["static"][0], format="WEBP", lossless=True)
    animation = json.dumps(animation_document(), separators=(",", ":")).encode()
    paths["animated"][0].write_bytes(gzip.compress(animation, mtime=0))
    images = [scene(frame) for frame in (0, 20, 40)]
    images[0].save(
        paths["gif"][0],
        format="GIF",
        save_all=True,
        append_images=images[1:],
        duration=400,
        loop=0,
        disposal=2,
        optimize=False,
    )
    with tempfile.TemporaryDirectory(prefix="frames-", dir=directory) as frames:
        frame_directory = Path(frames)
        for frame in range(VIDEO_FRAMES):
            scene(frame).save(frame_directory / f"frame-{frame:03}.png", format="PNG")
        await _encode(frame_directory, paths["video"][0], sticker=True)
        await _encode(frame_directory, paths["gif_mp4"][0], sticker=False)
    media = {}
    for name, (path, kind) in paths.items():
        path.chmod(0o600)
        media[name] = AuthoredAsset(path, await validate_selection(path, kind))
    unsupported = directory / "authored-unsupported.png"
    scene().save(unsupported, format="PNG")
    corrupt_gif = directory / "authored-corrupt.mp4"
    corrupt_gif.write_bytes(b"Authored corrupt GIF container.")
    corrupt_sticker = directory / "authored-corrupt.tgs"
    corrupt_sticker.write_bytes(gzip.compress(animation, mtime=0)[:-8])
    failures = {
        "unsupported": FailureSample(
            unsupported,
            "sticker",
            "image/png",
            unsupported.stat().st_size,
            "unsupported",
        ),
        "corrupt_gif": FailureSample(
            corrupt_gif, "gif", "video/mp4", corrupt_gif.stat().st_size, "corrupt"
        ),
        "corrupt_sticker": FailureSample(
            corrupt_sticker,
            "sticker",
            "application/x-tgsticker",
            corrupt_sticker.stat().st_size,
            "corrupt",
        ),
        "oversize_gif": FailureSample(
            paths["gif_mp4"][0], "gif", "video/mp4", GIF_BYTES + 1, "too_large", False
        ),
        "oversize_sticker": FailureSample(
            paths["video"][0],
            "sticker",
            "video/webm",
            STICKER_BYTES + 1,
            "too_large",
            False,
        ),
    }
    for sample in failures.values():
        sample.path.chmod(0o600)
    return AuthoredAssets(directory, media, failures)


PEER, OTHER_PEER = "user:171", "user:172"
TITLE, OTHER_TITLE = "GIF and sticker fixture", "Another media chat"


def register(app):
    """Authored GS6 controls, confined to this disposable fake-only server.

    Gates delay actual application work rather than synthesizing ready events.
    Safe public statistics expose facts, never private paths/native references.
    Assets are built once per server lifetime and shared by both fake accounts.
    """
    from enloq.application.catalog_assets import CatalogAssets
    from enloq.application.context import incoming
    from enloq.application.delivery import operation_view
    from enloq.domain.catalog import (
        CatalogLibrary,
        CatalogMetadata,
        CatalogOrigin,
        CatalogPackData,
        CatalogSearchData,
        NativeCatalogSource,
        NativePackReference,
        NativeThumbnail,
        WebCatalogSource,
        source_revision,
    )
    from enloq.domain.delivery import StickerReselectionRequired
    from enloq.domain.models import Chat, MediaInfo, Message, Unavailable
    from enloq.domain.suggestions import SuggestionOption
    from enloq.storage.store import blank_draft

    started, states, built = time.time(), {}, {}
    generation = asyncio.Lock()
    active_rewrites = ContextVar("authored_gif_sticker_rewrites", default=frozenset())

    async def assets():
        async with generation:
            if not built:
                value = await build_assets(
                    app.state.accounts.registry.root / "authored-gif-sticker-assets"
                )
                built["assets"] = value
                fallback = animation_document()
                fallback["assets"][0]["layers"][0]["masksProperties"][0]["mode"] = "d"
                path = value.directory / "authored-render-fallback.tgs"
                path.write_bytes(gzip.compress(json.dumps(fallback).encode(), mtime=0))
                path.chmod(0o600)
                facts = await validate_selection(path, "sticker")
                built["render_fallback"] = AuthoredAsset(path, facts)
            return built["assets"]

    app.state.authored_gif_sticker_assets = assets

    async def wait(state, name):
        if gate := state["gates"].get(name):
            await gate.wait()
        if state["failures"].get(name, 0):
            state["failures"][name] -= 1
            raise Unavailable("Authored temporary media fixture failure")

    def bind_generation(account, assistant, state):
        """Follow fixture engine replacements without stacking rewrite records."""
        engine = assistant.engine

        def hook(previous):
            async def generate(role, packet, *args, **kwargs):
                fixture_rewriting = (
                    engine is assistant.engine
                    and role == "outgoing_rewrite"
                    and state["sources"]
                    and any(
                        assistant.store.draft(f"{account}/{peer}").get("state")
                        == "rewriting"
                        for peer in (PEER, OTHER_PEER)
                    )
                )
                if not fixture_rewriting or account in active_rewrites.get():
                    return await previous(role, packet, *args, **kwargs)
                token = active_rewrites.set(active_rewrites.get() | {account})
                try:
                    state["rewrites"].append(
                        dict(
                            text=packet["draft"]["text"],
                            images=len(packet.get("images", [])),
                        )
                    )
                    await wait(state, "rewrite")
                    # Preserve the current engine's authored generation/stream
                    # behavior; the fixture proves only packet and lock mechanics.
                    return await previous(role, packet, *args, **kwargs)
                finally:
                    active_rewrites.reset(token)

            generate.authored_gif_sticker_state = state
            return generate

        for name in ("generate", "generate_stream"):
            previous = getattr(engine, name, None)
            if previous is not None and (
                getattr(previous, "authored_gif_sticker_state", None) is not state
            ):
                setattr(engine, name, hook(previous))

    def state_for(account):
        if account in states:
            bind_generation(account, app.state.accounts.get(account), states[account])
            return states[account]
        assistant = app.state.accounts.get(account)
        transport = assistant.transport
        state = states[account] = dict(
            gates={},
            failures={},
            downloads=[],
            received=[],
            sends=[],
            rewrites=[],
            web=[],
            animations=[],
            library=[],
            searches=[],
            faults={},
            sources={},
            web_source=None,
        )
        previous_download, previous_library = (
            transport.catalog_download,
            transport.catalog_library,
        )
        previous_search, previous_stream = transport.catalog_search, transport.stream
        previous_send = transport.send

        async def download(source, path, **fields):
            if source.document_id not in state["sources"]:
                return await previous_download(source, path, **fields)
            record = dict(
                document=source.document_id,
                thumb=bool(fields.get("thumb")),
                owner="selected"
                if path.parent == assistant.store.selected_media.root
                else "preview",
                completed=False,
            )
            state["downloads"].append(record)
            await wait(state, f"download:{record['owner']}:{source.document_id}")
            await wait(state, "download:" + source.document_id)
            result = await previous_download(source, path, **fields)
            record["completed"] = True
            return result

        async def library():
            state["library"].append({"completed": False})
            record = state["library"][-1]
            await wait(state, "library")
            value = await previous_library()
            record["completed"] = True
            return value

        async def search(query, offset=""):
            record = dict(query=query, offset=offset, completed=False)
            state["searches"].append(record)
            await wait(state, "search:" + query)
            result = await previous_search(query, offset)
            record["completed"] = True
            return result

        async def stream(peer, identity, **fields):
            if peer not in (PEER, OTHER_PEER):
                async for chunk in previous_stream(peer, identity, **fields):
                    yield chunk
                return
            record = dict(
                id=identity,
                revision=fields.get("expected_revision"),
                thumb=bool(fields.get("thumb")),
                completed=False,
            )
            state["received"].append(record)
            await wait(
                state,
                f"received:{'poster' if fields.get('thumb') else 'file'}:{identity}",
            )
            await wait(state, "received:" + str(identity))
            async for chunk in previous_stream(peer, identity, **fields):
                yield chunk
            record["completed"] = True

        async def send(description, evidence):
            if description.peer not in (PEER, OTHER_PEER):
                return await previous_send(description, evidence)
            selected = description.media
            record = dict(
                operation=description.attempt_id,
                text=description.text,
                reply_to=description.reply_to,
                kind=selected.kind if selected else None,
                format=selected.format if selected else None,
                size=selected.size if selected else None,
                sha256=selected.sha256 if selected else None,
                completed=False,
            )
            state["sends"].append(record)
            await wait(state, "send")
            if (
                state["faults"].get("reference")
                and selected
                and selected.kind == "sticker"
            ):
                state["faults"]["reference"] = False
                raise StickerReselectionRequired(
                    "Authored exact sticker reference unavailable; reselect it"
                )
            result = await previous_send(description, evidence)
            record["completed"] = True
            return result

        transport.catalog_download, transport.catalog_library = download, library
        transport.catalog_search, transport.stream, transport.send = (
            search,
            stream,
            send,
        )
        bind_generation(account, assistant, state)

        async def web_fetch(url, path, *, limit, **_fields):
            if url != WEB_GIF_URL:
                raise Unavailable("Only the authored synthetic web GIF is available")
            state["web"].append({"completed": False})
            record = state["web"][-1]
            await wait(state, "web")
            value = await assets()
            data = value.media["gif_mp4"].path.read_bytes()
            if len(data) > limit:
                raise Unavailable("Authored web fixture exceeds its limit")
            path.write_bytes(data)
            path.chmod(0o600)
            record["completed"] = True

        assistant.catalog_assets.web_fetcher = web_fetch
        state["web_fetch"] = web_fetch
        return state

    @app.middleware("http")
    async def animation_response_gate(request, call_next):
        """Separate header/body gates around real authenticated JSON bytes."""
        path = request.url.path
        state = next(
            (
                value
                for account, value in states.items()
                if path.startswith(f"/api/accounts/{account}/chats/{PEER}/")
                and path.endswith("/animation")
            ),
            None,
        )
        if state is None or not any(
            name in state["gates"] for name in ("animation-headers", "animation-body")
        ):
            return await call_next(request)
        stage = "headers" if "animation-headers" in state["gates"] else "body"
        record = {"stage": stage, "completed": False}
        state["animations"].append(record)
        if stage == "headers":
            await wait(state, "animation-headers")
            response = await call_next(request)
            record["completed"] = True
            return response
        response = await call_next(request)
        from starlette.responses import StreamingResponse

        async def body():
            await wait(state, "animation-body")
            async for chunk in response.body_iterator:
                yield chunk
            record["completed"] = True

        return StreamingResponse(
            body(), status_code=response.status_code, headers=dict(response.headers)
        )

    # SelectedMedia's explicit web download uses a module function. Route only
    # the synthetic example.test URL to the appropriate disposable workspace.
    from enloq.application import selected_media as selection_module

    previous_fetch = selection_module.fetch_web_media

    async def selected_web_fetch(url, path, *, limit):
        for account, state in states.items():
            if path.parent == app.state.accounts.get(account).store.selected_media.root:
                return await state["web_fetch"](url, path, limit=limit)
        return await previous_fetch(url, path, limit=limit)

    selection_module.fetch_web_media = selected_web_fetch

    def discard_catalog(assistant, state):
        sources = list(state["sources"].values())
        if state["web_source"]:
            sources.append(state["web_source"])
        for source in sources:
            for variant in ("thumbnail", "media"):
                assistant.media.cache.remove(
                    assistant.media.cache.catalog_entry(
                        CatalogAssets.identity(source, variant),
                        source_revision(source),
                        variant,
                    )
                )

    async def seed(account, case, premium=False, cached=True):
        assistant, state = app.state.accounts.get(account), state_for(account)
        value = await assets()
        for gate in state["gates"].values():
            gate.set()
        discard_catalog(assistant, state)
        state["web_source"] = None
        for field in ("gates", "failures", "faults", "sources"):
            state[field].clear()
        for field in (
            "downloads",
            "received",
            "sends",
            "rewrites",
            "web",
            "library",
            "searches",
            "animations",
        ):
            state[field].clear()
        assistant.transport.connected = True
        assistant.transport.held = 0
        assistant.transport.upload_failure = False
        assistant.transport.lose_receipt = False
        assistant.transport.catalog_failure = None
        assistant.transport.messages = [
            message
            for message in assistant.transport.messages
            if message.peer not in (PEER, OTHER_PEER)
        ]
        assistant.transport.deliveries = {
            identity: delivery
            for identity, delivery in assistant.transport.deliveries.items()
            if delivery["description"].peer not in (PEER, OTHER_PEER)
        }
        assistant.media.missing = {
            entry: value
            for entry, value in assistant.media.missing.items()
            if not any(
                entry[0].startswith(f"{account}/{peer}/") for peer in (PEER, OTHER_PEER)
            )
        }
        for peer in (PEER, OTHER_PEER):
            key = f"{account}/{peer}"
            for row in assistant.store.rows(
                "SELECT key FROM media_files WHERE key LIKE ?", (key + "/%",)
            ):
                assistant.media.cache.remove(row["key"])
            with assistant.store.transaction() as db:
                db.execute(
                    "DELETE FROM jobs WHERE scope IN (SELECT id FROM blocks WHERE chat_key=?)",
                    (key,),
                )
                for table in (
                    "blocks",
                    "messages",
                    "operations",
                    "drafts",
                    "selected_media",
                    "emotion_state",
                    "suggestion_state",
                ):
                    db.execute(f"DELETE FROM {table} WHERE chat_key=?", (key,))
                for table in ("derivatives", "message_versions"):
                    db.execute(
                        f"DELETE FROM {table} WHERE source LIKE ?", (key + "/%",)
                    )
            assistant.store.put_chat(
                Chat(
                    account_id=account,
                    peer=peer,
                    title=TITLE if peer == PEER else OTHER_TITLE,
                    kind="private",
                )
            )
            assistant.sync.ingest(
                Message(
                    account_id=account,
                    peer=peer,
                    message_id=90,
                    revision=1,
                    date=started - 100,
                    text="Authored reply target.",
                ),
                target=False,
                arrival=False,
            )
            assistant.store.selected_media.collect()
        assistant.catalog.clear()
        prefix = "Second " if account == 2 else ""
        specs = [
            (1, "gif_mp4", "gif", "mp4", "Loop MP4", False),
            (2, "gif", "gif", "gif", "Loop GIF", False),
            (3, "static", "sticker", "static", "Pattern static", False),
            (4, "animated", "sticker", "animated", "Pattern animated", False),
            (5, "video", "sticker", "video", "Pattern video", False),
            (6, "static", "sticker", "static", "Premium pattern", True),
            (7, "unsupported", "sticker", "unsupported", "Unsupported pattern", False),
            (8, "corrupt_sticker", "sticker", "animated", "Corrupt pattern", False),
            (9, "oversize_sticker", "sticker", "video", "Oversize sticker", False),
            (10, "oversize_gif", "gif", "mp4", "Oversize GIF", False),
            (
                11,
                "render_fallback",
                "sticker",
                "animated",
                "TGS render fallback",
                False,
            ),
        ]
        sources = {}
        thumb = value.media["static"].path.read_bytes()
        for index, asset_name, kind, format_, name, requires in specs:
            sample = (
                value.media.get(asset_name)
                or value.failures.get(asset_name)
                or built[asset_name]
            )
            data = sample.path.read_bytes()
            declared = (
                sample.declared_size if isinstance(sample, FailureSample) else len(data)
            )
            mime = (
                sample.mime if isinstance(sample, FailureSample) else sample.facts.mime
            )
            source = NativeCatalogSource(
                account_id=account,
                document_id=str(account * 10000 + index),
                access_hash="-17",
                file_reference="YQ==",
                dc_id=2,
                metadata=CatalogMetadata(
                    kind=kind,
                    format=format_,
                    mime=mime,
                    name=prefix + name,
                    size=declared,
                    width=SIZE,
                    height=SIZE,
                    duration=2
                    if format_ in ("gif", "mp4", "animated", "video")
                    else None,
                    emoji="◇" if kind == "sticker" else None,
                    keywords=("pattern", name.lower()),
                    requires_premium=requires,
                ),
                origins=(CatalogOrigin(kind="saved" if kind == "gif" else "recent"),),
                thumbnail=NativeThumbnail(
                    type="s", size=len(thumb), width=SIZE, height=SIZE
                ),
            )
            sources[name] = source
            state["sources"][source.document_id] = source
            assistant.transport.catalog_media[source.document_id, False] = data
            assistant.transport.catalog_media[source.document_id, True] = thumb
        pack = NativePackReference(
            id=str(account * 100 + 1),
            access_hash="-23",
            title=prefix + "Pattern pack",
            short_name="authored_pattern",
            count=3,
        )
        library = CatalogLibrary(
            premium=premium,
            gifs=tuple(
                sources[name] for name in ("Loop MP4", "Loop GIF", "Oversize GIF")
            ),
            recent=tuple(
                sources[name]
                for name in (
                    "Pattern static",
                    "Pattern video",
                    "Premium pattern",
                    "Unsupported pattern",
                    "Corrupt pattern",
                    "Oversize sticker",
                    "TGS render fallback",
                )
            ),
            favorites=(sources["Pattern animated"],),
            packs=(pack,),
        )
        assistant.transport.catalog_data = library
        assistant.transport.catalog_packs = {
            pack.id: CatalogPackData(
                pack=pack,
                items=tuple(
                    sources[name]
                    for name in ("Pattern static", "Pattern animated", "Pattern video")
                ),
            )
        }
        web = WebCatalogSource(
            account_id=account,
            remote_id="authored-web-loop",
            url=WEB_GIF_URL,
            metadata=CatalogMetadata(
                kind="gif",
                format="mp4",
                mime="video/mp4",
                name=prefix + "Web loop",
                size=value.media["gif_mp4"].facts.size,
                width=SIZE,
                height=SIZE,
                duration=2,
            ),
        )
        state["web_source"] = web
        assistant.transport.catalog_searches = {
            ("orbit", ""): CatalogSearchData(
                items=(web, sources["Loop GIF"]), next_offset="page2"
            ),
            ("orbit", "page2"): CatalogSearchData(items=(sources["Loop MP4"],)),
            ("older", ""): CatalogSearchData(items=(sources["Loop GIF"],)),
            ("newer", ""): CatalogSearchData(items=(web,)),
        }
        assistant.transport.catalog_calls.clear()
        assistant.transport.catalog_downloads.clear()
        if cached:
            assistant.catalog.storage.put(
                "library", library, assistant.catalog.library_revision(library)
            )
        if case in ("received", "fallbacks", "long"):
            names = {
                1: "static",
                2: "animated",
                3: "video",
                4: "gif_mp4",
                5: "gif",
                6: "animated",
                7: "render_fallback",
                8: "corrupt_sticker",
                9: "oversize_sticker",
            }
            identities = (7, 8, 9) if case == "fallbacks" else (1, 2, 3, 4, 5, 6)
            for identity in identities:
                name = names[identity]
                sample = (
                    value.media.get(name) or value.failures.get(name) or built[name]
                )
                data = sample.path.read_bytes()
                kind = "gif" if name in ("gif", "gif_mp4") else "sticker"
                info = MediaInfo(
                    mime=sample.mime
                    if isinstance(sample, FailureSample)
                    else sample.facts.mime,
                    file_name="authored." + sample.path.suffix.lstrip("."),
                    size=sample.declared_size
                    if isinstance(sample, FailureSample)
                    else len(data),
                    width=SIZE,
                    height=SIZE,
                    duration=2,
                    sticker=None
                    if kind == "gif"
                    else "static"
                    if name == "static"
                    else "video"
                    if name in ("video", "oversize_sticker")
                    else "animated",
                    emoji="◇" if kind == "sticker" else None,
                )
                assistant.transport.media[PEER, identity] = data
                assistant.sync.ingest(
                    Message(
                        account_id=account,
                        peer=PEER,
                        message_id=identity,
                        revision=1,
                        date=started - 200 + identity,
                        text="",
                        media=kind,
                        media_info=info,
                        attachment_id=f"authored-{name}-{identity}",
                        outgoing=identity in (3, 4, 6),
                    ),
                    target=False,
                    arrival=False,
                )
            if case == "long":
                for identity in range(100, 135):
                    assistant.sync.ingest(
                        Message(
                            account_id=account,
                            peer=PEER,
                            message_id=identity,
                            revision=1,
                            date=started - 90 + identity,
                            text=f"Authored scroll filler {identity}.",
                        ),
                        target=False,
                        arrival=False,
                    )
        settle(assistant, PEER)
        settle(assistant, OTHER_PEER)
        return {name: value.document_id for name, value in sources.items()}

    def settle(assistant, peer):
        key = f"{assistant.store.account_id}/{peer}"
        with assistant.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='superseded' WHERE scope IN (SELECT id FROM blocks WHERE chat_key=?) AND state='queued'",
                (key,),
            )
            db.execute(
                "UPDATE blocks SET state='ready',rendered='Authored media-group rewrite.',rendered_revision=revision,error=NULL,unanalysed='[]' WHERE chat_key=?",
                (key,),
            )

    async def suggestions(assistant):
        key = f"{assistant.store.account_id}/{PEER}"
        block = assistant.store.rows(
            "SELECT * FROM blocks WHERE chat_key=? ORDER BY date DESC,id DESC LIMIT 1",
            (key,),
        )[0]
        packet = await incoming(assistant, block)
        attempt = f"{time.time_ns():032x}"
        emotion = assistant.emotion.capture(block, packet, attempt)
        with assistant.store.transaction() as db:
            assistant.emotion.publish(db, key, emotion, "worried", 0, useful=True)
            record = assistant.suggestions.capture(
                db, block, packet, [], "worried", attempt
            )
            assistant.suggestions.store_record(db, key, record)
            assistant.suggestions.publish(
                db,
                key,
                attempt,
                [
                    SuggestionOption(
                        purpose="acknowledge", text="Thank you for telling me."
                    )
                ],
                0,
            )

    @app.post("/api/test/gif-sticker")
    async def control(request: Request):
        body = await request.json()
        action, account = body.get("action", "setup"), body.get("account", 1)
        assert account in (1, 2)
        assistant, state = app.state.accounts.get(account), state_for(account)
        key = f"{account}/{body.get('peer', PEER)}"
        if action == "setup":
            docs = await seed(
                account,
                body.get("case", "composer"),
                body.get("premium", False),
                body.get("cached", True),
            )
            return {"documents": docs}
        if action == "stats":
            return {
                **{
                    name: state[name]
                    for name in (
                        "downloads",
                        "received",
                        "sends",
                        "rewrites",
                        "web",
                        "library",
                        "searches",
                        "animations",
                    )
                },
                "catalog_calls": assistant.transport.catalog_calls,
                "draft": assistant.store.draft(key),
                "operations": [
                    operation_view(json.loads(row["body"]), assistant.store.clock())
                    for row in assistant.store.rows(
                        "SELECT body FROM operations WHERE chat_key=?", (key,)
                    )
                ],
                "search_scopes": len(assistant.catalog.searches),
                "queries_persisted": any(
                    query and query in json.dumps(row["body"])
                    for query in ("orbit", "older", "newer")
                    for row in assistant.catalog.storage.store.rows(
                        "SELECT body FROM media_catalogs"
                    )
                ),
                "asset_facts": {
                    name: {
                        "size": value.facts.size,
                        "sha256": value.facts.sha256,
                        "format": value.facts.format,
                    }
                    for name, value in built.get(
                        "assets", AuthoredAssets(Path("."), {}, {})
                    ).media.items()
                },
            }
        if action == "gate":
            state["gates"][body["name"]] = asyncio.Event()
        elif action in ("release", "reset"):
            names = (
                list(state["gates"])
                if action == "reset" or body.get("all")
                else [body["name"]]
            )
            for name in names:
                if gate := state["gates"].pop(name, None):
                    gate.set()
            if action == "reset":
                state["failures"].clear()
                state["faults"].clear()
                assistant.transport.connected = True
                assistant.transport.upload_failure = False
                assistant.transport.lose_receipt = False
        elif action == "fail":
            state["failures"][body["name"]] = body.get("count", 1)
        elif action == "fault":
            name = body["name"]
            if name == "reference":
                state["faults"][name] = True
            else:
                assert name in ("upload_failure", "lose_receipt", "connected")
                setattr(assistant.transport, name, body.get("value", True))
        elif action == "premium":
            assistant.transport.catalog_data = (
                assistant.transport.catalog_data.model_copy(
                    update={"premium": body["value"]}
                )
            )
            assistant.catalog.mark_stale()
        elif action == "stale":
            assistant.catalog.mark_stale()
        elif action == "retire-search":
            assistant.catalog.clear_transient(request.state.local_session, key)
        elif action == "draft":
            current = assistant.store.draft(key)
            fields = {
                name: body[name]
                for name in ("text", "undo", "state", "error", "reply_to")
                if name in body
            }
            assistant.store.mutate_draft(
                key, current["revision"], lambda draft: draft.update(fields)
            )
        elif action == "clear-draft":
            current = assistant.store.draft(key)
            assistant.store.mutate_draft(
                key,
                current["revision"],
                lambda draft: draft.update(
                    {
                        name: value
                        for name, value in blank_draft(key).items()
                        if name != "revision"
                    }
                ),
            )
        elif action == "suggestions":
            await suggestions(assistant)
        elif action == "evict":
            discard_catalog(assistant, state)
            for row in assistant.store.rows(
                "SELECT key FROM media_files WHERE key LIKE ?", (key + "/%",)
            ):
                assistant.media.cache.remove(row["key"])
        elif action == "edit":
            identity = body.get("id", 2)
            message = assistant.store.message(key, identity)
            assistant.sync.ingest(
                message.model_copy(
                    update={
                        "revision": message.revision + 1,
                        "attachment_id": f"authored-edit-{time.time_ns()}",
                    }
                ),
                target=False,
                arrival=False,
            )
            settle(assistant, body.get("peer", PEER))
        elif action == "delete":
            assistant.sync.deleted(body.get("peer", PEER), [body.get("id", 2)])
            settle(assistant, body.get("peer", PEER))
        else:
            raise AssertionError("Unknown authored media fixture action")
        return {"ok": True}
