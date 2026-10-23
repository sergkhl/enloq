"""Bounded local list/opening projections. No transport, media or model work."""

from pydantic import Field, computed_field

from ..domain.models import Chat, Message, Model
from ..storage.store import RETENTION
from ..storage.viewing import READY_SOURCE_IDS_SQL
from .notifications import MEDIA_LABELS


class ConversationPreview(Model):
    message_id: int | None = None
    date: float | None = None
    original: str = ""
    ai: str = ""
    # The newest message shown was deleted; its text is what it last said
    # (ADR 52 Q10).
    deleted: bool = False


class ConversationRow(Chat):
    @computed_field
    @property
    def key(self) -> str:
        return super().key

    activity: float
    processing: int = 0
    unseen: bool
    opening_source: int | None = None
    preview: ConversationPreview = Field(default_factory=ConversationPreview)


def compact(text):
    return " ".join(text.split())[:240]


def original_preview(message, *, group=False):
    # A deleted message is previewed by its captured content (ADR 52 Q10).
    text = compact(message.text)
    if message.media:
        label = MEDIA_LABELS.get(message.media, "Attachment")
        detail = text or (message.media_info and message.media_info.file_name) or ""
        text = f"{label}: {detail}" if detail else label
    if group:
        speaker = (
            "You"
            if message.author_peer == f"user:{message.account_id}"
            else message.author or "Unknown participant"
        )
        if message.author_signature:
            speaker += " · " + message.author_signature
        return compact(speaker + ": " + text)
    return compact(("You: " if message.outgoing else "") + text)


class ConversationPreviews:
    def __init__(self, store):
        self.store = store

    def unread(self, db, key, limit=100):
        """Ready unseen incoming messages, earliest first."""
        return [
            r[0]
            for r in db.execute(
                READY_SOURCE_IDS_SQL,
                (key, key, 0, self.store.clock() - RETENTION, limit),
            )
        ]

    def first(self, db, key):
        """The earliest cached position with ready unseen content: an incoming
        source or the exact target of a ready unseen reaction (ADR 26)."""
        targets = self.unread(db, key, 1)
        row = db.execute("SELECT body FROM chats WHERE key=?", (key,)).fetchone()
        if row and Chat.model_validate_json(row[0]).kind == "private":
            targets += [
                r["message_id"] for r in self.store.viewing.ready_reactions(db, key)
            ]
        return min(targets) if targets else None

    def opening(self, key):
        self.store.chat(key)
        with self.store.transaction() as db:
            return self.first(db, key)

    def opening_view(self, key):
        """The opening target and, read with it, the messages an Unread
        Separator captures on opening (ADR 28). Reactions place none."""
        self.store.chat(key)
        with self.store.transaction() as db:
            return self.first(db, key), self.unread(db, key)

    def recent(self, kind="private"):
        if kind == "saved":
            return self.saved()
        result = []
        for chat in self.store.recent_chats(kind, cached_access=kind == "group"):
            identity = chat.get("latest_message_id")
            latest = self.latest(chat["key"], identity) if identity else None
            with self.store.transaction() as db:
                first = self.first(db, chat["key"])
                preview = ConversationPreview(
                    message_id=identity, date=chat["activity"] or None
                )
                if latest:
                    message, capture = latest
                    shown = capture.message if capture else message
                    preview.message_id, preview.date = message.message_id, message.date
                    preview.deleted = capture is not None
                    preview.original = preview.ai = original_preview(
                        shown, group=kind == "group"
                    )
                    if (
                        kind == "private"
                        and not message.outgoing
                        and not message.deleted
                    ):
                        block = db.execute(
                            "SELECT * FROM blocks WHERE chat_key=? AND state='ready' "
                            "AND rendered_revision=revision AND EXISTS "
                            "(SELECT 1 FROM json_each(sources) WHERE value=?) "
                            "ORDER BY date DESC,id DESC LIMIT 1",
                            (chat["key"], message.message_id),
                        ).fetchone()
                        if block and self.store.viewing.rewrite(db, block):
                            preview.ai = compact(block["rendered"])
                result.append(
                    ConversationRow(
                        **{k: v for k, v in chat.items() if k != "key"},
                        unseen=first is not None,
                        opening_source=first,
                        preview=preview,
                    )
                )
        return result

    def latest(self, key, identity=None):
        """The newest message a conversation shows, from its newest native
        message down: a deletion that is hidden or left out gives way to the
        message before it (ADR 52 Q10). With its capture when deleted. An
        uncached newest message shows nothing rather than older history."""
        for row in self.store.rows(
            "SELECT body FROM messages WHERE chat_key=? AND date>=? "
            "AND (? IS NULL OR message_id<=?) ORDER BY message_id DESC LIMIT 20",
            (key, self.store.clock() - RETENTION, identity, identity),
        ):
            message = Message.model_validate_json(row["body"])
            if identity is not None and message.message_id != identity:
                return None
            identity = None
            if message.service:
                continue
            if not message.deleted:
                return message, None
            shown = self.store.history.shown([message])
            if shown:
                return shown[0]
        return None

    def saved(self):
        """The account's own Saved Messages as its third list's one row (ADR
        50): always there, never unseen, its preview without "You:"."""
        self.store.ensure_saved()
        result = []
        for chat in self.store.saved_chat():
            preview = ConversationPreview(date=chat["activity"] or None)
            # Deleted notes leave Saved Messages (ADR 52 Q9).
            latest = self.latest(chat["key"])
            if latest:
                message = latest[0]
                preview.message_id, preview.date = message.message_id, message.date
                preview.original = preview.ai = original_preview(
                    message.model_copy(update={"outgoing": False})
                )
            result.append(
                ConversationRow(
                    **{k: v for k, v in chat.items() if k != "key"},
                    unseen=False,
                    preview=preview,
                )
            )
        return result

    def window(self, key, around):
        """At most 25 predecessors and 75 current/successors, all already cached."""
        self.store.chat(key)
        with self.store.transaction() as db:
            before = db.execute(
                "SELECT body FROM messages WHERE chat_key=? AND message_id<? AND date>=? "
                "ORDER BY message_id DESC LIMIT 25",
                (key, around, self.store.clock() - RETENTION),
            ).fetchall()
            after = db.execute(
                "SELECT body FROM messages WHERE chat_key=? AND message_id>=? AND date>=? "
                "ORDER BY message_id LIMIT 75",
                (key, around, self.store.clock() - RETENTION),
            ).fetchall()
        return self.store.history.annotate(
            [Message.model_validate_json(r[0]) for r in [*reversed(before), *after]]
        )
