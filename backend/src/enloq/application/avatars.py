"""Current small native photos: account-local, bounded, never model context."""

import asyncio
import hashlib
import os
import stat

from PIL import Image

from ..config import private_directory
from ..domain.models import Refused
from ..storage.store import RETENTION
from ..transport.media import MediaBounds, MediaWorkspace, validate_image

BOUNDS = MediaBounds.narrowed(
    mime_types=frozenset({"image/jpeg", "image/png", "image/webp"}),
    image_bytes=256 * 1024,
    image_dimension=1024,
    image_pixels=1024 * 1024,
)
CACHE_ENTRIES = 256


class Avatars:
    def __init__(self, store, transport):
        self.store, self.transport = store, transport
        self.directory = private_directory(store.path.parent / "avatars")
        self.scratch = MediaWorkspace(self.directory / "pending")
        self.scratch.prepare()
        self.scratch.cleanup_orphans(older_than_seconds=900)
        # Serial native reads also coalesce simultaneous row/header requests.
        self.lock = asyncio.Lock()
        self.own_photo = lambda: None
        self.admit = lambda: None

    def current(self, peer):
        if peer == f"user:{self.store.account_id}":
            return self.own_photo()
        try:
            chat = self.store.chat(f"{self.store.account_id}/{peer}")
            if chat.kind in {"private", "group"}:
                return chat.photo_id
        except Refused:
            pass
        # A group member seen as an author, for their avatar (ADR 51 Q13).
        return self.store.author_photo(peer) if peer.startswith("user:") else None

    def path(self, peer, photo_id):
        digest = hashlib.sha256(
            f"{self.store.account_id}/{peer}/{photo_id}".encode()
        ).hexdigest()
        return self.directory / f"{digest}.img"

    def discard(self, row):
        self.path(row["peer"], row["photo_id"]).unlink(missing_ok=True)
        with self.store.transaction() as db:
            db.execute(
                "DELETE FROM avatars WHERE peer=? AND photo_id=?",
                (row["peer"], row["photo_id"]),
            )

    def collect(self):
        rows = self.store.rows("SELECT * FROM avatars ORDER BY expires DESC,rowid DESC")
        retained = set()
        for row in rows:
            if (
                row["expires"] <= self.store.clock()
                or self.current(row["peer"]) != row["photo_id"]
                or len(retained) >= CACHE_ENTRIES
            ):
                self.discard(row)
            else:
                retained.add(self.path(row["peer"], row["photo_id"]))
        for path in self.directory.glob("*.img"):
            if path not in retained:
                path.unlink(missing_ok=True)

    def clear(self):
        for row in self.store.rows("SELECT * FROM avatars"):
            self.discard(row)
        self.collect()

    def valid(self, peer, photo_id):
        self.admit()
        if not photo_id or self.current(peer) != photo_id:
            raise Refused("Current profile photo unavailable")

    def cached(self, peer, photo_id):
        rows = self.store.rows(
            "SELECT * FROM avatars WHERE peer=? AND photo_id=?", (peer, photo_id)
        )
        if not rows or rows[0]["expires"] <= self.store.clock():
            return None

        row = rows[0]
        if row["mime"] is None:
            raise Refused("Profile photo temporarily unavailable")
        try:
            fd = os.open(self.path(peer, photo_id), os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, "rb") as stream:
                info = os.fstat(stream.fileno())
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o077
                    or not 0 < info.st_size <= BOUNDS.max_bytes
                ):
                    raise OSError("Invalid private photo")
                data = stream.read(BOUNDS.max_bytes + 1)
            if len(data) > BOUNDS.max_bytes:
                raise OSError("Photo grew beyond its bound")
            return data, row["mime"]
        except OSError:
            self.discard(row)
            return None

    def native_valid(self, peer, photo_id, revision=None):
        self.valid(peer, photo_id)
        if peer.startswith(("chat:", "channel:")):
            state = self.store.groups.get(f"{self.store.account_id}/{peer}")
            if (
                not state.supported
                or state.access != "available"
                or not any(
                    row["peer"] == peer for row in self.store.recent_chats("group")
                )
                or (revision is not None and state.access_revision != revision)
            ):
                raise Refused("Native group profile photo unavailable")
            return state.access_revision
        return None

    async def get(self, peer, photo_id):
        self.valid(peer, photo_id)
        value = self.cached(peer, photo_id)
        if value:
            return value
        async with self.lock:
            self.valid(peer, photo_id)
            self.collect()
            value = self.cached(peer, photo_id)
            if value:
                return value
            native_revision = self.native_valid(peer, photo_id)
            try:
                with self.scratch.lease() as path:
                    async with asyncio.timeout(30):
                        await self.transport.download_avatar(
                            peer, photo_id, path, max_bytes=BOUNDS.max_bytes
                        )
                    self.native_valid(peer, photo_id, native_revision)
                    facts = validate_image(path, BOUNDS)
                    with Image.open(path) as image:
                        image.verify()
                    with Image.open(path) as image:
                        image.load()
                    path.chmod(0o600)
                    os.replace(path, self.path(peer, photo_id))
                    with self.store.transaction() as db:
                        db.execute(
                            "INSERT OR REPLACE INTO avatars VALUES (?,?,?,?)",
                            (
                                peer,
                                photo_id,
                                facts.mime_type,
                                self.store.clock() + RETENTION,
                            ),
                        )
            except asyncio.CancelledError:
                raise
            except Exception:
                # A short negative entry coalesces failures without a fetch loop.
                try:
                    self.native_valid(peer, photo_id, native_revision)
                except Refused:
                    pass
                else:
                    with self.store.transaction() as db:
                        db.execute(
                            "INSERT OR REPLACE INTO avatars VALUES (?,?,NULL,?)",
                            (peer, photo_id, self.store.clock() + 60),
                        )
                raise Refused("Profile photo temporarily unavailable") from None
            self.collect()
            self.native_valid(peer, photo_id, native_revision)
            value = self.cached(peer, photo_id)
            if value is None:
                raise Refused("Profile photo unavailable")
            return value
