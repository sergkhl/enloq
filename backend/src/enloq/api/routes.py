import json
import re
from datetime import datetime
from typing import Literal
from urllib.parse import quote
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import Field, PositiveInt, StrictBool
from starlette.background import BackgroundTask

from ..application.delivery import operation_view
from ..application.media import DOWNLOAD_BYTES, IMAGE_BYTES, voice_note
from ..application.memory import ACCOUNT
from ..application.previews import ConversationRow
from ..domain.catalog_assets import representation
from ..domain.group_questions import MAX_QUESTION, AskView
from ..domain.models import (
    SPOKEN_MEDIA,
    ChatKey,
    GroupProcessing,
    Message,
    Model,
    Refused,
    Unavailable,
)
from ..domain.selected_media import sticker_selected
from ..domain.selection_formats import STICKER_BYTES
from ..domain.suggestions import Style, SuggestionsView
from ..domain.video import video_candidate
from ..storage.store import RETENTION
from ..storage.viewing import ViewedSources
from ..transport.mtproto import DOWNLOAD, PLAYBACK
from .memory import AccountMemoryView, MemoryView, Remembering
from .scope import MEMBER_ACCOUNT
from .traits import ScaleEdit, TraitsView

SPOKEN_EXTENSIONS = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp4": ".m4a"}


def download_name(message, timezone="UTC"):
    """Voice notes and circles are named by kind and the message's local date
    and time, like Telegram Desktop (ADR 27); other files keep their name."""
    info = message.media_info
    if message.media == "video_note" or (
        message.media == "audio" and (info is None or info.voice)
    ):
        stamp = datetime.fromtimestamp(message.date, ZoneInfo(timezone)).strftime(
            "%Y-%m-%d_%H-%M-%S"
        )
        if message.media == "video_note":
            return f"video_{stamp}.mp4"
        return f"audio_{stamp}{SPOKEN_EXTENSIONS.get(info and info.mime, '.ogg')}"
    return (info and info.file_name) or f"telegram-{message.media}-{message.message_id}"


class ImageUpload(Model):
    revision: int = Field(ge=0)
    name: str = Field(max_length=256)
    data: str = Field(max_length=7_000_000)


class Revision(Model):
    revision: int = Field(ge=0)


class GroupProcessingEdit(Model):
    enabled: StrictBool


class Calming(Model):
    """A private chat's calming (ADR 53): on unless the chat chose otherwise."""

    enabled: bool


class CalmingEdit(Model):
    enabled: StrictBool


class VideoPreparation(Revision):
    version: int | None = Field(default=None, gt=0)


class PreparedVideo(Model):
    state: Literal["ready", "unsupported"]
    revision: int = Field(ge=0)
    version: int | None = Field(default=None, gt=0)
    mime: Literal["video/mp4", "video/webm"] | None


class Person(Model):
    """Someone New Message may write to (ADR 51 Q16)."""

    peer: str
    name: str
    username: str | None = None
    photo_id: str | None = None
    # An existing private chat, of any age.
    chat: bool = False


class NewMessagePeople(Model):
    chats: list[Person]
    contacts: list[Person]
    # Telegram's own people search, for three or more typed characters.
    search: list[Person]


class Transcript(Model):
    text: str
    language: str | None = None
    duration_seconds: float | None = None


class TranscriptStatus(Model):
    """A voice note's transcript, or where its transcription stands (ADR 51
    Q9): none yet, running, done or failed with a reason."""

    state: Literal["none", "running", "done", "failed"]
    transcript: Transcript | None
    reason: str | None


class ForwardingRead(Model):
    identities: list[PositiveInt] = Field(min_length=1, max_length=100)


class CachedOriginalsRead(Model):
    identities: list[PositiveInt] = Field(min_length=1, max_length=100)


class HiddenMessages(Model):
    """Deleted messages to hide for good (ADR 52 Q11)."""

    identities: list[PositiveInt] = Field(min_length=1, max_length=100)


class RepliesRead(Model):
    identities: list[PositiveInt] = Field(min_length=1, max_length=100)


class BlockRetry(Model):
    revision: str = Field(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$")


class UndoRewrite(Revision):
    attempt: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")


class MemoryEdit(Model):
    revision: int = Field(ge=0)
    text: str = Field(min_length=1, max_length=300)


class DraftEdit(Model):
    revision: int = Field(ge=0)
    text: str = Field(max_length=65536)
    reply_to: int | None = Field(default=None, gt=0)


class CorrespondentProfile(Model):
    """What a profile shows beyond the cached chat (ADR 46 Q11)."""

    name: str
    username: str | None = None
    phone: str | None = None
    bio: str | None = None


class MemberProfile(CorrespondentProfile):
    """A group member's profile (ADR 51 Q14), with their current photo."""

    photo_id: str | None = None


class GroupProfile(Model):
    """What a Group Profile shows beyond the cached chat (ADR 47 Q7)."""

    title: str
    username: str | None = None
    members: int | None = None
    description: str | None = None


class ReactionChoice(Model):
    emoji: str = Field(min_length=1, max_length=32)
    selected: bool


class SuggestionInsert(Revision):
    attempt: str = Field(pattern=r"^[a-f0-9]{32}$")
    index: int = Field(ge=0, lt=3)
    # The browser caret in UTF-16 code units; None inserts at the end.
    cursor: int | None = Field(default=None, ge=0, le=65536)


class SuggestionSettings(Model):
    automatic: StrictBool
    style: Style


class SuggestionGenerate(Revision):
    # Earlier clients still send their caret; a manual option is a complete
    # message replacing the draft (ADR 25 Q8), so none is used.
    cursor: int | None = Field(default=None, ge=0, le=65536)


class Question(Model):
    question: str = Field(min_length=1, max_length=MAX_QUESTION)


class SuggestionFold(Model):
    # ADR 56 Q12 B: the shown set's attempt, folded or unfolded everywhere.
    attempt: str = Field(pattern=r"^[a-f0-9]{32}$")
    folded: StrictBool


def routes(manager, settings):
    router = APIRouter(prefix="/api/accounts/{account_id}", dependencies=MEMBER_ACCOUNT)

    def key(account_id, peer):
        try:
            return ChatKey(account_id=account_id, peer=peer).key
        except ValueError:
            raise Refused("A valid conversation is required") from None

    def media_access(request, app, message, *, version=None, access_revision=None):
        """Recheck access after a wait, before returning even a validator."""
        auth = request.app.state.browser_auth
        if not auth.active(request.state.local_session):
            raise HTTPException(
                status_code=401,
                detail="Connect this browser from the Mac launcher",
                headers={"Clear-Site-Data": '"cache"'},
            )
        if manager.get(message.account_id) is not app or not app.media.authorized():
            raise Refused("Account authorization changed")
        chat = key(message.account_id, message.peer)
        if app.store.chat(chat).kind == "group":
            app.retained.cache_read(chat)
        if app.store.chat(chat).kind not in {"private", "group", "saved"}:
            raise Refused(
                "Only existing private conversations and groups are supported"
            )
        current = (
            app.store.history.version(chat, message.message_id, version)
            if version
            else app.store.message(chat, message.message_id)
        )
        if (
            not current
            or current.deleted
            or current.revision != message.revision
            or current.date < app.store.clock() - RETENTION
        ):
            raise Refused("Media source changed or expired; refresh its original")
        fresh = app.media.source_access(
            message,
            history=bool(version),
            variant=request.query_params.get("variant", "file"),
        )
        if access_revision is not None and fresh != access_revision:
            raise Refused("Native group access changed during media loading")
        return fresh

    @router.get("/avatars/{peer}")
    async def avatar(
        account_id: int,
        peer: str,
        photo_id: str = Query(min_length=1, max_length=32, pattern=r"^-?\d+$"),
    ):
        key(account_id, peer)
        async with manager.lease(account_id) as app:
            data, mime = await app.avatars.get(peer, photo_id)
            return Response(data, media_type=mime)

    @router.get("/chats", response_model=list[ConversationRow])
    def chats(account_id: int, kind: Literal["private", "group", "saved"] = "private"):
        return manager.get(account_id).previews.recent(kind)

    @router.get("/retained-groups")
    def retained_groups(account_id: int):
        return manager.get(account_id).retained.entries()

    @router.get("/chats/{peer}/processing", response_model=GroupProcessing)
    def group_processing(account_id: int, peer: str):
        return manager.get(account_id).group_processing(key(account_id, peer))

    @router.post("/chats/{peer}/processing", response_model=GroupProcessing)
    def set_group_processing(account_id: int, peer: str, body: GroupProcessingEdit):
        return manager.get(account_id).set_group_processing(
            key(account_id, peer), body.enabled
        )

    @router.get("/chats/{peer}/calming", response_model=Calming)
    def calming(account_id: int, peer: str):
        return manager.get(account_id).calming.view(key(account_id, peer))

    @router.post("/chats/{peer}/calming", response_model=Calming)
    def set_calming(account_id: int, peer: str, body: CalmingEdit):
        return manager.get(account_id).calming.set(key(account_id, peer), body.enabled)

    @router.get("/chats/{peer}/window")
    def cached_window(account_id: int, peer: str, around: int = Query(gt=0, lt=2**63)):
        app = manager.get(account_id)
        chat = key(account_id, peer)
        if app.store.chat(chat).kind == "group":
            app.retained.cache_read(chat)
        return app.previews.window(chat, around)

    @router.get("/chats/{peer}/briefings/{day}/sources")
    def briefing_sources(
        account_id: int,
        peer: str,
        day: str,
        result: str = Query(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$"),
        source: int = Query(gt=0, lt=2**63),
    ):
        app = manager.get(account_id)
        chat = key(account_id, peer)
        app.retained.cache_read(chat)
        return app.briefings.sources(chat, day, result, source)

    @router.post("/chats/{peer}/briefings/{attempt}/retry")
    def retry_briefing(
        account_id: int,
        peer: str,
        attempt: str = Path(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$"),
    ):
        return {
            "attempt": manager.get(account_id).briefings.retry_for(
                key(account_id, peer), attempt
            )
        }

    @router.get("/chats/{peer}/briefings/update")
    def briefing_update_state(account_id: int, peer: str):
        from ..application.group_updates import GroupUpdates

        return GroupUpdates(manager.get(account_id).briefings).view(
            key(account_id, peer)
        )

    @router.post("/chats/{peer}/briefings/update")
    def update_briefing(account_id: int, peer: str):
        from ..application.group_updates import GroupUpdates

        return GroupUpdates(manager.get(account_id).briefings).update(
            key(account_id, peer)
        )

    @router.get("/chats/{peer}/briefings/{day}/originals")
    def briefing_originals(
        account_id: int,
        peer: str,
        day: str,
        result: str = Query(min_length=32, max_length=32, pattern=r"^[a-f0-9]{32}$"),
        around: int | None = Query(default=None, gt=0, lt=2**63),
    ):
        app = manager.get(account_id)
        chat = key(account_id, peer)
        app.retained.cache_read(chat)
        return app.briefings.originals(chat, day, result, around=around)

    @router.get("/chats/{peer}")
    async def conversation(account_id: int, peer: str):
        app = manager.get(account_id)
        value = app.conversation(key(account_id, peer))
        # Legacy untyped rows learn their kind by exact ID, off the read path.
        app.schedule_refresh(key(account_id, peer))
        return value

    @router.post("/chats/{peer}/draft")
    def draft(account_id: int, peer: str, body: DraftEdit):
        def change(value):
            if sticker_selected(value):
                if body.text != value["text"]:
                    raise Refused("Remove the sticker before editing the kept draft")
                value["reply_to"] = body.reply_to
                return
            value.update(
                text=body.text,
                reply_to=body.reply_to,
                undo=None,
                state="idle",
                attempt=None,
                error=None,
            )
            # A draft that took a reminder set's option keeps that mark through
            # edits; clearing it drops the mark (ADR 58 Q3).
            if not body.text.strip():
                value.pop("reminders", None)

        return manager.get(account_id).store.mutate_draft(
            key(account_id, peer),
            body.revision,
            change,
        )

    @router.post("/chats/{peer}/suggestions/insert")
    def insert_suggestion(account_id: int, peer: str, body: SuggestionInsert):
        return manager.get(account_id).suggestions.insert(
            key(account_id, peer), body.revision, body.attempt, body.index, body.cursor
        )

    @router.post("/chats/{peer}/suggestions/generate", response_model=SuggestionsView)
    def generate_suggestions(account_id: int, peer: str, body: SuggestionGenerate):
        return manager.get(account_id).suggestions.generate(
            key(account_id, peer), body.revision
        )

    @router.post("/chats/{peer}/ask", response_model=AskView)
    async def ask_group(account_id: int, peer: str, body: Question):
        """Ask this group (ADR 59): an answer with checked citations, a range
        searched without an answer, or unchecked. Nothing is stored."""
        async with manager.lease(account_id) as app:
            return await app.questions.ask(key(account_id, peer), body.question)

    @router.get("/chats/{peer}/suggestions/settings", response_model=SuggestionSettings)
    def suggestion_settings(account_id: int, peer: str):
        return manager.get(account_id).suggestions.settings(key(account_id, peer))

    @router.post(
        "/chats/{peer}/suggestions/settings", response_model=SuggestionSettings
    )
    def set_suggestion_settings(account_id: int, peer: str, body: SuggestionSettings):
        return manager.get(account_id).suggestions.set_settings(
            key(account_id, peer), body.automatic, body.style
        )

    @router.post("/chats/{peer}/suggestions/fold", response_model=SuggestionsView)
    def fold_suggestions(account_id: int, peer: str, body: SuggestionFold):
        return manager.get(account_id).suggestions.fold(
            key(account_id, peer), body.attempt, body.folded
        )

    @router.post("/chats/{peer}/viewed")
    def viewed(account_id: int, peer: str, body: ViewedSources):
        app = manager.get(account_id)
        chat = key(account_id, peer)
        if app.store.chat(chat).kind == "group":
            app.retained.cache_read(chat)
        return app.store.viewing.acknowledge(chat, body)

    @router.get("/chats/{peer}/ready-sources")
    def ready_sources(
        account_id: int,
        peer: str,
        after: int = Query(default=0, ge=0, lt=2**63),
        limit: int = Query(default=100, ge=1, le=100),
    ):
        app = manager.get(account_id)
        chat = key(account_id, peer)
        if app.store.chat(chat).kind == "group":
            app.retained.cache_read(chat)
        return app.store.viewing.ready_sources(chat, after=after, limit=limit)

    @router.get("/chats/{peer}/history")
    async def history(account_id: int, peer: str, before: int = Query(default=0, ge=0)):
        async with manager.lease(account_id) as app:
            return app.store.history.annotate(
                await app.history(key(account_id, peer), before)
            )

    @router.post("/chats/{peer}/originals/forwarding")
    async def forwarding(account_id: int, peer: str, body: ForwardingRead):
        async with manager.lease(account_id) as app:
            chat = key(account_id, peer)
            if app.store.chat(chat).kind == "group":
                app.retained.cache_read(chat)
            return await app.forwarding.refresh(chat, body.identities)

    @router.post("/chats/{peer}/originals/replies")
    async def replies(account_id: int, peer: str, body: RepliesRead):
        """Replied-to messages for Reply Quotes (ADR 41)."""
        async with manager.lease(account_id) as app:
            chat = key(account_id, peer)
            if app.store.chat(chat).kind == "group":
                app.retained.cache_read(chat)
            return await app.replies.resolve(chat, body.identities)

    @router.get("/chats/{peer}/profile", response_model=CorrespondentProfile)
    async def profile(account_id: int, peer: str):
        """A private correspondent's profile, read now and kept nowhere."""
        async with manager.lease(account_id) as app:
            return await app.profiles.read(key(account_id, peer))

    @router.get("/chats/{peer}/members/{member}/profile", response_model=MemberProfile)
    async def member_profile(account_id: int, peer: str, member: str):
        """A group member's profile, read now and kept nowhere (ADR 51 Q14)."""
        async with manager.lease(account_id) as app:
            return await app.profiles.read_member(key(account_id, peer), member)

    @router.get("/chats/{peer}/group-profile", response_model=GroupProfile)
    async def group_profile(account_id: int, peer: str):
        """A group's profile, read now and kept nowhere (ADR 47 Q7)."""
        async with manager.lease(account_id) as app:
            return await app.profiles.read_group(key(account_id, peer))

    @router.get("/new-message", response_model=NewMessagePeople)
    async def new_message(
        account_id: int, query: str = Query(default="", max_length=64)
    ):
        """New Message's people (ADR 51 Q16)."""
        async with manager.lease(account_id) as app:
            return await app.new_message.search(query)

    @router.post("/new-message/{peer}")
    async def open_new_message(account_id: int, peer: str):
        """Open a person's private chat for New Message (ADR 51 Q17)."""
        if not re.fullmatch(r"user:[1-9][0-9]{0,19}", peer):
            raise Refused("Only a person's private chat opens here")
        async with manager.lease(account_id) as app:
            return await app.new_message.open(peer)

    @router.get("/reactions/options")
    async def reaction_options(account_id: int):
        async with manager.lease(account_id) as app:
            return await app.reactions.options()

    @router.post("/chats/{peer}/originals/{identity}/reactions")
    async def react(
        account_id: int,
        peer: str,
        body: ReactionChoice,
        identity: int = Path(gt=0, lt=2**63),
    ):
        async with manager.lease(account_id) as app:
            return {
                "reactions": await app.reactions.choose(
                    key(account_id, peer), identity, body.emoji, body.selected
                )
            }

    @router.get("/chats/{peer}/originals/{identity}/versions")
    def versions(
        account_id: int, peer: str, identity: int, before: int = Query(default=0, ge=0)
    ):
        app = manager.get(account_id)
        chat = key(account_id, peer)
        if app.store.chat(chat).kind == "group":
            app.retained.cache_read(chat)
        result = []
        # A deleted message shows the content captured at its deletion, so
        # that capture is no earlier step (ADR 52 Q7).
        shown = app.store.history.deleted_capture(chat, identity)
        for row in app.store.history.rows(chat, identity, before=before):
            if shown and row["id"] == shown.id:
                continue
            message = Message.model_validate_json(row["body"])
            result.append(
                dict(
                    id=row["id"],
                    message=message.model_dump(),
                    transcript=json.loads(row["transcript"])
                    if row["transcript"]
                    else None,
                    observed=row["observed"],
                    reason=row["reason"],
                    media_available=bool(
                        message.media and app.media.cache.get(message)
                    ),
                )
            )
        return result

    @router.post("/chats/{peer}/originals/current")
    def current_originals(account_id: int, peer: str, body: CachedOriginalsRead):
        app = manager.get(account_id)
        chat_key = key(account_id, peer)
        if app.store.chat(chat_key).kind == "group":
            app.retained.cache_read(chat_key)
        if app.store.chat(chat_key).kind not in {"private", "group", "saved"}:
            raise Refused(
                "Only existing private conversations and groups are supported"
            )
        sources = [
            app.store.message(chat_key, identity) for identity in body.identities
        ]
        current = [
            source
            for source in sources
            if source and source.date >= app.store.clock() - RETENTION
        ]
        present = {source.message_id for source in current}
        return {
            "messages": app.store.history.annotate(current),
            "missing": [
                identity for identity in body.identities if identity not in present
            ],
        }

    @router.post("/chats/{peer}/hidden")
    def hide(account_id: int, peer: str, body: HiddenMessages):
        app = manager.get(account_id)
        chat = key(account_id, peer)
        if app.store.chat(chat).kind == "group":
            app.retained.cache_read(chat)
        return {"hidden": app.store.history.hide(chat, body.identities)}

    @router.get("/chats/{peer}/originals/{identity}")
    async def original(account_id: int, peer: str, identity: int):
        async with manager.lease(account_id) as app:
            return await app.direct(key(account_id, peer), identity)

    @router.post("/chats/{peer}/blocks/{anchor}/retry")
    def retry(account_id: int, peer: str, anchor: int, body: BlockRetry):
        app = manager.get(account_id)
        identity = f"{key(account_id, peer)}/{anchor}"
        return {"attempt": app.rewriting.retry(identity, revision=body.revision)}

    @router.get("/chats/{peer}/blocks/{anchor}/sources")
    def group_sources(
        account_id: int,
        peer: str,
        anchor: int = Path(gt=0, lt=2**63),
        revision: str = Query(min_length=64, max_length=64, pattern=r"^[a-f0-9]{64}$"),
        cursor: int = Query(default=0, ge=0, lt=2**63),
        limit: int = Query(default=100, ge=1, le=100),
    ):
        return manager.get(account_id).source_page(
            key(account_id, peer), anchor, revision, cursor=cursor, limit=limit
        )

    @router.get("/chats/{peer}/memory", response_model=MemoryView)
    def memory(account_id: int, peer: str):
        return manager.get(account_id).memory.view(key(account_id, peer))

    @router.post("/chats/{peer}/memory/entries", response_model=MemoryView)
    def add_memory(account_id: int, peer: str, body: MemoryEdit):
        return manager.get(account_id).memory.add(
            key(account_id, peer), body.revision, body.text
        )

    @router.post("/chats/{peer}/memory/entries/{identity}", response_model=MemoryView)
    def edit_memory(account_id: int, peer: str, identity: str, body: MemoryEdit):
        return manager.get(account_id).memory.edit(
            key(account_id, peer), body.revision, identity, body.text
        )

    @router.post(
        "/chats/{peer}/memory/entries/{identity}/remove", response_model=MemoryView
    )
    def remove_memory(account_id: int, peer: str, identity: str, body: Revision):
        return manager.get(account_id).memory.remove(
            key(account_id, peer), body.revision, identity
        )

    @router.post("/chats/{peer}/memory/clear", response_model=MemoryView)
    def clear_memory(account_id: int, peer: str, body: Revision):
        return manager.get(account_id).memory.clear(
            key(account_id, peer), body.revision
        )

    @router.post("/chats/{peer}/memory/retry", response_model=MemoryView)
    def retry_memory(account_id: int, peer: str):
        return manager.get(account_id).memory.retry(key(account_id, peer))

    @router.get("/chats/{peer}/remember", response_model=Remembering)
    def remembering(account_id: int, peer: str):
        return manager.get(account_id).memory.remember_view(key(account_id, peer))

    @router.post("/chats/{peer}/remember", response_model=Remembering)
    def set_remembering(account_id: int, peer: str, body: CalmingEdit):
        return manager.get(account_id).memory.remember(
            key(account_id, peer), body.enabled
        )

    # ADR 55: the account's own Account Memory, under About you.
    @router.get("/memory", response_model=AccountMemoryView)
    def account_memory(account_id: int):
        return manager.get(account_id).memory.account_view()

    @router.post("/memory/entries", response_model=AccountMemoryView)
    def add_account_memory(account_id: int, body: MemoryEdit):
        return manager.get(account_id).memory.add(ACCOUNT, body.revision, body.text)

    @router.post("/memory/entries/{identity}", response_model=AccountMemoryView)
    def edit_account_memory(account_id: int, identity: str, body: MemoryEdit):
        return manager.get(account_id).memory.edit(
            ACCOUNT, body.revision, identity, body.text
        )

    @router.post("/memory/entries/{identity}/remove", response_model=AccountMemoryView)
    def remove_account_memory(account_id: int, identity: str, body: Revision):
        return manager.get(account_id).memory.remove(ACCOUNT, body.revision, identity)

    @router.post("/memory/clear", response_model=AccountMemoryView)
    def clear_account_memory(account_id: int, body: Revision):
        return manager.get(account_id).memory.clear(ACCOUNT, body.revision)

    # ADR 55 Q10-Q13: traits, a chat's person's and the account holder's.
    @router.get("/chats/{peer}/traits", response_model=TraitsView)
    def traits(account_id: int, peer: str):
        return manager.get(account_id).traits.view(key(account_id, peer))

    @router.post("/chats/{peer}/traits/scales/{trait}", response_model=TraitsView)
    def set_trait(account_id: int, peer: str, trait: str, body: ScaleEdit):
        return manager.get(account_id).traits.set_scale(
            key(account_id, peer), body.revision, trait, body.value
        )

    @router.post("/chats/{peer}/traits/notes", response_model=TraitsView)
    def add_trait_note(account_id: int, peer: str, body: MemoryEdit):
        return manager.get(account_id).traits.add_note(
            key(account_id, peer), body.revision, body.text
        )

    @router.post("/chats/{peer}/traits/notes/{identity}", response_model=TraitsView)
    def edit_trait_note(account_id: int, peer: str, identity: str, body: MemoryEdit):
        return manager.get(account_id).traits.edit_note(
            key(account_id, peer), body.revision, identity, body.text
        )

    @router.post(
        "/chats/{peer}/traits/notes/{identity}/remove", response_model=TraitsView
    )
    def remove_trait_note(account_id: int, peer: str, identity: str, body: Revision):
        return manager.get(account_id).traits.remove_note(
            key(account_id, peer), body.revision, identity
        )

    @router.get("/traits", response_model=TraitsView)
    def account_traits(account_id: int):
        return manager.get(account_id).traits.view(ACCOUNT)

    @router.post("/traits/scales/{trait}", response_model=TraitsView)
    def set_account_trait(account_id: int, trait: str, body: ScaleEdit):
        return manager.get(account_id).traits.set_scale(
            ACCOUNT, body.revision, trait, body.value
        )

    @router.post("/traits/notes", response_model=TraitsView)
    def add_account_trait_note(account_id: int, body: MemoryEdit):
        return manager.get(account_id).traits.add_note(
            ACCOUNT, body.revision, body.text
        )

    @router.post("/traits/notes/{identity}", response_model=TraitsView)
    def edit_account_trait_note(account_id: int, identity: str, body: MemoryEdit):
        return manager.get(account_id).traits.edit_note(
            ACCOUNT, body.revision, identity, body.text
        )

    @router.post("/traits/notes/{identity}/remove", response_model=TraitsView)
    def remove_account_trait_note(account_id: int, identity: str, body: Revision):
        return manager.get(account_id).traits.remove_note(
            ACCOUNT, body.revision, identity
        )

    @router.post("/chats/{peer}/process-older")
    def process_older(account_id: int, peer: str):
        app = manager.get(account_id)
        return {"queued": app.rewriting.process_older(key(account_id, peer))}

    @router.post("/chats/{peer}/stop")
    def stop(account_id: int, peer: str):
        return {
            "stopped": manager.get(account_id).rewriting.stop(key(account_id, peer))
        }

    @router.post("/chats/{peer}/undo")
    def undo(account_id: int, peer: str, body: UndoRewrite):
        return manager.get(account_id).rewriting.undo(
            key(account_id, peer), body.revision, body.attempt
        )

    @router.post("/chats/{peer}/send")
    async def send(account_id: int, peer: str, body: Revision, request: Request):
        """Accept the exact submission and return its cleared composer; the
        service then sends it in order (ADR 26)."""
        async with manager.lease(account_id) as app:
            chat = key(account_id, peer)

            def guard():
                if not request.app.state.browser_auth.active(
                    request.state.local_session
                ):
                    raise HTTPException(
                        401,
                        "Connect this browser from the Mac launcher",
                        headers={"Clear-Site-Data": '"cache"'},
                    )
                if manager.get(account_id) is not app:
                    raise Refused("The active account workspace changed")
                if app.store.chat(chat).kind == "group" and not any(
                    row["key"] == chat for row in app.store.recent_chats("group")
                ):
                    raise Refused(
                        "Inactive group work requires reviewed operation recovery"
                    )

            operation = await app.delivery.submit(chat, body.revision, guard=guard)
            guard()
            return {
                "operation": operation_view(operation, app.store.clock()),
                "draft": app.store.draft(chat),
            }

    @router.post("/operations/{identity}/recheck")
    async def recheck(account_id: int, identity: str):
        async with manager.lease(account_id) as app:
            return operation_view(
                await app.delivery.recheck(identity), app.store.clock()
            )

    # Async on the service loop: they wake its delivery queue, and a draining
    # account waits for them before recovery.
    @router.post("/operations/{identity}/retry")
    async def retry_send(account_id: int, identity: str):
        async with manager.lease(account_id) as app:
            return operation_view(app.delivery.retry(identity), app.store.clock())

    @router.post("/operations/{identity}/cancel")
    async def cancel_send(account_id: int, identity: str):
        async with manager.lease(account_id) as app:
            return operation_view(app.delivery.cancel(identity), app.store.clock())

    @router.post(
        "/chats/{peer}/originals/{identity}/media/prepare-video",
        response_model=PreparedVideo,
    )
    async def prepare_video(
        request: Request,
        account_id: int,
        peer: str,
        body: VideoPreparation,
        identity: int = Path(gt=0, lt=2**63),
    ):
        async with manager.lease(account_id) as app:
            chat = key(account_id, peer)
            message = (
                app.store.history.version(chat, identity, body.version)
                if body.version
                else app.store.message(chat, identity)
            )
            if not message or message.revision != body.revision:
                raise Refused("Video source changed; refresh its original")
            access_revision = media_access(request, app, message, version=body.version)
            mime = await app.media.prepare_video(message, history=bool(body.version))
            media_access(
                request,
                app,
                message,
                version=body.version,
                access_revision=access_revision,
            )
            return PreparedVideo(
                state="ready" if mime else "unsupported",
                revision=message.revision,
                version=body.version,
                mime=mime,
            )

    @router.get("/chats/{peer}/originals/{identity}/media")
    async def original_media(
        request: Request,
        account_id: int,
        peer: str,
        identity: int,
        download: bool = False,
        variant: Literal["file", "poster", "aac"] = "file",
        version: int | None = Query(default=None, gt=0),
        revision: int | None = Query(default=None, ge=0),
    ):
        async with manager.lease(account_id) as app:
            chat = key(account_id, peer)
            # A deleted message's media is its capture's, from the cache only
            # (ADR 52 Q7).
            version = app.store.history.shown_version(chat, identity, version)
            message = (
                app.store.history.version(chat, identity, version)
                if version
                else app.store.message(chat, identity)
            )
            if not message and revision is None and version is None:
                message = await app.direct(chat, identity)
            if not message or message.deleted or not message.media:
                raise Refused("Original media unavailable")
            if revision is not None and message.revision != revision:
                raise Refused("Media source changed; refresh its original")
            # iPhones play a voice note's AAC copy (ADR 46 CU1), never a download.
            if variant == "aac" and (download or version or not voice_note(message)):
                raise Refused("Only a current voice note has an AAC copy")
            video = video_candidate(message)
            selected_inline = (
                variant == "file"
                and message.media in ("gif", "sticker")
                and not download
            )
            if video and revision is None:
                raise Refused("The exact video source revision is required")
            access_revision = media_access(request, app, message, version=version)
            if video and variant == "file":
                app.media.video_access(message, history=bool(version))
            cached = (
                app.media.cache.get(
                    message,
                    variant,
                    limit=IMAGE_BYTES
                    if variant == "poster"
                    else STICKER_BYTES
                    if message.media == "sticker"
                    else DOWNLOAD_BYTES,
                )
                if version
                or app.store.chat(chat).kind == "group"
                or (video and variant == "file")
                or selected_inline
                else None
            )
            if version and cached is None:
                raise Refused("Historical media is no longer available locally")
            if video and variant == "file" and not download:
                if cached is None:
                    raise Refused(
                        "Video is not available locally; select Play or Retry loading"
                    )
                mime = await app.media.video_type(message, cached["path"])
                media_access(
                    request,
                    app,
                    message,
                    version=version,
                    access_revision=access_revision,
                )
                if mime not in {"video/mp4", "video/webm"}:
                    raise Refused(
                        "Video cannot be played here; use Download or Telegram"
                    )
            if selected_inline and cached:
                try:
                    facts = await app.media.selection_type(message, cached["path"])
                except Unavailable:
                    raise HTTPException(
                        503, "Media verification unavailable; retry"
                    ) from None
                media_access(
                    request,
                    app,
                    message,
                    version=version,
                    access_revision=access_revision,
                )
                if (
                    app.media.cache.get(
                        message,
                        limit=STICKER_BYTES
                        if message.media == "sticker"
                        else DOWNLOAD_BYTES,
                    )
                    is None
                ):
                    raise Refused("Media is no longer available locally; retry")
                if (
                    facts.format == "tgs"
                    or not representation(facts, kind=message.media).renderer_supported
                ):
                    raise Refused("Use the supported media preview for this item")
            if variant == "poster" or (
                (app.media.kept(message) or cached) and (not video or cached)
            ):
                if version and cached is None:
                    raise Refused("Historical media is no longer available locally")
                etag = app.media.etag(message, variant)
                private = {"ETag": etag, "Cache-Control": "private, no-cache"}
                # Selected formats also need actual retained-byte proof before
                # validating a browser copy from an older renderer/cache label.
                if not download and etag in request.headers.get(
                    "if-none-match", ""
                ).split(", "):
                    if selected_inline and cached is None:
                        raise Refused("Media preview is no longer available locally")
                    return Response(status_code=304, headers=private)
                path, mime = await app.media.serve(
                    message,
                    variant=variant,
                    priority=DOWNLOAD if download else PLAYBACK,
                    history=bool(version),
                    inline=not download,
                )
                try:
                    media_access(
                        request,
                        app,
                        message,
                        version=version,
                        access_revision=access_revision,
                    )
                except BaseException:
                    path.unlink(missing_ok=True)
                    raise
                cleanup = BackgroundTask(path.unlink, missing_ok=True)
                if mime and not download:
                    # FileResponse answers byte ranges (206), as Safari requires.
                    return FileResponse(
                        path, media_type=mime, headers=private, background=cleanup
                    )
                return FileResponse(
                    path,
                    media_type="application/octet-stream",
                    background=cleanup,
                    filename=download_name(message, app.store.timezone),
                    headers={"Cache-Control": "no-store"},
                )
            if version:
                raise Refused("Historical media was not cached")
            chunks = app.media.stream(message)
            first = await anext(chunks, b"")  # Refusals surface before headers.
            try:
                media_access(
                    request,
                    app,
                    message,
                    version=version,
                    access_revision=access_revision,
                )
            except BaseException:
                await chunks.aclose()
                raise

            async def body():
                yield first
                async for chunk in chunks:
                    yield chunk

            return StreamingResponse(
                body(),
                media_type="application/octet-stream",
                headers={
                    "Content-Disposition": "attachment; filename*=utf-8''"
                    + quote(download_name(message, app.store.timezone)),
                    "Cache-Control": "no-store",
                },
            )

    @router.get("/chats/{peer}/originals/{identity}/transcript")
    def transcript(account_id: int, peer: str, identity: int):
        app = manager.get(account_id)
        chat = key(account_id, peer)
        if app.store.chat(chat).kind == "group":
            app.retained.cache_read(chat)
        message = app.store.message(chat, identity)
        capture = app.store.history.deleted_capture(chat, identity)
        if capture and capture.message.media in SPOKEN_MEDIA:
            # The transcript a deleted voice note had (ADR 52 Q7).
            return capture.transcript
        if not message or message.deleted or message.media not in SPOKEN_MEDIA:
            raise Refused("Voice source unavailable")
        return app.media.cached(message).get("transcript")

    @router.get(
        "/chats/{peer}/originals/{identity}/transcript/status",
        response_model=TranscriptStatus,
    )
    def transcript_status(account_id: int, peer: str, identity: int):
        """The transcript, or where its transcription stands (ADR 51 Q9)."""
        app = manager.get(account_id)
        chat = key(account_id, peer)
        if app.store.chat(chat).kind == "group":
            app.retained.cache_read(chat)
        message = app.store.message(chat, identity)
        capture = app.store.history.deleted_capture(chat, identity)
        if capture and capture.message.media in SPOKEN_MEDIA:
            # A deleted voice note keeps the transcript it had; none starts.
            return (
                {"state": "done", "transcript": capture.transcript, "reason": None}
                if capture.transcript
                else {
                    "state": "failed",
                    "transcript": None,
                    "reason": "Deleted before it was transcribed",
                }
            )
        if not message or message.deleted or message.media not in SPOKEN_MEDIA:
            raise Refused("Voice source unavailable")
        return app.transcripts.status(message)

    @router.post(
        "/chats/{peer}/originals/{identity}/transcript",
        response_model=TranscriptStatus,
    )
    async def transcribe(account_id: int, peer: str, identity: int):
        """Show transcript starts a missing transcript (ADR 51 Q9)."""
        app = manager.get(account_id)
        chat = key(account_id, peer)
        if app.store.chat(chat).kind == "group":
            app.retained.cache_read(chat)
        message = app.store.message(chat, identity)
        if not message:
            raise Refused("Voice source unavailable")
        return app.transcripts.request(message)

    @router.post("/chats/{peer}/images")
    def attach(account_id: int, peer: str, body: ImageUpload):
        return manager.get(account_id).attachments.add(
            key(account_id, peer), body.revision, body.name, body.data
        )

    @router.get("/chats/{peer}/images/{identity}")
    def image(account_id: int, peer: str, identity: str):
        app = manager.get(account_id)
        chat = key(account_id, peer)
        if app.store.chat(chat).kind == "group":
            app.retained.cache_read(chat)
        value, path = app.attachments.get(chat, identity)
        return Response(path.read_bytes(), media_type=value["mime"])

    @router.post("/chats/{peer}/images/{identity}/remove")
    def remove_image(account_id: int, peer: str, identity: str, body: Revision):
        return manager.get(account_id).attachments.remove(
            key(account_id, peer), body.revision, identity
        )

    return router
