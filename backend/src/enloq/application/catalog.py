"""Cached account libraries, explicit metadata refresh and transient GIF search."""

import asyncio
import uuid
from dataclasses import dataclass

from ..domain.catalog import (
    SOURCE,
    CatalogItem,
    CatalogLibrary,
    CatalogPack,
    CatalogPackData,
    CatalogPackView,
    CatalogPreview,
    CatalogSearchData,
    CatalogView,
    GifSearchScope,
    GifSearchView,
    NativeCatalogSource,
    digest,
    pack_identity,
    pack_revision,
    source_identity,
    source_revision,
)
from ..domain.models import Conflict, Refused, Unavailable
from ..storage.catalogs import CatalogStore
from .group_access import native_access

CATALOG_TTL = 15 * 60
TICKET_TTL = 30 * 60
TICKET_LIMIT = 8192
SEARCH_SCOPES = 128
ENTITLEMENT_TIMEOUT = 15


@dataclass(frozen=True)
class Ticket:
    session: str
    key: str
    source: object
    revision: str
    expires: float
    scope: str | None
    generation: str | None
    access_revision: int | None


class MediaCatalog:
    def __init__(self, store, transport, *, authorized=lambda: True):
        self.store, self.transport, self.authorized = store, transport, authorized
        self.storage = CatalogStore(store)
        self.lock = asyncio.Lock()
        self.tickets = {}
        self.ticket_index = {}
        self.searches = {}
        self.changes = 0
        transport.catalog_changed = self.mark_stale

    def allowed(self, key, guard=None):
        if guard:
            guard()
        if not self.authorized():
            raise Refused("Account authorization changed")
        if self.store.chat(key).kind == "group" and not any(
            row["key"] == key for row in self.store.recent_chats("group")
        ):
            raise Refused("Inactive group work is available for retained recovery only")
        return native_access(self.store, key)

    def access_guard(self, key, guard=None):
        revision = self.allowed(key, guard)

        def current():
            self.allowed(key, guard)
            native_access(self.store, key, revision)

        return current

    def mark_stale(self):
        self.changes += 1
        self.storage.mark_stale()

    def clear_transient(self, session=None, key=None):
        self.tickets = {
            token: value
            for token, value in self.tickets.items()
            if (session is not None and value.session != session)
            or (key is not None and value.key != key)
        }
        self.searches = {
            scope: value
            for scope, value in self.searches.items()
            if (session is not None and scope[0] != session)
            or (key is not None and scope[1] != key)
        }
        self.index_tickets()

    def clear(self):
        # Logout/drain calls remain valid after authorization was withdrawn.
        self.clear_transient()
        self.storage.clear()

    def library(self):
        row = self.storage.get("library")
        return (
            CatalogLibrary.model_validate(row["body"]) if row else CatalogLibrary()
        ), row

    @staticmethod
    def library_revision(value):
        return digest(
            [
                value.premium,
                [
                    [source_revision(item) for item in getattr(value, field)]
                    for field in ("gifs", "recent", "favorites")
                ],
                [pack_revision(pack) for pack in value.packs],
                value.truncated,
            ]
        )

    def stale(self, row):
        return (
            not row
            or bool(row["stale"])
            or row["updated"] is None
            or row["updated"] < self.store.clock() - CATALOG_TTL
        )

    def cleanup(self):
        now = self.store.clock()
        self.tickets = {
            token: ticket
            for token, ticket in self.tickets.items()
            if ticket.expires > now
        }
        self.searches = {
            scope: value
            for scope, value in self.searches.items()
            if value["expires"] > now
        }
        if len(self.tickets) >= TICKET_LIMIT:
            keep = sorted(
                self.tickets,
                key=lambda token: self.tickets[token].expires,
                reverse=True,
            )[: TICKET_LIMIT // 2]
            self.tickets = {token: self.tickets[token] for token in keep}
        if len(self.searches) >= SEARCH_SCOPES:
            keep = sorted(
                self.searches,
                key=lambda scope: self.searches[scope]["expires"],
                reverse=True,
            )[: SEARCH_SCOPES // 2]
            self.searches = {scope: self.searches[scope] for scope in keep}
        self.index_tickets()

    def index_tickets(self):
        self.ticket_index = {
            (
                ticket.session,
                ticket.key,
                ticket.revision,
                ticket.scope,
                ticket.generation,
                ticket.access_revision,
            ): token
            for token, ticket in self.tickets.items()
        }

    def item(self, source, key, session, *, premium, scope="library", generation=None):
        if source.account_id != self.store.account_id:
            raise Refused("Catalog source belongs to another account")
        revision = source_revision(source)
        access_revision = self.allowed(key)
        lookup = (session, key, revision, scope, generation, access_revision)
        token = self.ticket_index.get(lookup)
        if token is None:
            if len(self.tickets) >= TICKET_LIMIT:
                self.cleanup()
            token = uuid.uuid4().hex
        detached = SOURCE.validate_python(source.model_dump(mode="json"))
        self.tickets[token] = Ticket(
            session,
            key,
            detached,
            revision,
            self.store.clock() + TICKET_TTL,
            scope,
            generation,
            access_revision,
        )
        self.ticket_index[lookup] = token
        unavailable = source.metadata.unavailable
        if source.metadata.requires_premium and premium is not True:
            unavailable = (
                "Telegram Premium is required"
                if premium is False
                else "Account entitlement is not verified"
            )
        return CatalogItem(
            token=token,
            revision=revision,
            metadata=source.metadata,
            selectable=unavailable is None,
            unavailable=unavailable,
            preview=self.preview_value(token, revision, source),
        )

    @staticmethod
    def preview_value(token, revision, source):
        thumbnail = (
            isinstance(source, NativeCatalogSource) and source.thumbnail is not None
        )
        web_thumb = getattr(source, "thumbnail_url", None) is not None
        return CatalogPreview(
            token=token,
            revision=revision,
            kind="thumbnail"
            if thumbnail or web_thumb
            else "unavailable"
            if source.metadata.unavailable
            else "media",
            format=source.metadata.format,
            width=source.metadata.width,
            height=source.metadata.height,
        )

    def pack_value(self, pack):
        identity = pack_identity(pack, self.store.account_id)
        row = self.storage.get("pack:" + identity)
        current = bool(row and row["revision"] == pack_revision(pack))
        return CatalogPack(
            id=identity,
            revision=pack_revision(pack),
            title=pack.title,
            short_name=pack.short_name,
            count=pack.count,
            cached=bool(row),
            stale=not current or self.stale(row),
        )

    def cached(self, key, session):
        self.allowed(key)
        self.cleanup()
        value, row = self.library()
        return CatalogView(
            revision=self.library_revision(value),
            updated_at=row["updated"] if row else None,
            stale=self.stale(row),
            premium=value.premium,
            truncated=value.truncated,
            error=row["error"] if row else None,
            gifs=[
                self.item(item, key, session, premium=value.premium)
                for item in value.gifs
            ],
            recent=[
                self.item(item, key, session, premium=value.premium)
                for item in value.recent
            ],
            favorites=[
                self.item(item, key, session, premium=value.premium)
                for item in value.favorites
            ],
            packs=[self.pack_value(pack) for pack in value.packs],
        )

    async def refresh(self, key, session, revision, *, guard=None):
        current_access = self.access_guard(key, guard)
        before, _ = self.library()
        if self.library_revision(before) != revision:
            raise Conflict("Library changed; reopen the picker")
        async with self.lock:
            current_access()
            current, _ = self.library()
            if self.library_revision(current) != revision:
                return self.cached(key, session)
            changes = self.changes
            try:
                value = await self.transport.catalog_library()
            except asyncio.CancelledError:
                raise
            except Exception:
                current_access()
                self.storage.failed("library", current, self.library_revision(current))
                return self.cached(key, session)
            current_access()
            value = CatalogLibrary.model_validate(value)
            if any(
                item.account_id != self.store.account_id
                for name in ("gifs", "recent", "favorites")
                for item in getattr(value, name)
            ):
                raise Refused("Catalog source belongs to another account")
            self.storage.put("library", value, self.library_revision(value))
            self.storage.prune_packs(
                {
                    "pack:" + pack_identity(pack, self.store.account_id)
                    for pack in value.packs
                }
            )
            if changes != self.changes:
                self.storage.mark_stale()
            return self.cached(key, session)

    def known_pack(self, identity, revision=None):
        library, _ = self.library()
        found = next(
            (
                pack
                for pack in library.packs
                if pack_identity(pack, self.store.account_id) == identity
            ),
            None,
        )
        if found is None or (revision is not None and pack_revision(found) != revision):
            raise Refused("The installed sticker pack changed or is unavailable")
        return found

    def pack(self, key, session, identity, revision):
        self.allowed(key)
        self.cleanup()
        pack = self.known_pack(identity, revision)
        library, _ = self.library()
        row = self.storage.get("pack:" + identity)
        current = bool(row and row["revision"] == revision)
        value = CatalogPackData.model_validate(row["body"]) if current else None
        return CatalogPackView(
            pack=self.pack_value(pack),
            items=[
                self.item(
                    item,
                    key,
                    session,
                    premium=library.premium,
                    scope="pack:" + identity,
                )
                for item in value.items
            ]
            if value
            else [],
            updated_at=row["updated"] if row else None,
            truncated=value.truncated if value else False,
            error=row["error"]
            if current
            else "Sticker pack changed; refresh its contents"
            if row
            else None,
        )

    async def refresh_pack(self, key, session, identity, revision, *, guard=None):
        current_access = self.access_guard(key, guard)
        async with self.lock:
            current_access()
            pack = self.known_pack(identity, revision)
            changes = self.changes
            try:
                value = await self.transport.catalog_pack(pack)
            except asyncio.CancelledError:
                raise
            except Exception:
                current_access()
                self.known_pack(identity, revision)
                self.storage.failed(
                    "pack:" + identity, CatalogPackData(pack=pack, items=()), revision
                )
                return self.pack(key, session, identity, revision)
            current_access()
            self.known_pack(identity, revision)
            value = CatalogPackData.model_validate(value)
            if value.pack.id != pack.id or any(
                item.account_id != self.store.account_id for item in value.items
            ):
                raise Refused("The exact sticker pack is unavailable")
            library, _ = self.library()
            updated = library.model_copy(
                update={
                    "packs": tuple(
                        value.pack if candidate.id == pack.id else candidate
                        for candidate in library.packs
                    )
                }
            )
            self.storage.replace("library", updated, self.library_revision(updated))
            self.storage.put("pack:" + identity, value, pack_revision(value.pack))
            if changes != self.changes:
                self.storage.mark_stale()
            return self.pack(key, session, identity, pack_revision(value.pack))

    def open_search(self, key, session):
        access_revision = self.allowed(key)
        self.clear_transient(session, key)
        self.cleanup()
        epoch = uuid.uuid4().hex
        self.searches[session, key] = dict(
            epoch=epoch,
            request=-1,
            generation=None,
            query=None,
            next=None,
            expires=self.store.clock() + TICKET_TTL,
            access_revision=access_revision,
        )
        return GifSearchScope(epoch=epoch)

    def close_search(self, key, session, epoch):
        self.allowed(key)
        state = self.searches.get((session, key))
        if state and state["epoch"] != epoch:
            raise Conflict("The picker already changed")
        self.clear_transient(session, key)

    async def search(self, key, session, body, *, guard=None):
        current_access = self.access_guard(key, guard)
        self.cleanup()
        scope = (session, key)
        previous = self.searches.get(scope)
        if (
            not previous
            or previous["epoch"] != body.epoch
            or previous["access_revision"] != self.allowed(key)
        ):
            raise Refused("GIF search expired; reopen the picker")
        if body.request <= previous["request"]:
            raise Conflict("GIF search changed; use its current results")
        query_hash = digest(body.query)
        if body.offset and (
            previous["query"] != query_hash
            or previous["next"] != body.offset
            or not previous["generation"]
        ):
            raise Refused("GIF search page expired; search again")
        generation = previous["generation"] if body.offset else uuid.uuid4().hex
        state = dict(
            epoch=body.epoch,
            request=body.request,
            generation=generation,
            query=query_hash,
            next=None,
            expires=self.store.clock() + TICKET_TTL,
            access_revision=previous["access_revision"],
        )
        self.searches[scope] = state
        library, _ = self.library()
        if not body.query.strip():
            if body.offset:
                raise Refused("Saved GIFs have no search page")
            return GifSearchView(
                epoch=body.epoch,
                request=body.request,
                items=[
                    self.item(item, key, session, premium=library.premium)
                    for item in library.gifs
                ],
                next_offset=None,
                truncated=library.truncated,
            )
        try:
            result = await self.transport.catalog_search(body.query, body.offset)
        except asyncio.CancelledError:
            raise
        except Exception:
            current_access()
            raise Unavailable("GIF search is unavailable; try again") from None
        current_access()
        if self.searches.get(scope) is not state:
            raise Conflict("GIF search changed; discard the earlier results")
        result = CatalogSearchData.model_validate(result)
        state["next"] = result.next_offset
        return GifSearchView(
            epoch=body.epoch,
            request=body.request,
            items=[
                self.item(
                    item,
                    key,
                    session,
                    premium=library.premium,
                    scope=None,
                    generation=generation,
                )
                for item in result.items
            ],
            next_offset=result.next_offset,
            truncated=result.truncated,
        )

    def ticket(self, key, session, token, revision):
        access_revision = self.allowed(key)
        self.cleanup()
        ticket = self.tickets.get(token)
        if (
            not ticket
            or ticket.session != session
            or ticket.key != key
            or ticket.revision != revision
            or ticket.access_revision != access_revision
        ):
            raise Refused("This picker item changed or expired; select it again")
        if ticket.generation:
            state = self.searches.get((session, key))
            if not state or state["generation"] != ticket.generation:
                raise Refused("GIF search changed; select a current result")
        else:
            if ticket.scope == "library":
                library, _ = self.library()
                sources = library.gifs + library.recent + library.favorites
            else:
                identity = ticket.scope.removeprefix("pack:")
                pack = self.known_pack(identity)
                row = self.storage.get(ticket.scope)
                if not row or row["revision"] != pack_revision(pack):
                    raise Refused("Sticker pack changed; refresh its contents")
                sources = CatalogPackData.model_validate(row["body"]).items
            source = next(
                (
                    item
                    for item in sources
                    if source_identity(item) == source_identity(ticket.source)
                    and source_revision(item) == revision
                ),
                None,
            )
            if source is None:
                raise Refused("This picker item changed or is unavailable")
            ticket = Ticket(
                ticket.session,
                ticket.key,
                source,
                revision,
                ticket.expires,
                ticket.scope,
                ticket.generation,
                ticket.access_revision,
            )
        return ticket

    def resolve(self, key, session, token, revision):
        ticket = self.ticket(key, session, token, revision)
        source = ticket.source
        library, _ = self.library()
        if source.metadata.unavailable or (
            source.metadata.requires_premium and library.premium is not True
        ):
            raise Refused(source.metadata.unavailable or "Telegram Premium is required")
        return SOURCE.validate_python(source.model_dump(mode="json"))

    def preview(self, key, session, token, revision):
        ticket = self.ticket(key, session, token, revision)
        return self.preview_value(token, revision, ticket.source)

    async def entitlement(self):
        if not self.authorized():
            raise Refused("Account authorization changed")
        try:
            async with asyncio.timeout(ENTITLEMENT_TIMEOUT):
                value = await self.transport.catalog_entitlement()
        except TimeoutError:
            raise Unavailable("Account entitlement is unavailable; try again") from None
        if not self.authorized():
            raise Refused("Account authorization changed")
        return value is True

    async def refresh_same_document(self, source):
        if (
            not self.authorized()
            or not isinstance(source, NativeCatalogSource)
            or source.account_id != self.store.account_id
        ):
            raise Refused("Exact native media is unavailable for this account")
        fresh = await self.transport.catalog_refresh_document(source)
        if (
            not self.authorized()
            or fresh.account_id != source.account_id
            or fresh.document_id != source.document_id
            or fresh.metadata.kind != source.metadata.kind
        ):
            raise Refused("Exact native media changed or is unavailable")
        return SOURCE.validate_python(fresh.model_dump(mode="json"))
