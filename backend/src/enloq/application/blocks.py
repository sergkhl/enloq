"""Stable native membership and coalesced work; time alone never regenerates text."""

import json
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain.models import ANALYSED_MEDIA, Message
from ..domain.source_revision import fingerprint, proves
from ..storage.accounts import DEFAULT_PROCESSING_THRESHOLD
from ..storage.store import WINDOW
from .capacity import RANK, rank_sql

# Blocks that failed before dispatch only because they held non-AI media (Q21).
UNSUPPORTED_FAILURE = "This media needs supported local preparation"
GROUPING_VERSION = "calendar-day-v1"
SOURCE_REVISIONS_VERSION = "local-revisions-v1"


def processing_threshold(store, db):
    metadata = "shared.metadata" if store.shared_path else "metadata"
    row = db.execute(
        f"SELECT value FROM {metadata} WHERE key='processing_threshold'"
    ).fetchone()
    return int(row[0]) if row else DEFAULT_PROCESSING_THRESHOLD


def original_characters(messages):
    """Whole-target Unicode code points; added/context/derived text never counts."""
    return sum(len(message.text.strip()) for message in messages if not message.deleted)


def text_rewriting(store, db):
    """ADR 54: whether incoming text is rewritten automatically (the
    registry's text_rewriting, off unless set)."""
    if store.rewrites_text is not None:
        return store.rewrites_text
    metadata = "shared.metadata" if store.shared_path else "metadata"
    row = db.execute(
        f"SELECT value FROM {metadata} WHERE key='text_rewriting'"
    ).fetchone()
    return bool(row and row[0] == "1")


def analysed(messages):
    """Whether a target has media the rewrite reads: an image, a voice note
    or a video circle."""
    return any(
        not message.deleted and message.media in ANALYSED_MEDIA for message in messages
    )


def character_admitted(messages, minimum, text=True):
    """Analysed media is always admitted. Text is admitted from the threshold
    on, and with text rewriting off (ADR 54) never: it stays combined, and
    only calming rewrites it."""
    messages = tuple(messages)
    return analysed(messages) or (text and original_characters(messages) >= minimum)


def cached_sources(db, block):
    """Only a complete, current cached membership can establish admission."""
    sources = [
        Message.model_validate_json(row["body"])
        for row in db.execute(
            "SELECT body FROM messages WHERE chat_key=? "
            "AND message_id IN (SELECT value FROM json_each(?)) ORDER BY message_id",
            (block["chat_key"], block["sources"]),
        )
    ]
    if (
        not sources
        or [source.message_id for source in sources] != json.loads(block["sources"])
        or any(
            source.deleted or source.outgoing or source.service for source in sources
        )
        or fingerprint(sources) != block["revision"]
    ):
        return None
    return sources


def automatic(job):
    """Work the threshold may hold: not an explicit retry, an older run or a
    held block released for calming at any length (ADR 53 Q6)."""
    payload = json.loads(job["payload"] or "{}")
    return not (
        payload.get("explicit_retry")
        or payload.get("class") == "older"
        or "calm" in payload
    )


def defer(db, scope, revision, due, payload):
    db.execute(
        "INSERT OR REPLACE INTO incoming_deferred VALUES (?,?,?,?)",
        (scope, revision, due, payload),
    )
    # A preparation still in flight must never reuse this attempt after release.
    db.execute(
        "UPDATE jobs SET state='superseded',error=NULL WHERE scope=? "
        "AND role='incoming_rewrite' AND input_revision=? "
        "AND state IN ('queued','claimed') "
        "AND NOT coalesce(json_extract(payload,'$.explicit_retry'),0) "
        "AND coalesce(json_extract(payload,'$.class'),'new')!='older' "
        "AND json_type(payload,'$.calm') IS NULL",
        (scope, revision),
    )
    db.execute(
        "UPDATE blocks SET state='original',error=NULL,stage=NULL,"
        "rendered=CASE WHEN rendered_revision=revision THEN rendered ELSE NULL END "
        "WHERE id=? AND revision=? AND state IN ('queued','working')",
        (scope, revision),
    )
    key = "run:" + scope.rsplit("/", 1)[0]
    run = db.execute("SELECT value FROM cursors WHERE key=?", (key,)).fetchone()
    if run:
        value = json.loads(run["value"])
        if scope in value["scopes"]:
            value["scopes"].remove(scope)
            db.execute(
                "UPDATE cursors SET value=? WHERE key=?", (json.dumps(value), key)
            )


def reconcile_admission(store):
    """Recheck pending automatic work and held memberships without native reads.

    Successful caches and unchanged failed attempts are absent from this pass.
    Running calls retain their publication guards and may finish after a Save.
    Held eligibility has no runnable job, worker claim or memory-idle footprint.
    """
    with store.transaction() as db:
        minimum = processing_threshold(store, db)
        text = text_rewriting(store, db)
        for job in db.execute(
            "SELECT * FROM jobs WHERE role='incoming_rewrite' "
            "AND state IN ('queued','claimed') ORDER BY rowid"
        ).fetchall():
            if not automatic(job):
                continue
            block = db.execute(
                "SELECT * FROM blocks WHERE id=?", (job["scope"],)
            ).fetchone()
            if not block or block["revision"] != job["input_revision"]:
                continue
            sources = cached_sources(db, block)
            if sources and not character_admitted(sources, minimum, text):
                defer(
                    db, job["scope"], job["input_revision"], job["due"], job["payload"]
                )
        for waiting in db.execute("SELECT * FROM incoming_deferred").fetchall():
            block = db.execute(
                "SELECT * FROM blocks WHERE id=?", (waiting["scope"],)
            ).fetchone()
            if (
                not block
                or block["revision"] != waiting["input_revision"]
                or block["state"] != "original"
            ):
                db.execute(
                    "DELETE FROM incoming_deferred WHERE scope=?", (waiting["scope"],)
                )
                continue
            sources = cached_sources(db, block)
            chat = db.execute(
                "SELECT * FROM chats WHERE key=?", (block["chat_key"],)
            ).fetchone()
            body = json.loads(chat["body"]) if chat else {}
            if (
                not sources
                or not body.get("ai_eligible")
                or body.get("kind") != "private"
                or chat["activity"] < store.clock() - WINDOW
                or any(source.date < store.clock() - WINDOW for source in sources)
                or not rewritable(sources)
            ):
                db.execute(
                    "DELETE FROM incoming_deferred WHERE scope=?", (waiting["scope"],)
                )
                continue
            if character_admitted(sources, minimum, text):
                payload = json.loads(waiting["payload"])
                # A held decision is not this rewrite's (ADR 49 Q3); once
                # released, the block is classified after its rewrite instead.
                payload.pop("decided", None)
                db.execute(
                    "UPDATE jobs SET state='superseded',error='The held block was admitted for rewriting' "
                    "WHERE role='emotion_classification' AND scope=? AND state='queued' "
                    "AND json_extract(payload,'$.held')=1 "
                    "AND json_extract(payload,'$.block')=?",
                    (block["chat_key"], block["id"]),
                )
                enqueue(
                    db,
                    waiting["scope"],
                    waiting["input_revision"],
                    waiting["due"],
                    klass=payload.get("class", "new"),
                    payload=payload,
                )
                db.execute(
                    "UPDATE blocks SET state='queued',error=NULL,stage=NULL WHERE id=?",
                    (waiting["scope"],),
                )


def rewritable(messages):
    """Whether any target can become model input: text or analysable media."""
    return any(
        not m.deleted and (m.text.strip() or m.media in ANALYSED_MEDIA)
        for m in messages
    )


def rebuild(store, chat_key, changed=None, *, target=True, klass=None):
    # Change-driven work is new; cached unread reconciliation is catch-up.
    klass = klass or ("new" if changed else "catchup")
    chat = store.chat(chat_key)
    messages = [
        Message.model_validate_json(r["body"])
        for r in store.rows(
            "SELECT body FROM messages WHERE chat_key=? ORDER BY message_id",
            (chat_key,),
        )
    ]
    timezone = ZoneInfo(store.timezone)
    groups = []
    current = []
    for m in messages:
        if m.deleted:
            continue
        if m.outgoing or m.service:
            if current:
                groups.append(current)
            current = []
            continue
        if (
            current
            and datetime.fromtimestamp(m.date, timezone).date()
            != datetime.fromtimestamp(current[-1].date, timezone).date()
        ):
            groups.append(current)
            current = []
        current.append(m)
    if current:
        groups.append(current)
    admitted = chat.kind == "private" and chat.ai_eligible
    identities = set()
    with store.transaction() as db:
        previous_blocks = list(
            db.execute("SELECT * FROM blocks WHERE chat_key=?", (chat_key,))
        )
        previous_sources = {
            row["id"]: json.loads(row["sources"]) for row in previous_blocks
        }
        previous_waits = {
            row["scope"]: row["due"]
            for row in db.execute(
                "SELECT scope,max(due) AS due FROM jobs WHERE role='incoming_rewrite' AND state='queued' AND scope IN (SELECT id FROM blocks WHERE chat_key=?) GROUP BY scope",
                (chat_key,),
            )
        }
        deferred = {
            row["scope"]: row
            for row in db.execute(
                "SELECT * FROM incoming_deferred WHERE scope IN "
                "(SELECT id FROM blocks WHERE chat_key=?)",
                (chat_key,),
            )
        }
        previous_waits.update({scope: row["due"] for scope, row in deferred.items()})
        minimum = processing_threshold(store, db)
        text = text_rewriting(store, db)
        for group in groups:
            sources = [m.message_id for m in group]
            related = [
                row
                for row in previous_blocks
                if set(previous_sources[row["id"]]).intersection(sources)
            ]
            # Retain exact membership first, including an identity whose first
            # native source was deleted earlier. Splits reuse an old identity
            # only once; merges inherit processing from every related block.
            old = next(
                (
                    row
                    for row in related
                    if row["id"] not in identities
                    and previous_sources[row["id"]] == sources
                ),
                None,
            )
            if old is None:
                old = next(
                    (row for row in related if row["id"] not in identities), None
                )
            identity = old["id"] if old else group[0].key
            identities.add(identity)
            revision = fingerprint(group)
            source_revisions = json.dumps({m.message_id: m.revision for m in group})
            membership_unchanged = bool(old and previous_sources[old["id"]] == sources)
            affected = (
                changed.message_id in sources
                if changed
                else any(i > chat.read_through for i in sources)
            )
            referenced = False
            if old:
                previous = db.execute(
                    "SELECT payload FROM jobs WHERE scope=? ORDER BY due DESC LIMIT 1",
                    (identity,),
                ).fetchone()
                if previous and changed:
                    payload = json.loads(previous[0])
                    inputs = payload.get("inputs", {})
                    # An exact reply can become cached later without changing
                    # the source version already used by a completed rewrite.
                    # Mere cache admission is not a changed model dependency.
                    referenced = changed.key in inputs
                    if referenced and not changed.deleted:
                        digest = payload.get("transient_inputs", {}).get(changed.key)
                        if (
                            inputs[changed.key] == changed.revision
                            and digest
                            and proves(
                                changed,
                                digest,
                                payload.get("attribution_inputs", {}).get(changed.key),
                            )
                        ):
                            referenced = False
            catch_up = (
                changed is None
                and affected
                and (not old or old["state"] == "original")
                and not (old and identity in deferred and old["revision"] == revision)
            )
            if old and old["revision"] == revision and not referenced and not catch_up:
                if old["source_revisions"] != source_revisions:
                    db.execute(
                        "UPDATE blocks SET source_revisions=? WHERE id=?",
                        (source_revisions, identity),
                    )
                continue
            content = rewritable(group)
            # Never queue an already-read initial block just because it was cached.
            automatic_work = (
                admitted
                and content
                and all(m.date >= store.clock() - WINDOW for m in group)
                and (
                    (target and affected)
                    or referenced
                    or any(row["id"] in deferred for row in related)
                    or any(
                        row["state"] not in ("original", "nothing") for row in related
                    )
                )
            )
            should_queue = automatic_work and character_admitted(group, minimum, text)
            # A changed block that is not re-queued (e.g. an explicitly processed
            # block older than seven days) withdraws its stale rendering.
            keep = (
                old
                and membership_unchanged
                and content
                and not referenced
                and not (changed and changed.deleted)
                and (should_queue or old["revision"] == revision)
            )
            state = "queued" if should_queue else "original" if content else "nothing"
            due = (store.clock() if changed else group[-1].date) + 30
            if automatic_work and not changed and not membership_unchanged:
                # Conversion does not invent an arrival, and it must not
                # shorten an already-admitted source's quiet/backoff wait.
                due = max(
                    [due, *(previous_waits.get(row["id"], due) for row in related)]
                )
            db.execute(
                "INSERT OR REPLACE INTO blocks (id,chat_key,revision,sources,rendered,state,error,date,unanalysed,rendered_revision,guidance,source_revisions,calmed) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    identity,
                    chat_key,
                    revision,
                    json.dumps(sources),
                    old["rendered"] if keep else None,
                    state,
                    None,
                    group[-1].date,
                    old["unanalysed"] if keep else None,
                    old["rendered_revision"] if keep else None,
                    old["guidance"] if keep else None,
                    source_revisions,
                    old["calmed"] if keep else 0,
                ),
            )
            db.execute(
                "UPDATE jobs SET state='superseded' WHERE scope=? AND state='queued'",
                (identity,),
            )
            if should_queue:
                enqueue(
                    db,
                    identity,
                    revision,
                    due,
                    klass=klass,
                )
            elif automatic_work:
                defer(db, identity, revision, due, json.dumps({"class": klass}))
        for row in db.execute(
            "SELECT id FROM blocks WHERE chat_key=?", (chat_key,)
        ).fetchall():
            if row[0] not in identities:
                db.execute("DELETE FROM blocks WHERE id=?", (row[0],))
                db.execute(
                    "UPDATE jobs SET state='superseded' WHERE scope=? AND state='queued'",
                    (row[0],),
                )


def regroup_cached(store):
    """Convert only cached projections once under the configured day rule.

    Each chat is committed independently. An interrupted pass safely resumes:
    rebuilt memberships retain their revision/job, and the marker is written
    only after every cached chat has converted. Native viewing, derivatives,
    memory, drafts and accounting are untouched; no transport is consulted.
    Queued work still passes the runtime's processing, role and budget gates.
    """
    marker = store.rows("SELECT value FROM metadata WHERE key='incoming_grouping'")
    if not marker or marker[0]["value"] != GROUPING_VERSION:
        for chat in store.rows("SELECT key FROM chats ORDER BY key"):
            rebuild(store, chat["key"], klass="catchup")
        with store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('incoming_grouping',?)",
                (GROUPING_VERSION,),
            )
    hydrate_source_revisions(store)


def hydrate_source_revisions(store):
    """One metadata-only upgrade of every cached group, including excluded chats.

    Existing membership/output stays authoritative: snapshots are hydrated only
    when the current cached sources still prove its fingerprint. A stale group
    stays unavailable to source-page reads until ordinary rebuilding replaces
    it. Per-group commits and a final marker make interruption safe; reading
    pages never performs this upgrade or starts regeneration.
    """
    marker = store.rows(
        "SELECT value FROM metadata WHERE key='incoming_source_revisions'"
    )
    if marker and marker[0]["value"] == SOURCE_REVISIONS_VERSION:
        return
    for identity in store.rows(
        "SELECT id FROM blocks WHERE source_revisions IS NULL ORDER BY chat_key,id"
    ):
        with store.transaction() as db:
            block = db.execute(
                "SELECT * FROM blocks WHERE id=? AND source_revisions IS NULL",
                (identity["id"],),
            ).fetchone()
            if not block:
                continue
            sources = json.loads(block["sources"])
            revisions = {}
            valid = True

            def current_sources():
                nonlocal valid
                for offset in range(0, len(sources), 100):
                    rows = db.execute(
                        "SELECT body FROM messages WHERE chat_key=? "
                        "AND message_id IN (SELECT value FROM json_each(?)) "
                        "ORDER BY message_id",
                        (block["chat_key"], json.dumps(sources[offset : offset + 100])),
                    ).fetchall()
                    for row in rows:
                        message = Message.model_validate_json(row["body"])
                        valid = valid and (
                            message.key == f"{block['chat_key']}/{message.message_id}"
                            and not message.deleted
                            and not message.outgoing
                            and not message.service
                        )
                        revisions[message.message_id] = message.revision
                        yield message

            revision = fingerprint(current_sources())
            if (
                valid
                and len(revisions) == len(sources)
                and revision == block["revision"]
            ):
                db.execute(
                    "UPDATE blocks SET source_revisions=? WHERE id=?",
                    (json.dumps(revisions), block["id"]),
                )
    with store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO metadata VALUES ('incoming_source_revisions',?)",
            (SOURCE_REVISIONS_VERSION,),
        )


def enqueue(db, scope, revision, due, *, klass="new", payload=None):
    """Queue an incoming rewrite in a priority class (see capacity.CLASSES)."""
    track(db, scope)
    db.execute("DELETE FROM incoming_deferred WHERE scope=?", (scope,))
    identity = uuid.uuid4().hex
    db.execute(
        "INSERT INTO jobs VALUES (?,?,?,?,'queued',?,?,NULL)",
        (
            identity,
            scope,
            "incoming_rewrite",
            revision,
            due,
            json.dumps({**(payload or {}), "class": klass}),
        ),
    )
    return identity


def track(db, scope):
    """A chat's current run: every block queued since the chat was last idle,
    so progress reads "Processing N of M"."""
    key = "run:" + scope.rsplit("/", 1)[0]
    row = db.execute("SELECT value FROM cursors WHERE key=?", (key,)).fetchone()
    scopes = json.loads(row[0])["scopes"] if row else []
    if (
        scopes
        and not db.execute(
            "SELECT count(*) FROM blocks WHERE id IN (SELECT value FROM json_each(?)) AND state IN ('queued','working')",
            (json.dumps(scopes),),
        ).fetchone()[0]
    ):
        scopes = []  # The previous run finished.
    if scope not in scopes:
        scopes.append(scope)
    db.execute(
        "INSERT OR REPLACE INTO cursors VALUES (?,?)",
        (key, json.dumps({"scopes": scopes})),
    )


def progress(store, chat_key):
    """The chat's current run. It is explicit while pending work includes the
    chat-level older run, which ADR 28 names Rewriting earlier messages."""
    rows = store.rows("SELECT value FROM cursors WHERE key=?", ("run:" + chat_key,))
    scopes = json.loads(rows[0]["value"])["scopes"] if rows else []
    if not scopes:
        return None
    pending = store.rows(
        "SELECT count(*) AS n FROM blocks WHERE id IN (SELECT value FROM json_each(?)) AND state IN ('queued','working')",
        (json.dumps(scopes),),
    )[0]["n"]
    if not pending:
        return None
    explicit = store.rows(
        "SELECT 1 FROM jobs WHERE role='incoming_rewrite' "
        "AND state IN ('queued','claimed','running') "
        f"AND {rank_sql()}=? AND scope IN (SELECT value FROM json_each(?)) LIMIT 1",
        (RANK["older"], json.dumps(scopes)),
    )
    return {
        "done": len(scopes) - pending,
        "total": len(scopes),
        "explicit": bool(explicit),
    }


def reclassify(store, chat_key, identity):
    """A metadata refresh may reveal what a legacy item is. It never changes a
    revision or queues work; it only moves a block into or out of Nothing."""
    for row in store.rows(
        "SELECT id,sources,state FROM blocks WHERE chat_key=? AND state IN ('original','failed','nothing')",
        (chat_key,),
    ):
        sources = json.loads(row["sources"])
        if identity not in sources:
            continue
        content = rewritable(
            filter(None, (store.message(chat_key, i) for i in sources))
        )
        if row["state"] == "nothing" and content:
            state = "original"
        elif row["state"] != "nothing" and not content:
            state = "nothing"
        else:
            continue
        with store.transaction() as db:
            db.execute(
                "UPDATE blocks SET state=?,error=NULL,rendered=NULL WHERE id=? AND state=?",
                (state, row["id"], row["state"]),
            )
            db.execute(
                "UPDATE jobs SET state='superseded' WHERE scope=? AND state='queued'",
                (row["id"],),
            )


def requeue_unsupported(store, eligible):
    """Q21: blocks that failed before dispatch only on non-AI media re-queue
    once, when still inside the processing window. Nothing reached a provider."""
    for row in store.rows(
        "SELECT * FROM blocks WHERE state='failed' AND error=?", (UNSUPPORTED_FAILURE,)
    ):
        jobs = store.rows(
            "SELECT id,payload FROM jobs WHERE scope=? AND role='incoming_rewrite'",
            (row["id"],),
        )
        if any(json.loads(j["payload"]).get("requeued") for j in jobs) or any(
            store.rows("SELECT id FROM reservations WHERE id=?", (j["id"],))
            for j in jobs
        ):
            continue
        if not eligible(row):
            continue
        with store.transaction() as db:
            enqueue(
                db,
                row["id"],
                row["revision"],
                store.clock(),
                klass="catchup",
                payload={"requeued": "unsupported"},
            )
            db.execute(
                "UPDATE blocks SET state='queued',error=NULL WHERE id=? AND state='failed'",
                (row["id"],),
            )


def reconcile_unread(store):
    """Reconcile only cached recent unread targets, independently of native cursors."""
    for chat in store.recent_chats():
        if chat["ai_eligible"]:
            rebuild(store, chat["key"])
