"""Reading and change intake; opening a conversation never starts inference."""

import asyncio
import json

from ..domain.models import (
    Conflict,
    GroupProcessing,
    Message,
    ProcessingPaused,
    Refused,
)
from ..inference.fake import FakeDecider, FakeEngine
from ..storage.store import RETENTION, WINDOW
from .assessment import Assessment
from .attachments import Attachments
from .avatars import Avatars
from .blocks import progress, rebuild
from .calming import Calming
from .catalog import MediaCatalog
from .catalog_assets import CatalogAssets
from .content_readiness import UnseenReadiness
from .deletion import Deletion
from .delivery import Delivery, operation_view
from .emotion import Emotion
from .forwarding import Forwarding
from .group_briefings import GroupBriefings
from .group_updates import GroupUpdates
from .media import Media
from .memory import Memory
from .new_message import NewMessage
from .notifications import IncomingNotifications
from .previews import ConversationPreviews
from .profiles import Profiles
from .reactions import Reactions
from .reminders import Reminders
from .replies import Replies
from .retained_groups import RetainedGroups
from .rewriting import Rewriting
from .selected_media import SelectedMedia
from .suggestions import Suggestions
from .sync import Sync, coverage, covered
from .traits import Traits
from .transcripts import Transcripts


class Conversations:
    def __init__(self, store, transport, engine=None, decider=None, calmer=None):
        self.store, self.transport, self.engine = store, transport, engine
        self.status = "Offline demo" if engine is None else "Connecting"
        self.engine = engine or FakeEngine()
        # ADR 30: Emotion Classification has its own exact route.
        self.decider = decider or FakeDecider()
        # ADR 53: calmed rewrites have their own route, Muse; without one,
        # the rewrite engine calms too.
        self.calmer = calmer or self.engine
        self.admit_ai = lambda: None
        self.account_processing_enabled = lambda: True
        self.authorized = lambda: True
        self.group_runtime_admit = self.admit_group_runtime
        self.catalog = MediaCatalog(
            store, transport, authorized=lambda: self.authorized()
        )
        self.rewriting = Rewriting(self)
        self.memory = Memory(self)
        self.traits = Traits(self)
        self.emotion = Emotion(self)
        self.calming = Calming(self)
        self.suggestions = Suggestions(self)
        self.reminders = Reminders(self)
        self.delivery = Delivery(store, transport)
        self.delivery.authorized = lambda: self.authorized()
        self.forwarding = Forwarding(store, transport)
        self.media = Media(store, transport)
        self.catalog_assets = CatalogAssets(self.catalog, self.media.cache, transport)
        self.selected_media = SelectedMedia(self)
        self.delivery.selected_media = self.selected_media
        self.media.retained_preview = self.selected_media.message_file
        self.attachments = Attachments(store)
        self.avatars = Avatars(store, transport)
        self.sync = Sync(store, transport, changed=self.changed)
        self.sync.arrived = self.arrived
        self.notifications = IncomingNotifications(self)
        self.unseen = UnseenReadiness(self)
        self.previews = ConversationPreviews(store)
        self.retained = RetainedGroups(self)
        self.reactions = Reactions(self)
        self.profiles = Profiles(self)
        self.assessment = Assessment(self)
        self.deletion = Deletion(self)
        self.briefings = GroupBriefings(self)
        self.group_changed = self.briefings.changed
        self.store.groups.on_access_change = self.group_access_changed
        self.sync.new_incoming = self.notifications.admit
        self.replies = Replies(self)
        self.transcripts = Transcripts(self)
        self.new_message = NewMessage(self)
        # Background reads owned by this runtime; draining cancels them.
        self.tasks = set()
        self.refreshed = set()

    def admit_group_runtime(self):
        if not self.authorized():
            raise Refused("This group account is unavailable")
        if not self.transport.connected:
            raise ProcessingPaused("Waiting for this account's Telegram connection")

    def group_access_changed(self, state):
        self.delivery.revalidate_group(state.key)
        self.briefings.reconcile(state.key)

    def changed(self, message, *, target=True, arrival=True):
        kind = self.store.chat(f"{message.account_id}/{message.peer}").kind
        if kind == "group":
            self.group_changed(message, arrival=arrival)
            return
        if kind == "saved":
            # Saved Messages takes no AI work for now (ADR 50 Q3): no blocks,
            # memory or Reaction Assessment, only the drafts' revalidation.
            return
        # Older bytes belong only to reading history. Current derivatives and
        # cited memory still invalidate, and history cannot trigger downloads.
        self.memory.forget(message)
        self.assessment.evidence_changed(message)
        rebuild(
            self.store,
            f"{message.account_id}/{message.peer}",
            message,
            target=target,
            klass="new" if arrival else "catchup",
        )

    def arrived(self, message):
        if self.store.chat(f"{message.account_id}/{message.peer}").kind != "private":
            return
        # The owner's own voice is transcribed too (ADR 51 Q8).
        self.transcripts.own(message)
        if (
            not message.outgoing
            and not message.deleted
            and message.date >= self.store.clock() - WINDOW
        ):
            self.run(self.media.prefetch(message))
            if (
                message.forwarded
                and not message.forwarded_from
                and not message.forward_read
                and self.admitted(message)
            ):
                self.run(self.forwarding.intake(message))

    def admitted(self, message):
        """Whether new incoming work waits for this message (ADR 27): only
        processing earns an intake read; display uses the open-time repair."""
        try:
            self.admit_ai()
        except ProcessingPaused:
            return False
        return bool(
            self.store.rows(
                "SELECT 1 FROM jobs WHERE role='incoming_rewrite' AND state='queued' AND scope IN (SELECT id FROM blocks WHERE chat_key=? AND EXISTS (SELECT 1 FROM json_each(blocks.sources) WHERE value=?))",
                (f"{message.account_id}/{message.peer}", message.message_id),
            )
        )

    async def process_due(self):
        await self.rewriting.process_due()
        await self.briefings.process_due()

    def conversation(self, key):
        if key == self.store.saved_key:
            self.store.ensure_saved()
        chat = self.store.chat(key)
        if chat.kind == "saved":
            return self.saved(key, chat)
        if chat.kind not in {"private", "group"}:
            raise Refused(
                "Only existing private conversations and groups are supported"
            )
        group = chat.kind == "group"
        if group:
            self.retained.cache_read(key)
        messages = self.store.messages(key)
        if group:
            messages = [
                message
                for message in messages
                if message.date >= self.store.clock() - RETENTION
            ]
        opening, unread = self.previews.opening_view(key)
        return dict(
            chat={**chat.model_dump(), "key": key},
            timezone=self.store.timezone,
            opening_source=opening,
            unread_sources=unread,
            emotion=None if group else self.emotion.view(key).model_dump(),
            suggestions=None if group else self.suggestions.view(key).model_dump(),
            reminder_occurrence=None if group else self.reminders.identity(key),
            # ADR 53: the profile's switch, kept with the app's snapshot.
            calming=None if group else self.calming.on(key),
            group_processing=self.group_processing(key).model_dump() if group else None,
            retained=group
            and not any(
                row["key"] == key
                for row in self.store.recent_chats("group", cached_access=True)
            ),
            messages=self.briefings.annotate(messages)
            if group
            else self.store.history.annotate(messages),
            draft=self.store.draft(key),
            operations=[
                operation_view(o, self.store.clock())
                for o in self.store.send_operations.recent(key)
            ],
            deletions=self.deletion.recent(key),
            reply_holds=self.store.deletion_operations.reply_holds(key),
            blocks=[] if group else self.stages(key, viewing=True),
            briefings=self.briefings.view(key) if group else [],
            briefing_update=GroupUpdates(self.briefings).view(key) if group else None,
            progress=None if group else progress(self.store, key),
            older={
                "available": 0 if group else len(self.rewriting.older(key)),
                "notice": None if group else self.rewriting.older_notice(key),
            },
        )

    def saved(self, key, chat):
        """The account's own Saved Messages (ADR 50): its originals, drafts and
        sends, with no AI, unseen messages or briefings."""
        return dict(
            chat={**chat.model_dump(), "key": key},
            timezone=self.store.timezone,
            opening_source=None,
            unread_sources=[],
            emotion=None,
            suggestions=None,
            calming=None,
            group_processing=None,
            retained=False,
            messages=self.store.history.annotate(self.store.messages(key)),
            draft=self.store.draft(key),
            operations=[
                operation_view(o, self.store.clock())
                for o in self.store.send_operations.recent(key)
            ],
            deletions=self.deletion.recent(key),
            reply_holds=self.store.deletion_operations.reply_holds(key),
            blocks=[],
            briefings=[],
            briefing_update=None,
            progress=None,
            older={"available": 0, "notice": None},
        )

    def group_processing(self, key):
        state = self.store.groups.get(key)
        master = self.account_processing_enabled()
        effective = bool(
            master
            and state.processing_enabled
            and state.supported
            and state.access == "available"
        )
        return GroupProcessing(
            processing_enabled=state.processing_enabled,
            account_processing_enabled=master,
            effective=effective,
            access=state.access,
            supported=state.supported,
            reason=(
                state.reason
                if not state.supported or state.access != "available"
                else "account_off"
                if not master
                else "group_off"
                if not state.processing_enabled
                else "ready"
            ),
        )

    def set_group_processing(self, key, enabled):
        if enabled and not any(
            row["key"] == key
            for row in self.store.recent_chats("group", cached_access=True)
        ):
            raise Refused("Inactive retained work cannot enable group processing")
        self.store.groups.set_processing_enabled(key, enabled)
        self.briefings.reconcile(key)
        return self.group_processing(key)

    def stages(self, key, *, viewing=False):
        """Blocks with their visible Processing Stage."""
        try:
            self.admit_ai()
            paused = False
        except ProcessingPaused:
            paused = True
        due = {
            r["scope"]: r["due"]
            for r in self.store.rows(
                "SELECT scope,due FROM jobs WHERE role='incoming_rewrite' AND state='queued' AND scope IN (SELECT id FROM blocks WHERE chat_key=?)",
                (key,),
            )
        }
        held = "incoming_rewrite" in self.rewriting.paused_roles()
        # ADR 53 Q7: held blocks whose calming decision is not yet made.
        deciding = self.calming.deciding(key)
        blocks = []
        for r in self.store.rows(
            "SELECT * FROM blocks WHERE chat_key=? ORDER BY date,id", (key,)
        ):
            state, error = r["state"], r["error"] or ""
            if state == "queued":
                stage = (
                    "paused"
                    if paused
                    else "format_paused"
                    if held
                    else "budget"
                    if "budget" in error.lower()
                    else "retrying"
                    if error.startswith("Provider busy")
                    else "quiet"
                    if due.get(r["id"], 0) > self.store.clock()
                    else "queued"
                )
            else:
                stage = {
                    "working": r["stage"] or "preparing",
                    "ready": "done",
                    "nothing": "nothing",
                    "failed": "failed",
                    "unknown": "failed",
                }.get(state)
            viewed = None
            if viewing:
                with self.store.transaction() as db:
                    viewed = self.store.viewing.rewrite(db, r)
            blocks.append(
                {
                    **r,
                    "viewed_content": viewed.model_dump() if viewed else None,
                    "stream": self.rewriting.partial(r),
                    "sources": json.loads(r["sources"]),
                    "source_revisions": json.loads(r["source_revisions"])
                    if r["source_revisions"]
                    else None,
                    "unanalysed": json.loads(r["unanalysed"] or "[]"),
                    "stage": stage,
                    "calmed": bool(r["calmed"]),
                    "deciding": r["id"] in deciding,
                }
            )
        return blocks

    def source_page(self, key, anchor, revision, *, cursor=0, limit=100):
        """One bounded page of a group's existing local sources, in native order.

        The revision binds the cursor to the displayed membership. Missing or
        expired cache rows retain their source slots and never trigger a read
        from Telegram. Native metadata, history and viewing tokens come from
        the same transaction as the block, so pages can be safely combined.
        """
        chat = self.store.chat(key)
        if chat.kind != "private":
            raise Refused("Only private conversations are supported")
        if not 1 <= limit <= 100 or cursor < 0:
            raise Refused("Invalid group source page")
        identity = f"{key}/{anchor}"
        cutoff = self.store.clock() - RETENTION
        with self.store.transaction() as db:
            block = db.execute(
                "SELECT id,revision,sources,source_revisions FROM blocks WHERE chat_key=? AND id=?",
                (key, identity),
            ).fetchone()
            if (
                not block
                or block["revision"] != revision
                or not block["source_revisions"]
            ):
                raise Conflict("Message group changed; reload its current sources")
            sources = json.loads(block["sources"])
            snapshots = json.loads(block["source_revisions"])
            # Intake commits before rebuilding its block. The stored native
            # revisions reject that gap even when the changed source lies on
            # another page; only metadata is checked across the entire group.
            changed = db.execute(
                "SELECT 1 FROM json_each(?) v JOIN messages m "
                "ON m.chat_key=? AND m.message_id=CAST(v.key AS INTEGER) "
                "WHERE m.revision!=v.value OR m.deleted!=0 LIMIT 1",
                (block["source_revisions"], key),
            ).fetchone()
            if changed or set(snapshots) != {str(source) for source in sources}:
                raise Conflict("Message group changed; reload its current sources")
            if cursor > len(sources):
                raise Refused("Invalid group source cursor")
            page = sources[cursor : cursor + limit]
            rows = db.execute(
                "SELECT m.body,m.message_id,(SELECT count(*) FROM message_versions v "
                "WHERE v.source=m.key AND v.date>=?) AS history_count "
                "FROM messages m WHERE m.chat_key=? AND m.date>=? "
                "AND m.message_id IN (SELECT value FROM json_each(?)) "
                "ORDER BY m.message_id",
                (cutoff, key, cutoff, json.dumps(page)),
            ).fetchall()
            messages = []
            found = set()
            for row in rows:
                message = Message.model_validate_json(row["body"])
                if message.key != f"{key}/{row['message_id']}":
                    raise Refused("Group source identity mismatch")
                viewed = self.store.viewing.original(message)
                messages.append(
                    dict(
                        message.model_dump(),
                        viewed_content=viewed.model_dump() if viewed else None,
                        history_count=row["history_count"],
                        reactions=self.store.reactions.view(message.key, db),
                    )
                )
                found.add(message.message_id)
            end = cursor + len(page)
            return dict(
                block_id=identity,
                revision=block["revision"],
                sources=page,
                messages=messages,
                missing=[source for source in page if source not in found],
                total=len(sources),
                next_cursor=end if end < len(sources) else None,
                complete=end == len(sources),
            )

    async def history(self, key, before=0):
        """Older originals: the local cache first, Telegram only past it."""
        chat = self.store.chat(key)
        if chat.kind == "group":
            self.retained.cache_read(key)
            # Group reading uses retained native evidence, never an implicit
            # backfill beyond the independently bounded recent intake.
            return [
                Message.model_validate_json(r["body"]).model_dump()
                for r in self.store.rows(
                    "SELECT body FROM messages WHERE chat_key=? AND date>=? "
                    "AND (?=0 OR message_id<?) ORDER BY message_id DESC LIMIT 50",
                    (key, self.store.clock() - RETENTION, before, before),
                )
            ]
        if chat.kind not in {"private", "saved"}:
            raise Refused("Only private conversations are supported")
        floor = coverage(self.store, key)
        if floor is not None and before > floor:
            local = [
                Message.model_validate_json(r["body"])
                for r in self.store.rows(
                    "SELECT body FROM messages WHERE chat_key=? AND message_id>=? AND message_id<? ORDER BY message_id DESC LIMIT 50",
                    (key, floor, before),
                )
            ]
            if len(local) == 50:
                return [m.model_dump() for m in local]
        page = await self.transport.history(chat.peer, before, 50)
        if any(
            m.account_id != self.store.account_id or m.peer != chat.peer for m in page
        ):
            raise Refused("History source identity mismatch")
        stored = []
        for message in page:
            if message.date >= self.store.clock() - 30 * 86400:
                self.sync.ingest(message, target=False)
                stored.append(message.message_id)
        # Read the floor again: a gap restart during the Telegram read clears it,
        # and a cleared floor must not be recreated from this page alone.
        floor = coverage(self.store, key)
        if floor is not None and before >= floor and stored:
            # This page continues below the covered range without a gap.
            covered(self.store, key, min(stored))
        return [m.model_dump() for m in page]

    async def direct(self, key, identity):
        chat = self.store.chat(key)
        if chat.kind == "group":
            self.retained.cache_read(key)
        found = self.store.message(key, identity)
        if found:
            return (
                found
                if chat.kind != "group" or found.date >= self.store.clock() - RETENTION
                else None
            )
        if chat.kind == "group":
            return None
        found = await self.transport.exact(chat.peer, identity)
        if found and (
            found.account_id != self.store.account_id
            or found.peer != chat.peer
            or found.message_id != identity
        ):
            raise Refused("Reply source identity mismatch")
        if found and found.date >= self.store.clock() - 30 * 86400:
            self.sync.ingest(found, target=False)
            found = self.store.message(key, identity)
        return found

    async def refresh_legacy(self, key, *, through=None, limit=30):
        """Re-read untyped legacy rows by exact ID. A kind/metadata refresh is
        not a content change; an unreachable row stays an unanalysed item."""
        chat = self.store.chat(key)
        identities = [
            r["message_id"]
            for r in self.store.rows(
                "SELECT message_id,key FROM messages WHERE chat_key=? AND deleted=0 AND json_extract(body,'$.media')='unsupported' AND message_id<=? ORDER BY message_id DESC LIMIT ?",
                (key, through or 2**62, limit),
            )
            if r["key"] not in self.refreshed
        ]
        if not identities or not hasattr(self.transport, "exact_many"):
            return 0
        try:
            async with asyncio.timeout(60):
                page = await self.transport.exact_many(chat.peer, identities)
        except asyncio.CancelledError:
            raise
        except Exception:
            return 0
        # One successful exact read per row and process; missing rows stay legacy.
        self.refreshed.update(f"{key}/{i}" for i in identities)
        count = 0
        for message in page:
            if (
                message.account_id == self.store.account_id
                and message.peer == chat.peer
                and message.message_id in identities
            ):
                self.sync.ingest(message, target=False)
                count += 1
        return count

    def schedule_refresh(self, key):
        if self.store.chat(key).kind == "private":
            self.run(self.refresh_legacy(key))

    def run(self, work):
        """A background read owned by this runtime; draining cancels it."""
        try:
            task = asyncio.get_running_loop().create_task(work)
        except RuntimeError:
            work.close()  # No loop: a synchronous caller such as a fixture seed.
            return
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def active(self, key):
        return any(c["key"] == key for c in self.store.recent_chats())
