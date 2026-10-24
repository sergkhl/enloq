"""Native same-chat context, never a profile or an automatic suggested answer."""

from ..domain.forwarding import attribution, attribution_current
from ..domain.models import SPOKEN_MEDIA, Message, Refused, reply_target
from ..domain.source_revision import dependency_fingerprint, proves
from ..storage.store import RETENTION, WINDOW
from .blocks import fingerprint
from .media import transcript_fingerprint

CONTEXT_TOKENS = 6000
FORWARD_DETAILS = ("forward_date", "forward_signature")
TARGET_BYTES = 24000
# The enriched request bound media preparation applies (``media.enrich``).
PACKET_BYTES = 32000


def projection(message):
    value = dict(
        id=message.message_id,
        revision=message.revision,
        text=message.text,
        links=[link.model_dump() for link in message.links],
        outgoing=message.outgoing,
        date=message.date,
        # A link preview is ordinary text; its URL is already in the text.
        media=None if message.media == "link" else message.media,
        # Only a message in this chat; another chat's number means nothing here.
        reply_to=reply_target(message),
        forwarded=getattr(message, "forwarded", None),
        forwarded_from=getattr(message, "forwarded_from", None),
    )
    # ADR 27: imported history is its sender's own message, dated by its
    # original; other forwards carry their header details for attribution.
    # Absent details are omitted, keeping ordinary messages compact.
    if getattr(message, "imported", None):
        value.update(forwarded=False, forwarded_from=None, imported=True)
    if value["forwarded"] or value.get("imported"):
        for field in FORWARD_DETAILS:
            if (detail := getattr(message, field, None)) is not None:
                if field != "forward_signature" or value["forwarded"]:
                    value[field] = detail
    return value


async def incoming(app, block, *, retry=False, on_stage=None, explicit=False):
    """``explicit`` work (a click) may target any cached block up to 30 days
    old; everything else keeps the seven-day processing window."""
    import json

    store = app.store
    identities = json.loads(block["sources"])
    # Legacy untyped rows learn their kind by exact ID before preparation.
    await app.refresh_legacy(block["chat_key"], through=max(identities))
    sources = [store.message(block["chat_key"], i) for i in identities]
    oldest = store.clock() - (RETENTION if explicit else WINDOW)
    if not sources or any(m is None or m.deleted or m.date < oldest for m in sources):
        raise Refused("Source expired or unavailable; originals remain accessible")
    if fingerprint(sources) != block["revision"]:
        raise Refused("Source changed before dispatch")
    if sum(len(m.text.encode()) for m in sources) > TARGET_BYTES:
        raise Refused("Incoming block exceeds the text bound; read its originals")
    # A combined block gets one older direct reference: the latest explicit
    # reply to a message in this chat (ADR 41).
    reply_id = next(
        (reply_target(m) for m in reversed(sources) if reply_target(m)), None
    )
    parent = await app.direct(block["chat_key"], reply_id) if reply_id else None
    if reply_id and (parent is None or parent.deleted):
        raise Refused("Direct reply source unavailable; read the originals")
    # Seven days back from now, or from the target's own date when the target
    # is older than that (ADR 15); the local store only, never a history scan.
    since = min(store.clock(), sources[0].date) - WINDOW
    recent = [
        Message.model_validate_json(r["body"])
        for r in store.rows(
            "SELECT body FROM messages WHERE chat_key=? AND message_id<? AND date>=? AND deleted=0 ORDER BY message_id DESC LIMIT 20",
            (block["chat_key"], sources[0].message_id, since),
        )
    ]
    context = []
    used = 0
    for m in ([parent] if parent else []) + recent:
        if m.key in {n.key for n in sources + context}:
            continue
        # UTF-8 byte count is a conservative token upper bound, including metadata.
        size = len(str(projection(m)).encode())
        if used + size > CONTEXT_TOKENS:
            if parent and m.key == parent.key:
                raise Refused("Direct reply exceeds the supporting context bound")
            continue
        context.append(m)
        used += size
    transient = [
        m for m in context if not store.message(block["chat_key"], m.message_id)
    ]
    packet = dict(
        messages=[projection(m) for m in sources],
        context=[projection(m) for m in sorted(context, key=lambda m: m.message_id)],
        inputs={m.key: m.revision for m in sources + context},
        attribution_inputs={m.key: attribution(m) for m in sources + context},
        transient=[m.key for m in transient],
        transient_inputs={
            m.key: digest
            for m in transient
            if (digest := dependency_fingerprint(m)) is not None
        },
        direct_reply=parent.message_id if parent else None,
        fingerprint=block["revision"],
    )

    packet = await app.media.enrich(
        packet, block["chat_key"], retry=retry, on_stage=on_stage or (lambda _: None)
    )
    packet["image_inputs"] = {
        f"{block['chat_key']}/{m['id']}": m["image_context"]
        for m in packet["messages"] + packet["context"]
        if m.get("image_context")
    }
    return packet


def cached(store, db, block):
    """ADR 49 Q3: the same bounded inputs for an automatic block held under the
    Processing Character Threshold, from the local cache only, inside the
    caller's transaction: no Telegram read and no media preparation. Cached
    transcripts and image meanings stand in for prepared media; anything else
    stays an unanalysed attachment, and an uncached direct reply is left out.
    Returns None when the block cannot be judged from the cache."""
    import json

    key = block["chat_key"]

    def message(identity):
        row = db.execute(
            "SELECT body FROM messages WHERE chat_key=? AND message_id=?",
            (key, identity),
        ).fetchone()
        return Message.model_validate_json(row["body"]) if row else None

    sources = [message(i) for i in json.loads(block["sources"])]
    oldest = store.clock() - WINDOW
    if (
        not sources
        or any(m is None or m.deleted or m.date < oldest for m in sources)
        or fingerprint(sources) != block["revision"]
        or sum(len(m.text.encode()) for m in sources) > TARGET_BYTES
    ):
        return None
    reply_id = next(
        (reply_target(m) for m in reversed(sources) if reply_target(m)), None
    )
    parent = message(reply_id) if reply_id else None
    if parent is not None and parent.deleted:
        parent = None
    since = min(store.clock(), sources[0].date) - WINDOW
    recent = [
        Message.model_validate_json(r["body"])
        for r in db.execute(
            "SELECT body FROM messages WHERE chat_key=? AND message_id<? AND date>=? AND deleted=0 ORDER BY message_id DESC LIMIT 20",
            (key, sources[0].message_id, since),
        )
    ]
    context = []
    used = 0
    for m in ([parent] if parent else []) + recent:
        if m.key in {n.key for n in sources + context}:
            continue
        size = len(str(projection(m)).encode())
        if used + size > CONTEXT_TOKENS:
            continue
        context.append(m)
        used += size

    def item(m):
        value = projection(m)
        row = db.execute(
            "SELECT body FROM derivatives WHERE source=? AND revision=?",
            (m.key, m.revision),
        ).fetchone()
        cache = json.loads(row["body"]) if row else {}
        if value["media"] == "image" and cache.get("image_context"):
            value["image_context"] = cache["image_context"]
        elif value["media"] in SPOKEN_MEDIA and (cache.get("transcript") or {}).get(
            "text"
        ):
            value["transcript"] = cache["transcript"]
        return value

    messages = [item(m) for m in sources]
    supporting = [item(m) for m in sorted(context, key=lambda m: m.message_id)]
    packet = dict(
        messages=messages,
        context=supporting,
        inputs={m.key: m.revision for m in sources + context},
        attribution_inputs={m.key: attribution(m) for m in sources + context},
        transient=[],
        transient_inputs={},
        direct_reply=parent.message_id if parent else None,
        fingerprint=block["revision"],
        transcript_inputs={
            f"{key}/{m['id']}": transcript_fingerprint(m["transcript"])
            for m in messages + supporting
            if m.get("transcript")
        },
        image_inputs={
            f"{key}/{m['id']}": m["image_context"]
            for m in messages + supporting
            if m.get("image_context")
        },
    )
    if len(json.dumps(packet, ensure_ascii=False).encode()) > PACKET_BYTES:
        return None
    return packet


def current(store, packet, *, db=None):
    import json

    def read(sql, args):
        return (
            [dict(row) for row in db.execute(sql, args)]
            if db
            else store.rows(sql, args)
        )

    for key, revision in packet["inputs"].items():
        rows = read("SELECT revision,deleted,body FROM messages WHERE key=?", (key,))
        # A single older exact reply may be transient, outside cache retention.
        if not rows and key not in packet.get("transient", []):
            return False
        if rows and (rows[0]["revision"] != revision or rows[0]["deleted"]):
            return False
        recorded = packet.get("attribution_inputs", {}).get(key)
        if rows and key in packet.get("transient", []):
            source = Message.model_validate_json(rows[0]["body"])
            digest = packet.get("transient_inputs", {}).get(key)
            if not digest or not proves(source, digest, recorded):
                return False
        if rows and recorded is not None:
            # Filling in unknown attribution keeps work current (ADR 27).
            if not attribution_current(recorded, json.loads(rows[0]["body"])):
                return False
    for key, digest in packet.get("transcript_inputs", {}).items():
        rows = read("SELECT body FROM derivatives WHERE source=?", (key,))
        transcript = json.loads(rows[0]["body"]).get("transcript") if rows else None
        if transcript_fingerprint(transcript) != digest:
            return False
    for key, text in packet.get("image_inputs", {}).items():
        rows = read("SELECT body FROM derivatives WHERE source=?", (key,))
        current = json.loads(rows[0]["body"]).get("image_context") if rows else None
        if text != current:
            return False
    return True


def cached_recent(store, key, *, before=None, reply_id=None, db=None):
    """At most 20 recent cached messages and one cached direct reply.

    Explicit suggestions and arrival screening use this snapshot without
    fetching history or preparing media. The entire supporting input, including
    cached media meanings, fits the usual conservative 6,000-byte bound.
    Dependencies let callers discard a result if any used input changes.
    """
    import json

    def read(sql, args):
        return list(db.execute(sql, args)) if db else store.rows(sql, args)

    rows = read(
        "SELECT body FROM messages WHERE chat_key=? AND deleted=0 AND date>=? "
        "AND (? IS NULL OR message_id<?) ORDER BY message_id DESC LIMIT 20",
        (key, store.clock() - WINDOW, before, before),
    )
    recent = [Message.model_validate_json(r["body"]) for r in rows]
    if reply_id:
        parent = read(
            "SELECT body FROM messages WHERE chat_key=? AND message_id=? AND deleted=0",
            (key, reply_id),
        )
        if parent:
            recent.insert(0, Message.model_validate_json(parent[0]["body"]))
    selected, used, seen = [], 0, set()
    for source in recent:
        if source.key in seen:
            continue
        seen.add(source.key)
        item = projection(source)
        cached = read(
            "SELECT body FROM derivatives WHERE source=? AND revision=?",
            (source.key, source.revision),
        )
        derivative = json.loads(cached[0]["body"]) if cached else {}
        if item["media"] == "image" and derivative.get("image_context"):
            item["image_context"] = derivative["image_context"]
        if item["media"] in SPOKEN_MEDIA and derivative.get("transcript"):
            item["transcript"] = derivative["transcript"]
        size = len(json.dumps(item, ensure_ascii=False).encode())
        if used + size > CONTEXT_TOKENS:
            continue
        selected.append((source, item))
        used += size
    selected.sort(key=lambda pair: pair[0].message_id)
    return {
        "messages": [item for _, item in selected],
        "inputs": {source.key: source.revision for source, _ in selected},
        "attribution_inputs": {
            source.key: attribution(source) for source, _ in selected
        },
        "transcript_inputs": {
            source.key: transcript_fingerprint(item["transcript"])
            for source, item in selected
            if item.get("transcript")
        },
        "image_inputs": {
            source.key: item["image_context"]
            for source, item in selected
            if item.get("image_context")
        },
    }
