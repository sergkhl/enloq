"""Isolated authored ADR 33 browser fixture; never reads populated config.

The generated six-second MP4/WebM contain a plain coloured scene and silence.
All transfers use FakeTransport. Gated/failing transfers expose lifecycle
branches without reaching Telegram or a model provider.
"""

import asyncio
import time
from io import BytesIO
from pathlib import Path

from enloq.config import private_directory
from enloq.domain.models import Chat, MediaInfo, Message, Unavailable
from enloq.inference.runner import bounded_process
from enloq.media_tools import FFMPEG
from fastapi import Request
from PIL import Image

PEER = "user:161"
TITLE = "Video player"
FIXTURES = Path(__file__).resolve().parents[1] / "backend/tests/fixtures"


def register(app):
    started = time.time()
    assets = {}
    states = {}
    generation = asyncio.Lock()

    async def generated():
        async with generation:
            if assets:
                return assets
            directory = private_directory(
                app.state.accounts.registry.root / "authored-video-assets"
            )
            for name, encoding in (
                (
                    "scene.mp4",
                    [
                        "-c:v",
                        "libx264",
                        "-profile:v",
                        "baseline",
                        "-pix_fmt",
                        "yuv420p",
                        "-preset",
                        "ultrafast",
                        "-c:a",
                        "aac",
                        "-b:a",
                        "64k",
                        "-movflags",
                        "+faststart",
                    ],
                ),
                (
                    "scene.webm",
                    [
                        "-c:v",
                        "libvpx-vp9",
                        "-deadline",
                        "realtime",
                        "-cpu-used",
                        "8",
                        "-c:a",
                        "libopus",
                    ],
                ),
            ):
                path = directory / name
                await bounded_process(
                    [
                        FFMPEG,
                        "-hide_banner",
                        "-loglevel",
                        "error",
                        "-nostdin",
                        "-f",
                        "lavfi",
                        "-i",
                        "color=c=0x315a6c:s=320x180:r=24",
                        "-f",
                        "lavfi",
                        "-i",
                        "anullsrc=r=48000:cl=stereo",
                        "-vf",
                        "drawbox=x=45:y=45:w=100:h=65:color=0xd6d3bd:t=fill",
                        "-t",
                        "6",
                        "-shortest",
                        *encoding,
                        "-threads",
                        "1",
                        "-y",
                        str(path),
                    ],
                    stdin=b"",
                    cwd=directory,
                    env={"PATH": "/opt/homebrew/bin:/usr/bin:/bin", "LANG": "C"},
                    timeout=30,
                    stdout_limit=1024,
                    stderr_limit=4096,
                )
                assets[name] = path.read_bytes()
            for name in ("tone.ogg", "circle.mp4", "loop.mp4"):
                assets[name] = (FIXTURES / name).read_bytes()
            poster = BytesIO()
            Image.new("RGB", (320, 180), (49, 90, 108)).save(poster, format="JPEG")
            assets["poster"] = poster.getvalue()
            return assets

    def state_for(account):
        if account in states:
            return states[account]
        conversation = app.state.accounts.get(account)
        state = states[account] = {"requests": [], "gates": {}, "failures": {}}
        previous = conversation.transport.stream

        async def stream(peer, identity, **fields):
            if peer != PEER:
                async for chunk in previous(peer, identity, **fields):
                    yield chunk
                return
            thumb = fields.get("thumb", False)
            record = {
                "id": identity,
                "poster": thumb,
                "revision": fields.get("expected_revision"),
                "priority": fields.get("priority"),
                "completed": False,
                "failed": False,
            }
            state["requests"].append(record)
            try:
                if not thumb and identity in state["gates"]:
                    await state["gates"][identity].wait()
                if not thumb and state["failures"].get(identity, 0):
                    state["failures"][identity] -= 1
                    raise Unavailable("Authored temporary video transfer failure")
                async for chunk in previous(peer, identity, **fields):
                    yield chunk
                record["completed"] = True
            except BaseException:
                record["failed"] = True
                raise

        conversation.transport.stream = stream
        return state

    def settle(conversation, *, ready=True):
        with conversation.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='superseded' WHERE scope LIKE ? AND state='queued'",
                (f"{conversation.store.account_id}/{PEER}%",),
            )
            db.execute(
                "UPDATE blocks SET state=?,rendered=?,rendered_revision=CASE WHEN ? THEN revision ELSE NULL END,error=NULL,unanalysed='[]' WHERE chat_key=?",
                (
                    "ready" if ready else "original",
                    "Authored video-group rewrite." if ready else None,
                    ready,
                    f"{conversation.store.account_id}/{PEER}",
                ),
            )

    async def seed(account, case, ready=True):
        conversation = app.state.accounts.get(account)
        state = state_for(account)
        for gate in state["gates"].values():
            gate.set()
        state["gates"].clear()
        state["failures"].clear()
        state["requests"].clear()
        key = f"{account}/{PEER}"
        for row in conversation.store.rows(
            "SELECT key FROM media_files WHERE key LIKE ?", (key + "/%",)
        ):
            conversation.media.cache.remove(row["key"])
        with conversation.store.transaction() as db:
            db.execute("DELETE FROM jobs WHERE scope LIKE ?", (key + "%",))
            for table in ("blocks", "messages"):
                db.execute(f"DELETE FROM {table} WHERE chat_key=?", (key,))
            for table in ("derivatives", "message_versions"):
                db.execute(f"DELETE FROM {table} WHERE source LIKE ?", (key + "/%",))
        conversation.media.missing = {
            entry: value
            for entry, value in conversation.media.missing.items()
            if not entry[0].startswith(key + "/")
        }
        conversation.store.put_chat(
            Chat(account_id=account, peer=PEER, title=TITLE, kind="private")
        )
        media = await generated()
        descriptions = {
            1: ("video", "scene.mp4", "incoming scene.mp4", "video/mp4", False),
            2: (
                "document",
                "scene.mp4",
                "outgoing file.MP4",
                "application/octet-stream",
                True,
            ),
            3: ("video", "scene.mp4", "outgoing scene.mp4", "video/mp4", True),
            4: ("document", "scene.mp4", "incoming mime-only.txt", "video/mp4", False),
            5: ("document", "scene.webm", "authored scene.webm", "video/webm", False),
            6: ("document", None, "corrupt.mp4", "video/mp4", False),
            7: ("document", None, "ordinary.txt", "text/plain", False),
            8: ("video", None, "oversize.mp4", "video/mp4", False),
            9: ("video", None, "unavailable.mp4", "video/mp4", False),
            10: ("audio", "tone.ogg", None, "audio/ogg", False),
            11: ("audio", "tone.ogg", "authored tone.ogg", "audio/ogg", False),
            12: ("video_note", "circle.mp4", None, "video/mp4", False),
            13: ("gif", "loop.mp4", "authored loop.mp4", "video/mp4", False),
            15: (
                "document",
                "scene.mp4",
                "valid container.mov",
                "application/octet-stream",
                False,
            ),
            16: ("document", None, "unsupported.mov", "video/quicktime", False),
            17: ("video", "scene.mp4", None, None, False),
            # VP9, which the journeys' Chromium decodes (it has no H.264).
            18: ("video", "scene.webm", None, "video/webm", False),
            19: ("video_note", "scene.webm", None, "video/webm", False),
        }
        descriptions[50] = descriptions[2]
        if case == "continuation":
            descriptions[11] = ("audio", "tone.ogg", None, "audio/ogg", False)
        identities = {
            "single": [1],
            "basic": [1, 2, 3, 4, 15, 17],
            "group": [1, 4, 15],
            "ownership": [1, 2, 10, 11, 12, 13],
            "continuation": [1, 10, 11],
            "formats": [5, 6, 7, 8, 9, 16, 17],
            "lazy": [1, *range(20, 45), 50],
            "playable": [10, 11, 18, 19],
        }[case]
        for order, identity in enumerate(identities):
            kind, name, file_name, mime, outgoing = descriptions.get(
                identity, (None, None, None, None, False)
            )
            data = media[name] if name else None
            if identity in (6, 7, 16):
                data = b"Authored non-video bytes."
            if data is not None:
                conversation.transport.media[PEER, identity] = data
            else:
                conversation.transport.media.pop((PEER, identity), None)
            if identity in (1, 2, 12, 18, 19, 50):
                conversation.transport.media[PEER, identity, "poster"] = media["poster"]
            else:
                conversation.transport.media.pop((PEER, identity, "poster"), None)
            info = (
                MediaInfo(
                    file_name=file_name,
                    mime=mime,
                    size=(50 * 1024**2 + 1)
                    if identity == 8
                    else (
                        6 * 1024**2 if identity == 1 else len(data) if data else None
                    ),
                    duration=2 if identity in (10, 11, 12) else 6,
                    width=320 if kind in ("video", "document") else None,
                    height=180 if kind in ("video", "document") else None,
                    voice=identity == 10 or (case == "continuation" and identity == 11),
                    waveform=[index % 32 for index in range(48)]
                    if identity == 10
                    else None,
                )
                if kind
                else None
            )
            conversation.sync.ingest(
                Message(
                    account_id=account,
                    peer=PEER,
                    message_id=identity,
                    revision=1,
                    date=started - 600 + order,
                    text=f"Authored {kind or 'text'} source {identity}.",
                    media=kind,
                    media_info=info,
                    attachment_id=f"authored-video-{identity}" if kind else None,
                    outgoing=outgoing,
                    forwarded=False,
                ),
                target=False,
                arrival=False,
            )
        settle(conversation, ready=ready)
        return identities

    @app.post("/api/test/video")
    async def video(request: Request):
        body = await request.json()
        action = body.get("action", "setup")
        account = body.get("account", 1)
        assert account in (1, 2)
        conversation = app.state.accounts.get(account)
        state = state_for(account)
        identity = body.get("id", 1)
        key = f"{account}/{PEER}"
        if action == "setup":
            identities = await seed(
                account, body.get("case", "single"), body.get("ready", True)
            )
            return {"ids": identities}
        if action == "stats":
            return {"requests": state["requests"]}
        if action == "gate":
            state["gates"][identity] = asyncio.Event()
        elif action == "release":
            identities = list(state["gates"]) if body.get("all") else [identity]
            for pending in identities:
                if gate := state["gates"].pop(pending, None):
                    gate.set()
        elif action == "fail":
            state["failures"][identity] = body.get("count", 1)
        elif action == "evict":
            message = conversation.store.message(key, identity)
            revision = body.get("revision", message.revision)
            conversation.media.cache.remove(f"{message.key}@{revision}#file")
        elif action == "ready":
            settle(conversation, ready=True)
        elif action == "edit":
            message = conversation.store.message(key, identity)
            conversation.sync.ingest(
                message.model_copy(
                    update={
                        "revision": message.revision + 1,
                        "attachment_id": f"authored-revised-{time.time_ns()}",
                        "text": f"Authored revised video source {identity}.",
                    }
                ),
                target=False,
                arrival=False,
            )
            settle(conversation, ready=body.get("ready", True))
        elif action == "delete":
            conversation.sync.deleted(PEER, [identity])
            settle(conversation)
        elif action == "expire":
            message = conversation.store.message(key, identity)
            expired = message.model_copy(update={"date": time.time() - 31 * 86400})
            with conversation.store.transaction() as db:
                db.execute(
                    "UPDATE messages SET date=?,body=? WHERE key=?",
                    (expired.date, expired.model_dump_json(), expired.key),
                )
        else:
            return {"ok": False}
        return {"ok": True}
