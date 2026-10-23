"""Private message-media cache: account-local files under one global LRU.

Entries are keyed by message and variant (the file, a poster frame or a voice
note's AAC copy), bound to the source revision and expire 30 days after the
message date. Callers read through hardlinks, so eviction never pulls bytes
from under a response.
"""

import asyncio
import hashlib
import hmac
import inspect
import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

from ..config import private_directory
from ..domain.models import MediaTooLarge, Refused
from ..storage.store import RETENTION
from ..transport.media import MEDIA_FILE_PREFIX

LIMIT = 2 * 1024**3
VARIANTS = ("file", "poster", "aac")


def private_scratch(directory, *, suffix):
    """Create a private scratch file, owning setup cleanup from the first fd."""
    try:
        descriptor, raw = tempfile.mkstemp(
            prefix=MEDIA_FILE_PREFIX, suffix=suffix, dir=directory
        )
    except OSError:
        raise Refused("Private media scratch unavailable") from None
    path = Path(raw)
    owned = None
    try:
        created = os.fstat(descriptor)
        owned = created.st_dev, created.st_ino
        os.fchmod(descriptor, 0o600)
        os.close(descriptor)
    except BaseException as error:
        try:
            os.close(descriptor)
        except OSError:
            pass
        for _ in range(2):
            try:
                current = path.lstat()
                if (current.st_dev, current.st_ino) == owned:
                    path.unlink()
                break
            except FileNotFoundError:
                break
            except OSError:
                continue
        if isinstance(error, OSError):
            raise Refused("Private media scratch unavailable") from None
        raise
    return path


def _copy_retained(source, destination, *, limit, expected_sha256, expected_size):
    """Copy one bounded immutable owner file; never link durable pins to LRU."""
    try:
        before = source.lstat()
        if not stat.S_ISREG(before.st_mode) or not before.st_size:
            raise Refused("Retained media bytes unavailable")
        if before.st_size > limit:
            raise MediaTooLarge("Media exceeds size limit")
        if before.st_size != expected_size:
            raise Refused("Retained media bytes changed")

        def identity(details):
            return (
                details.st_dev,
                details.st_ino,
                details.st_size,
                details.st_mtime_ns,
                details.st_ctime_ns,
                details.st_mode,
            )

        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as reader:
            if identity(os.fstat(reader.fileno())) != identity(before):
                raise Refused("Retained media bytes changed")
            digest, size = hashlib.sha256(), 0
            # r+b cannot recreate a scratch path removed after cancellation.
            with destination.open("r+b") as writer:
                while chunk := reader.read(64 * 1024):
                    size += len(chunk)
                    if size > limit:
                        raise MediaTooLarge("Media exceeds size limit")
                    digest.update(chunk)
                    writer.write(chunk)
                writer.truncate()
            if (
                identity(os.fstat(reader.fileno())) != identity(before)
                or identity(source.lstat()) != identity(before)
                or size != expected_size
                or not hmac.compare_digest(digest.hexdigest(), expected_sha256)
            ):
                raise Refused("Retained media bytes changed")
        return size
    except OSError:
        raise Refused("Retained media bytes unavailable") from None


def evict(caches, limit=LIMIT):
    """Remove least-recently-used entries across accounts until under limit."""
    rows = [
        (row["used"], cache, row["key"], row["size"])
        for cache in caches
        for row in cache.store.rows("SELECT key,size,used FROM media_files")
    ]
    total = sum(row[3] for row in rows)
    for _, cache, key, size in sorted(rows, key=lambda row: row[0]):
        if total <= limit:
            break
        cache.remove(key)
        total -= size
    return total


class MediaCache:
    def __init__(self, store, transport, *, limit=LIMIT):
        self.store, self.transport, self.limit = store, transport, limit
        self.directory = private_directory(store.path.parent / "media-cache")
        self.locks: dict[str, asyncio.Lock] = {}
        self.generation = 0
        self.evict = lambda: evict([self], self.limit)
        # Upgrade old cache keys in place. No download or extra retained copy.
        for row in store.rows("SELECT key,revision FROM media_files"):
            source, variant = row["key"].rsplit("#", 1)
            if "@" in source:
                continue
            entry = f"{source}@{row['revision']}#{variant}"
            old, new = self.path(row["key"]), self.path(entry)
            if old.is_file() and not new.exists():
                os.link(old, new)
            with store.transaction() as db:
                db.execute(
                    "UPDATE media_files SET key=? WHERE key=?", (entry, row["key"])
                )
            old.unlink(missing_ok=True)

    @staticmethod
    def entry(message, variant="file"):
        return f"{message.key}@{message.revision}#{variant}"

    @staticmethod
    def catalog_entry(identity, revision, variant):
        if variant not in ("thumbnail", "media"):
            raise Refused("Catalog asset variant unavailable")
        return f"catalog/{hashlib.sha256(identity.encode()).hexdigest()}@{revision}#{variant}"

    def path(self, entry):
        return self.directory / (hashlib.sha256(entry.encode()).hexdigest() + ".bin")

    def get(self, message, variant="file", *, limit=None):
        if message.account_id != self.store.account_id:
            raise Refused("Media belongs to another account")
        if message.date < self.store.clock() - RETENTION:
            return None
        entry = self.entry(message, variant)
        rows = self.store.rows(
            "SELECT * FROM media_files WHERE key=? AND revision=?",
            (entry, message.revision),
        )
        path = self.path(entry)
        if not rows:
            return None
        try:
            actual = path.lstat()
        except FileNotFoundError:
            return None
        if limit is not None and max(actual.st_size, rows[0]["size"]) > limit:
            self.remove(entry)
            raise MediaTooLarge("Media exceeds size limit")
        if not stat.S_ISREG(actual.st_mode) or actual.st_size != rows[0]["size"]:
            self.remove(entry)
            return None
        with self.store.transaction() as db:
            db.execute(
                "UPDATE media_files SET used=? WHERE key=?", (self.store.clock(), entry)
            )
        return {**rows[0], "path": path}

    def get_catalog(self, identity, revision, variant, *, limit):
        """Disposable catalog bytes share this cache without a fake message."""
        entry = self.catalog_entry(identity, revision, variant)
        rows = self.store.rows(
            "SELECT * FROM media_files WHERE key=? AND revision=?", (entry, revision)
        )
        if not rows:
            return None
        if rows[0]["date"] < self.store.clock() - RETENTION:
            self.remove(entry)
            return None
        path = self.path(entry)
        try:
            actual = path.lstat()
        except FileNotFoundError:
            self.remove(entry)
            return None
        if max(actual.st_size, rows[0]["size"]) > limit:
            self.remove(entry)
            raise MediaTooLarge("Catalog asset exceeds size limit")
        if (
            not stat.S_ISREG(actual.st_mode)
            or not actual.st_size
            or actual.st_size != rows[0]["size"]
        ):
            self.remove(entry)
            return None
        with self.store.transaction() as db:
            db.execute(
                "UPDATE media_files SET used=? WHERE key=?", (self.store.clock(), entry)
            )
        return {**rows[0], "path": path}

    async def fetch_catalog(
        self, identity, revision, variant, *, limit, download, guard=None, timeout=90
    ):
        """One guarded exact asset transfer, using the existing global LRU."""

        generation = self.generation

        def allowed():
            if generation != self.generation:
                raise Refused("Catalog cache changed; retry")
            if guard:
                guard()

        allowed()
        found = self.get_catalog(identity, revision, variant, limit=limit)
        if found is not None:
            return found
        entry = self.catalog_entry(identity, revision, variant)
        async with self.locks.setdefault(entry, asyncio.Lock()):
            allowed()
            found = self.get_catalog(identity, revision, variant, limit=limit)
            if found is not None:
                return found
            partial = private_scratch(self.directory, suffix=".part")
            try:
                # Native/web exact downloaders create their own exclusive600 file.
                partial.unlink()
                async with asyncio.timeout(timeout):
                    await download(partial)
                allowed()
                actual = partial.lstat()
                if not stat.S_ISREG(actual.st_mode) or not actual.st_size:
                    raise Refused("Catalog asset unavailable")
                if actual.st_size > limit:
                    raise MediaTooLarge("Catalog asset exceeds size limit")
                os.chmod(partial, 0o600)
                os.replace(partial, self.path(entry))
                now = self.store.clock()
                with self.store.transaction() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO media_files VALUES (?,?,?,?,?,?,?)",
                        (entry, revision, variant, actual.st_size, None, now, now),
                    )
            finally:
                partial.unlink(missing_ok=True)
            self.evict()
            allowed()
            found = self.get_catalog(identity, revision, variant, limit=limit)
            if found is None:
                raise Refused("Catalog asset was evicted before use; retry")
            return found

    async def retain(
        self, message, source, *, limit, expected_sha256, expected_size, guard=None
    ):
        """Copy confirmed current-source bytes into disposable source-date cache."""
        generation = self.generation

        def allowed():
            if guard:
                guard()
            if generation != self.generation:
                raise Refused("Media cache changed; retry")
            if message.account_id != self.store.account_id:
                raise Refused("Media belongs to another account")
            if message.deleted or message.date < self.store.clock() - RETENTION:
                raise Refused("Media source has expired")
            stored = self.store.message(
                f"{message.account_id}/{message.peer}", message.message_id
            )
            if not stored or stored.deleted or stored.revision != message.revision:
                raise Refused("Media source changed during preparation")

        allowed()
        found = self.get(message, limit=limit)
        if found is not None:
            return found
        entry = self.entry(message)
        async with self.locks.setdefault(entry, asyncio.Lock()):
            allowed()
            found = self.get(message, limit=limit)
            if found is not None:
                return found
            partial = private_scratch(self.directory, suffix=".part")
            try:
                size = await asyncio.to_thread(
                    _copy_retained,
                    source,
                    partial,
                    limit=limit,
                    expected_sha256=expected_sha256,
                    expected_size=expected_size,
                )
                allowed()
                os.replace(partial, self.path(entry))
                with self.store.transaction() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO media_files VALUES (?,?,?,?,?,?,?)",
                        (
                            entry,
                            message.revision,
                            "file",
                            size,
                            None,
                            message.date,
                            self.store.clock(),
                        ),
                    )
            finally:
                partial.unlink(missing_ok=True)
            self.evict()
            allowed()
            found = self.get(message, limit=limit)
            if found is None:
                raise Refused("Media was evicted before use; retry")
            return found

    async def fetch(
        self,
        message,
        *,
        limit,
        priority,
        variant="file",
        check=None,
        timeout=600,
        guard=None,
    ):
        """The cached entry, downloading it once through the file lane."""
        generation = self.generation

        def allowed():
            if generation != self.generation:
                raise Refused("Media cache changed; retry")
            if guard:
                guard()

        allowed()
        if message.date < self.store.clock() - RETENTION:
            raise Refused("Media source has expired")
        info = message.media_info
        if variant == "file" and info and info.size is not None and info.size > limit:
            raise MediaTooLarge("Media exceeds size limit")
        found = self.get(message, variant, limit=limit)
        if found is None:
            entry = self.entry(message, variant)
            lock = self.locks.setdefault(entry, asyncio.Lock())
            # Simultaneous reads of one entry coalesce into one download.
            async with lock:
                allowed()
                found = self.get(message, variant, limit=limit) or await self._download(
                    message, entry, variant, limit, priority, check, timeout, allowed
                )
        allowed()
        if found["size"] > limit:
            raise MediaTooLarge("Media exceeds size limit")
        return found

    async def _download(
        self, message, entry, variant, limit, priority, check, timeout, guard
    ):
        partial = private_scratch(self.directory, suffix=".part")
        try:
            async with asyncio.timeout(timeout):
                await self.transport.download(
                    message.peer,
                    message.message_id,
                    partial,
                    max_bytes=limit,
                    expected_revision=message.revision,
                    priority=priority,
                    thumb=variant == "poster",
                )
            guard()
            if partial.stat().st_size > limit:
                raise MediaTooLarge("Media exceeds size limit")
            mime = check(partial) if check else None
            if inspect.isawaitable(mime):
                mime = await mime
            guard()
            stored = self.store.message(
                f"{message.account_id}/{message.peer}", message.message_id
            )
            # An edit or deletion during the download must not cache stale bytes.
            if message.date < self.store.clock() - RETENTION:
                raise Refused("Media source expired during download")
            if stored and (stored.deleted or stored.revision != message.revision):
                raise Refused("Media source changed during download")
            size = partial.stat().st_size
            if size > limit:
                raise MediaTooLarge("Media exceeds size limit")
            os.replace(partial, self.path(entry))
            with self.store.transaction() as db:
                db.execute(
                    "INSERT OR REPLACE INTO media_files VALUES (?,?,?,?,?,?,?)",
                    (
                        entry,
                        message.revision,
                        variant,
                        size,
                        mime,
                        message.date,
                        self.store.clock(),
                    ),
                )
        finally:
            partial.unlink(missing_ok=True)
        self.evict()
        found = self.get(message, variant)
        if found is None:
            raise Refused("Media was evicted before use; retry")
        return found

    async def derive(self, message, variant, produce, *, mime, limit, guard=None):
        """A copy made from other bytes, such as a voice note's AAC copy, kept
        and expiring like its source. `produce` writes the given private path;
        simultaneous reads of one entry make it once."""
        generation = self.generation

        def allowed():
            if generation != self.generation:
                raise Refused("Media cache changed; retry")
            if guard:
                guard()

        allowed()
        if message.date < self.store.clock() - RETENTION:
            raise Refused("Media source has expired")
        found = self.get(message, variant, limit=limit)
        if found is not None:
            return found
        entry = self.entry(message, variant)
        async with self.locks.setdefault(entry, asyncio.Lock()):
            allowed()
            found = self.get(message, variant, limit=limit)
            if found is not None:
                return found
            partial = private_scratch(self.directory, suffix=".part")
            try:
                await produce(partial)
                allowed()
                stored = self.store.message(
                    f"{message.account_id}/{message.peer}", message.message_id
                )
                if message.date < self.store.clock() - RETENTION:
                    raise Refused("Media source expired during preparation")
                if stored and (stored.deleted or stored.revision != message.revision):
                    raise Refused("Media source changed during preparation")
                size = partial.stat().st_size
                if size > limit:
                    raise MediaTooLarge("Media exceeds size limit")
                os.replace(partial, self.path(entry))
                with self.store.transaction() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO media_files VALUES (?,?,?,?,?,?,?)",
                        (
                            entry,
                            message.revision,
                            variant,
                            size,
                            mime,
                            message.date,
                            self.store.clock(),
                        ),
                    )
            finally:
                partial.unlink(missing_ok=True)
        self.evict()
        allowed()
        found = self.get(message, variant, limit=limit)
        if found is None:
            raise Refused("Media was evicted before use; retry")
        return found

    def link(self, found, destination: Path):
        """Hardlink cached bytes to a private scratch path the caller owns."""
        destination.unlink(missing_ok=True)
        os.link(found["path"], destination)

    def remove(self, entry):
        with self.store.transaction() as db:
            db.execute("DELETE FROM media_files WHERE key=?", (entry,))
        self.path(entry).unlink(missing_ok=True)

    def discard(self, message_key):
        """Explicitly discard all revisions of a source, never on native edit."""
        for row in self.store.rows(
            "SELECT key FROM media_files WHERE key LIKE ?", (message_key + "@%",)
        ):
            self.remove(row["key"])

    def clear(self):
        self.generation += 1
        with self.store.transaction() as db:
            db.execute("DELETE FROM media_files")
        if self.directory.exists():
            shutil.rmtree(self.directory)
        private_directory(self.directory)

    def collect(self):
        cutoff = self.store.clock() - RETENTION
        for row in self.store.rows(
            "SELECT key FROM media_files WHERE date<?", (cutoff,)
        ):
            self.remove(row["key"])
        known = set()
        for row in self.store.rows("SELECT key FROM media_files"):
            path = self.path(row["key"])
            if path.is_file():
                known.add(path.name)
            else:
                self.remove(row["key"])
        stale = time.time() - 900
        for path in self.directory.iterdir():
            try:
                if path.name in known:
                    continue
                if path.suffix == ".part" and path.lstat().st_mtime > stale:
                    continue  # An in-flight download.
                path.unlink()
            except OSError:
                continue

    def usage(self):
        return self.store.rows(
            "SELECT count(*) AS files,coalesce(sum(size),0) AS bytes FROM media_files"
        )[0]
