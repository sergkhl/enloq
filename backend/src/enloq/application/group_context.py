"""Cached native group input, bounded independently of private rewrites."""

import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from ..domain.forwarding import attribution
from ..domain.models import ANALYSED_MEDIA, Chat, Message, Refused, reply_target
from ..storage.store import RETENTION, WINDOW
from . import context
from .media import transcript_fingerprint

PACKET_BYTES = 32000
CHUNK_BYTES = 24000
IMAGES_PER_CHUNK = 4
MAX_SOURCES = 200


def encoded(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


def day_bounds(store, day):
    try:
        parsed = date.fromisoformat(day)
    except (TypeError, ValueError):
        raise Refused("A calendar briefing day is required") from None
    if parsed.isoformat() != day:
        raise Refused("A canonical calendar briefing day is required")
    zone = ZoneInfo(store.timezone)
    return (
        datetime.combine(parsed, time(), zone).timestamp(),
        datetime.combine(parsed + timedelta(days=1), time(), zone).timestamp(),
    )


def calendar_day(store, message):
    return (
        datetime.fromtimestamp(message.date, ZoneInfo(store.timezone))
        .date()
        .isoformat()
    )


def eligible(message):
    """Owner/bot authors remain eligible; service/wholly Non-AI sources do not."""
    return bool(
        not message.deleted
        and not message.service
        and (message.text.strip() or message.media in ANALYSED_MEDIA)
    )


def original_characters(messages):
    return sum(len(message.text.strip()) for message in messages if eligible(message))


def group_chat(store, key, *, db=None):
    if db is None:
        chat = store.chat(key)
    else:
        row = db.execute("SELECT body FROM chats WHERE key=?", (key,)).fetchone()
        if not row:
            raise Refused("Native group access is unavailable")
        chat = Chat.model_validate_json(row["body"])
    state = store.groups.get(key, db=db)
    if (
        chat.account_id != store.account_id
        or chat.kind != "group"
        or not chat.ai_eligible
        or not state.supported
        or state.access != "available"
    ):
        raise Refused("Native group access is unavailable")
    return state


def day_sources(store, key, day, *, admission=True):
    """Read only retained originals, without native fetches or history scans."""
    group_chat(store, key)
    start, end = day_bounds(store, day)
    return [
        message
        for row in store.rows(
            "SELECT body FROM messages WHERE chat_key=? AND date>=? AND date<? "
            "AND deleted=0 ORDER BY message_id",
            (key, max(start, store.clock() - RETENTION), end),
        )
        if eligible(message := Message.model_validate_json(row["body"]))
        and store.groups.current(message, admission=admission)
    ]


def projection(message):
    value = context.projection(message)
    # Telegram's out bit is delivery direction, never proof of author identity.
    value["speaker"] = {
        "peer": message.author_peer,
        "name": (message.author or "Unknown") if message.author_peer else "Unknown",
        "signature": message.author_signature,
    }
    return value


def _prepared(store, message):
    value = projection(message)
    if store is None:
        return value
    rows = store.rows(
        "SELECT body FROM derivatives WHERE source=? AND revision=?",
        (message.key, message.revision),
    )
    cached = json.loads(rows[0]["body"]) if rows else {}
    for field in ("transcript", "image_context"):
        if cached.get(field):
            value[field] = cached[field]
    return value


def _footprint(store, message):
    # Leave the same 6,000-byte Supporting Context allowance and room for the
    # packet envelope. Dependency metadata is input too, rather than an
    # invisible exception to the existing 32,000-byte request bound.
    return encoded(
        dict(
            message=_prepared(store, message),
            input={message.key: message.revision},
            attribution={message.key: attribution(message)},
            speaker={message.key: [message.author_peer, message.author_signature]},
        )
    )


def _enriched_footprint(packet, message):
    key = f"{packet['chat_key']}/{message['id']}"
    return encoded(
        dict(
            message=message,
            input={key: message["revision"]},
            attribution={key: packet["attribution_inputs"].get(key)},
            speaker={key: packet["speaker_inputs"].get(key)},
        )
    )


@dataclass(frozen=True)
class InputPlan:
    chunks: tuple[tuple[Message, ...], ...]
    omitted: tuple[dict, ...]

    @property
    def selected_ids(self):
        return tuple(message.message_id for chunk in self.chunks for message in chunk)


def partition(sources, *, store=None, max_sources=MAX_SOURCES):
    """Whole sources, oldest first; an oversized source is explicitly omitted.

    Cached transcript/image wording participates in the bound. New transcript
    wording is re-partitioned after local preparation, never silently truncated.
    """
    if type(max_sources) is not int or not 0 <= max_sources <= MAX_SOURCES:
        raise Refused("Invalid briefing source bound")
    sources = tuple(
        sorted((m for m in sources if eligible(m)), key=lambda m: m.message_id)
    )
    identities = {(m.account_id, m.peer) for m in sources}
    if len(identities) > 1 or len({m.message_id for m in sources}) != len(sources):
        raise Refused("Briefing input must use one exact group")
    chunks, current_chunk, omitted = [], [], []
    used, images, selected = 0, 0, 0
    for message in sources:
        value = _prepared(store, message)
        size = _footprint(store, message)
        if selected >= max_sources:
            omitted.append(
                dict(message_id=message.message_id, reason="source work limit")
            )
            continue
        if size > CHUNK_BYTES or len(message.text.encode()) > context.TARGET_BYTES:
            omitted.append(
                dict(
                    message_id=message.message_id, reason="source exceeds request bound"
                )
            )
            continue
        image = int(message.media == "image" and not value.get("image_context"))
        if current_chunk and (
            used + size > CHUNK_BYTES or images + image > IMAGES_PER_CHUNK
        ):
            chunks.append(tuple(current_chunk))
            current_chunk, used, images = [], 0, 0
        current_chunk.append(message)
        used += size
        images += image
        selected += 1
    if current_chunk:
        chunks.append(tuple(current_chunk))
    return InputPlan(tuple(chunks), tuple(omitted))


def packet(store, key, day, sources, *, omissions=()):
    """Freeze native dependencies and one cached direct reply per request."""
    state = group_chat(store, key)
    intake = store.groups.intake(key)
    start, end = day_bounds(store, day)
    sources = tuple(sources)
    if (
        not sources
        or len({message.message_id for message in sources}) != len(sources)
        or any(
            message.account_id != store.account_id
            or f"{message.account_id}/{message.peer}" != key
            or not start <= message.date < end
            or not eligible(message)
            or not store.groups.current(message)
            or (current_source := store.message(key, message.message_id)) is None
            or current_source.revision != message.revision
            for message in sources
        )
    ):
        raise Refused("Briefing source changed or is unavailable")
    sources = tuple(sorted(sources, key=lambda m: m.message_id))
    if sum(_footprint(store, message) for message in sources) > CHUNK_BYTES:
        raise Refused("Briefing sources exceed the request bound")
    ids = {m.message_id for m in sources}
    reply = next(
        (
            reply_target(m)
            for m in reversed(sources)
            if reply_target(m) and reply_target(m) not in ids
        ),
        None,
    )
    parent = store.message(key, reply) if reply else None
    missing = []
    if reply and (
        parent is None
        or parent.deleted
        or parent.service
        or not store.groups.current(parent)
    ):
        parent = None
        missing.append(
            dict(message_id=reply, reason="direct reply is not currently cached")
        )
    since = max(store.clock() - RETENTION, min(store.clock(), sources[0].date) - WINDOW)
    recent = [
        Message.model_validate_json(row["body"])
        for row in store.rows(
            "SELECT body FROM messages WHERE chat_key=? AND message_id<? AND date>=? "
            "AND deleted=0 ORDER BY message_id DESC LIMIT 20",
            (key, sources[0].message_id, since),
        )
    ]
    supporting, used = [], 0
    for message in ([parent] if parent else []) + recent:
        if (
            message.message_id in ids
            or message.message_id in {m.message_id for m in supporting}
            or message.service
            or not store.groups.current(message)
        ):
            continue
        size = _footprint(store, message)
        if used + size > context.CONTEXT_TOKENS:
            if parent and message.key == parent.key:
                raise Refused("Direct reply exceeds the supporting context bound")
            continue
        supporting.append(message)
        used += size
    combined = sources + tuple(supporting)
    result = dict(
        day=day,
        chat_key=key,
        messages=[projection(m) for m in sources],
        context=[projection(m) for m in sorted(supporting, key=lambda m: m.message_id)],
        covered_ids=[m.message_id for m in sources],
        inputs={m.key: m.revision for m in combined},
        attribution_inputs={m.key: attribution(m) for m in combined},
        speaker_inputs={m.key: [m.author_peer, m.author_signature] for m in combined},
        group_proof=dict(epoch=intake.epoch, access_revision=state.access_revision),
        direct_reply=parent.message_id if parent else None,
        missing_context=missing,
        omissions=list(omissions),
    )
    if encoded(result) > PACKET_BYTES:
        raise Refused("Combined briefing inputs exceed the request bound")
    return result


def current(store, value, *, admission=False, db=None):
    """Material native/derivative dependencies; processing switches are separate.

    A new stage is admitted only on originals proven in the current intake
    epoch. Work already consumed stands through a new epoch until an original is
    seen to change (ADR 34): an epoch change alone regenerates nothing.
    """
    if db is None:
        with store.transaction() as held:
            return current(store, value, admission=admission, db=held)
    from ..domain.briefing_contract import BriefingError, covered_source_ids

    try:
        key = value["chat_key"]
        state = group_chat(store, key, db=db)
        intake = store.groups.intake(key, db=db)
        proof = value["group_proof"]
        if (
            proof["access_revision"] != state.access_revision
            or type(proof["epoch"]) is not int
            or not 0 <= proof["epoch"] <= intake.epoch
        ):
            return False
        if admission and intake.replay_pending:
            return False
        if not isinstance(value.get("inputs"), dict) or any(
            not isinstance(source, str) or type(revision) is not int or revision < 0
            for source, revision in value["inputs"].items()
        ):
            return False
        covered = covered_source_ids(value)
        if not covered or any(
            f"{key}/{identity}" not in value["inputs"] for identity in covered
        ):
            return False
        projected = value.get("messages", []) + value.get("context", [])
        if len({m["id"] for m in projected}) != len(projected):
            return False
        for message in projected:
            identity = message["id"]
            source = f"{key}/{identity}"
            if (
                type(identity) is not int
                or identity <= 0
                or source not in value["inputs"]
                or type(message["revision"]) is not int
                or message["revision"] != value["inputs"][source]
            ):
                return False
        if not context.current(store, value, db=db):
            return False
        for source, revision in value["inputs"].items():
            if not source.startswith(key + "/"):
                return False
            row = db.execute(
                "SELECT body FROM messages WHERE key=?", (source,)
            ).fetchone()
            message = Message.model_validate_json(row["body"]) if row else None
            if (
                message is None
                or message.revision != revision
                or message.service
                or not (
                    store.groups.current(message, admission=True, db=db)
                    if admission
                    else store.groups.standing(message, db=db)
                )
                or value.get("speaker_inputs", {}).get(source)
                != [message.author_peer, message.author_signature]
            ):
                return False
        return True
    except (BriefingError, Refused, KeyError, TypeError, ValueError):
        return False


def _fit_context(prepared):
    """Supporting context gives way when media prepared since freezing outgrows it.

    Freezing admitted this context under the same bounds with the wording cached
    then, and repartitioning targets cannot shrink it. Choose it again the same
    way: the direct reply always, then the newest that still fit. A direct reply
    that is itself too large stays for the caller's refusal, as when freezing.
    """

    def packet_bytes():
        return encoded({k: v for k, v in prepared.items() if k != "images"})

    supporting = prepared.get("context", [])
    sizes = {m["id"]: _enriched_footprint(prepared, m) for m in supporting}
    if sum(sizes.values()) <= context.CONTEXT_TOKENS and packet_bytes() <= PACKET_BYTES:
        return
    reply = prepared.get("direct_reply")
    kept, used = [], 0
    for message in sorted(supporting, key=lambda m: (m["id"] != reply, -m["id"])):
        if message["id"] == reply or used + sizes[message["id"]] <= (
            context.CONTEXT_TOKENS
        ):
            kept.append(message)
            used += sizes[message["id"]]

    def keep():
        retained = {m["id"] for m in kept}
        prepared["context"] = sorted(kept, key=lambda m: m["id"])
        for message in supporting:
            if message["id"] in retained:
                continue
            key = f"{prepared['chat_key']}/{message['id']}"
            for field in (
                "inputs",
                "attribution_inputs",
                "speaker_inputs",
                "image_inputs",
                "transcript_inputs",
            ):
                prepared.get(field, {}).pop(key, None)
        if "images" in prepared:
            prepared["images"] = [
                image
                for image in prepared["images"]
                if image["message_id"] in retained
                or image["message_id"] in prepared["covered_ids"]
            ]

    keep()
    while kept and kept[-1]["id"] != reply and packet_bytes() > PACKET_BYTES:
        kept.pop()
        keep()


async def prepare(
    app,
    value,
    *,
    retry=False,
    guard=None,
    valid=None,
    on_stage=lambda _: None,
    on_speech_start=None,
):
    """Reusable media stays local; group preparation never resolves uncached IDs."""
    import copy

    material = valid or (lambda: current(app.store, value))
    prepared = await app.media.enrich(
        copy.deepcopy(value),
        value["chat_key"],
        retry=retry,
        guard=guard,
        valid=material,
        on_stage=on_stage,
        on_speech_start=on_speech_start,
    )
    prepared["image_inputs"] = {
        f"{value['chat_key']}/{m['id']}": m["image_context"]
        for m in prepared["messages"] + prepared.get("context", [])
        if m.get("image_context")
    }
    prepared["transcript_inputs"] = {
        f"{value['chat_key']}/{m['id']}": transcript_fingerprint(m["transcript"])
        for m in prepared["messages"] + prepared.get("context", [])
        if m.get("transcript")
    }
    if (
        sum(_enriched_footprint(prepared, m) for m in prepared["messages"])
        <= CHUNK_BYTES
    ):
        _fit_context(prepared)
    # A fresh local transcript can be much longer than its original caption.
    # Hold before any model call so the planner can repartition the now-cached
    # whole sources, explicitly omitting any individually oversized source.
    if (
        sum(_enriched_footprint(prepared, m) for m in prepared["messages"])
        > CHUNK_BYTES
        or sum(_enriched_footprint(prepared, m) for m in prepared.get("context", []))
        > context.CONTEXT_TOKENS
        or encoded({k: v for k, v in prepared.items() if k != "images"}) > PACKET_BYTES
    ):
        raise Refused(
            "Prepared briefing inputs exceed the request bound; repartition cached sources"
        )
    if not material() or not current(app.store, prepared):
        raise Refused("Briefing input changed during preparation")
    return prepared


def synthesis(store, key, day, *, packets=(), chunks=(), omissions=()):
    """Bound final input to valid chunk notes and exact prepared raw targets.

    A caller may use a small day's originals directly, avoiding a redundant
    chunk request. Oversized days use reusable chunk notes. This performs no
    model work and never changes coverage to fit the bound.
    """
    from ..domain.briefing_contract import BriefingError, validate_briefing

    packets, chunks = tuple(packets), tuple(chunks)
    if len(packets) > 1:
        raise Refused("Final briefing uses one bounded raw source packet")
    group_chat(store, key)
    day_bounds(store, day)
    dependencies = list(packets)
    notes, messages, supporting, covered = [], [], [], set()
    for chunk in chunks:
        if chunk.snapshot.key != key or chunk.snapshot.day != day or not chunk.valid:
            raise Refused("Briefing chunk belongs to a different source scope")
        if covered & set(chunk.covered_ids):
            raise Refused("Briefing coverage repeats a source")
        try:
            output = validate_briefing(chunk.output, chunk.covered_ids)
        except BriefingError:
            raise Refused("Briefing chunk is invalid") from None
        covered.update(chunk.covered_ids)
        notes.append(dict(covered_ids=list(chunk.covered_ids), **output.model_dump()))
        dependencies.extend(chunk.snapshot.packets)
    inputs, forward_inputs, speaker_inputs, transcripts, images = {}, {}, {}, {}, {}
    proof = None
    for value in dependencies:
        if (
            value.get("chat_key") != key
            or value.get("day") != day
            or not current(store, value)
        ):
            raise Refused("Briefing dependency changed before synthesis")
        # Saved chunks may predate the current intake epoch; each dependency
        # was just proven standing. Only an access change separates them.
        latest = value["group_proof"]
        if proof is not None and proof["access_revision"] != latest["access_revision"]:
            raise Refused("Briefing access changed before synthesis")
        if proof is None or latest["epoch"] > proof["epoch"]:
            proof = latest
        inputs.update(value["inputs"])
        forward_inputs.update(value.get("attribution_inputs", {}))
        speaker_inputs.update(value.get("speaker_inputs", {}))
        transcripts.update(value.get("transcript_inputs", {}))
        images.update(value.get("image_inputs", {}))
    for value in packets:
        if value.get("images"):
            raise Refused("Briefing images require validated meanings before synthesis")
        identities = {m["id"] for m in value["messages"]}
        if covered & identities:
            raise Refused("Briefing coverage repeats a source")
        covered.update(identities)
        messages.extend(value["messages"])
        supporting.extend(value.get("context", []))
    if not covered:
        raise Refused("No substantive briefing input is available")
    # Each source is either a target or Supporting Context, with no duplicated
    # earlier event presented as today's target activity.
    raw_ids = {m["id"] for m in messages}
    distinct_context = {m["id"]: m for m in supporting if m["id"] not in raw_ids}
    result = dict(
        day=day,
        chat_key=key,
        messages=messages,
        context=[distinct_context[i] for i in sorted(distinct_context)],
        chunks=notes,
        covered_ids=sorted(covered),
        inputs=inputs,
        attribution_inputs=forward_inputs,
        speaker_inputs=speaker_inputs,
        group_proof=proof,
        direct_reply=packets[0].get("direct_reply") if packets else None,
        transcript_inputs=transcripts,
        image_inputs=images,
        omissions=list(omissions),
        missing_context=[
            m for value in dependencies for m in value.get("missing_context", [])
        ],
    )
    if encoded(result) > PACKET_BYTES:
        raise Refused("Final briefing inputs exceed the request bound")
    return result
