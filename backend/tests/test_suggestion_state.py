"""S1: source-bound Response Suggestions state and prepared context (ADR 25).

Authored messages only. Records are prepared directly from the real incoming
context builder; generation and scheduling belong to later units.
"""

import json

import pytest
from enloq.application.context import incoming
from enloq.domain.emotion import CODES
from enloq.domain.models import Conflict
from enloq.domain.suggestions import (
    EXPIRY,
    QUALIFYING,
    ResponseSuggestions,
    SuggestionOption,
)
from enloq.inference.openrouter import ImageContext
from enloq.storage.store import RETENTION, SCHEMA_VERSION

from backend.tests.test_emotion import KEY
from backend.tests.test_rebuild import fixture, image_bytes, message

OPTIONS = [
    SuggestionOption(purpose="acknowledge", text="That sounds like a lot today."),
    SuggestionOption(purpose="clarify", text="Do you want to talk it through?"),
]


def latest_block(app, key=None):
    return app.store.rows(
        "SELECT * FROM blocks WHERE chat_key=? ORDER BY date DESC,id DESC LIMIT 1",
        (key or f"{app.store.account_id}/user:42",),
    )[0]


async def prepare(
    app, *, code="worried", attempt="a" * 32, extra=None, fresh=(), useful=True
):
    block = latest_block(app)
    packet = await incoming(app, block)
    packet.update(extra or {})
    emotion = app.emotion.capture(block, packet, attempt)
    with app.store.transaction() as db:
        # The decision's yes or no, with its probability (ADR 56 Q19).
        usefulness = {True: 0.9, False: 0.1}.get(useful)
        app.emotion.publish(
            db,
            block["chat_key"],
            emotion,
            code,
            1,
            useful=useful,
            usefulness=usefulness,
        )
        record = app.suggestions.capture(db, block, packet, fresh, code, attempt)
        return app.suggestions.store_record(db, block["chat_key"], record)


def publish(app, attempt="a" * 32, options=OPTIONS, key=KEY):
    with app.store.transaction() as db:
        return app.suggestions.publish(db, key, attempt, options, 1)


async def ready(app, **kwargs):
    record = await prepare(app, **kwargs)
    assert publish(app, record["attempt"])
    return record


async def test_prepared_packet_excludes_memory_raw_media_and_guidance(tmp_path):
    app = fixture(tmp_path)
    app.transport.media["user:42", 2] = image_bytes()
    app.sync.ingest(message(app, 1, outgoing=True, text="Owner wording"))
    app.sync.ingest(message(app, 2, media="image", text=""))
    app.sync.ingest(message(app, 3, media="audio", text=""))
    app.sync.ingest(message(app, 4, text="Latest worried text"))
    with app.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (
                f"{KEY}/3",
                1,
                json.dumps({"transcript": {"text": "Spoken words", "language": "en"}}),
            ),
        )
    record = await prepare(
        app,
        extra={
            "memory": {"note": "Unverified", "entries": [{"text": "Memory fact"}]},
            "guidance": {"version": 9, "fields": {"incoming": "Owner text"}},
        },
        fresh=[ImageContext(message_id=2, text="A described synthetic image")],
    )
    text = json.dumps(record["packet"])
    for forbidden in ("Memory fact", "data_url", "base64", "Owner text", "memory"):
        assert forbidden not in text
    packet = record["packet"]
    assert [m["id"] for m in packet["messages"]] == [2, 3, 4]
    assert [m["id"] for m in packet["context"]] == [1]
    assert packet["context"][0]["outgoing"] is True
    by_id = {m["id"]: m for m in packet["messages"]}
    assert by_id[2]["image_context"] == "A described synthetic image"
    assert by_id[3]["transcript"]["text"] == "Spoken words"
    assert record["state"] == "pending" and record["emotion"] == "worried"
    assert set(record["inputs"]) == {f"{KEY}/{i}" for i in (1, 2, 3, 4)}


async def test_reads_never_call_transport_media_or_queue_work(tmp_path, monkeypatch):
    app = fixture(tmp_path)
    app.sync.ingest(message(app))
    record = await prepare(app)
    calls = []

    def forbidden(*args, **kwargs):
        calls.append(args)
        raise AssertionError("A suggestion read reached transport or media")

    for name in ("exact", "exact_many", "history", "download", "stream"):
        monkeypatch.setattr(app.transport, name, forbidden, raising=False)
    for name in ("enrich", "image", "transcript", "acquire"):
        monkeypatch.setattr(app.media, name, forbidden)
    jobs = app.store.rows("SELECT * FROM jobs")
    assert app.suggestions.view(KEY).reason == "cancelled"  # No job exists yet.
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO jobs VALUES (?,?,?,?,'queued',?,?,NULL)",
            (record["attempt"], KEY, "response_suggestions", record["source"], 0, "{}"),
        )
    assert app.suggestions.view(KEY).state == "preparing"
    publish(app)
    assert app.suggestions.view(KEY).state == "ready"
    assert not calls
    assert len(app.store.rows("SELECT * FROM jobs")) == len(jobs) + 1
    assert not app.store.rows("SELECT * FROM reservations")


@pytest.mark.parametrize("useful", [True, False])
@pytest.mark.parametrize("code", CODES)
async def test_a_set_qualifies_when_its_block_is_emotional_or_useful(
    tmp_path, code, useful
):
    # ADR 56: a usable non-neutral emotion or a yes on usefulness, unknown
    # emotion included.
    app = fixture(tmp_path)
    app.sync.ingest(message(app))
    await ready(app, code=code, useful=useful)
    view = app.suggestions.view(KEY)
    if code in QUALIFYING or useful:
        assert view.state == "ready" and [o.text for o in view.options] == [
            o.text for o in OPTIONS
        ]
    else:
        assert (view.state, view.reason, view.options) == ("none", "not_qualifying", [])


@pytest.mark.parametrize("useful", [False, None])
async def test_a_calm_set_shows_and_inserts_only_while_its_decision_found_it_useful(
    tmp_path, useful
):
    # ADR 49 Q2, kept by ADR 56 for blocks without a non-neutral emotion;
    # None is a record published before the question existed.
    app = fixture(tmp_path)
    app.sync.ingest(message(app))
    record = await ready(app, code="neutral", useful=useful)
    view = app.suggestions.view(KEY)
    assert (view.state, view.reason, view.options) == ("none", "not_qualifying", [])
    draft = app.store.draft(KEY)
    with pytest.raises(Conflict, match="Suggestions changed"):
        app.suggestions.insert(KEY, draft["revision"], record["attempt"], 0)


async def test_twelve_hour_source_time_edge_survives_restart(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app))
    record = await ready(app)
    now[0] = record["source_time"] + EXPIRY - 0.01
    restarted = fixture(tmp_path, clock=lambda: now[0])
    view = restarted.suggestions.view(KEY)
    assert view.state == "ready" and view.expires_at == record["source_time"] + EXPIRY
    now[0] += 0.01
    assert restarted.suggestions.view(KEY).reason == "expired"
    # Late publication cannot restore an expired set either.
    assert not publish(restarted)


async def test_the_owners_own_reaction_answers_a_block_for_good(tmp_path):
    # ADR 49 Q12: any own reaction, from any client, on one of its messages.
    from backend.tests.test_reactions import snapshot

    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app, 1, text="Can you send the photos?"))
    app.sync.ingest(message(app, 2, text="When you can."))
    record = await ready(app)
    assert record["sources"] == [1, 2]
    # A minimal snapshot does not say whose reactions they are.
    now[0] += 5
    app.sync.reactions(
        "user:42", 1, snapshot(own=("👍",), observed=now[0], minimal=True)
    )
    assert app.suggestions.view(KEY).state == "ready"
    now[0] += 5
    app.sync.reactions("user:42", 1, snapshot(own=("❤",), observed=now[0]))
    assert app.suggestions.view(KEY).reason == "answered"
    # Removing it revives nothing, not even after a restart.
    now[0] += 5
    app.sync.reactions("user:42", 1, snapshot(observed=now[0]))
    restarted = fixture(tmp_path, clock=lambda: now[0])
    assert restarted.suggestions.view(KEY).reason == "answered"
    draft = restarted.store.draft(KEY)
    with pytest.raises(Conflict, match="Suggestions changed"):
        restarted.suggestions.insert(KEY, draft["revision"], record["attempt"], 0)


async def test_an_earlier_own_reaction_leaves_later_messages_unanswered(tmp_path):
    from backend.tests.test_reactions import snapshot

    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app, 1, text="Look at this!"))
    now[0] += 5
    app.sync.reactions("user:42", 1, snapshot(own=("😂",), observed=now[0]))
    now[0] += 5
    # A later message in the same block was not answered by that reaction.
    app.sync.ingest(message(app, 2, text="And are you coming tonight?", date=now[0]))
    record = await ready(app)
    assert record["sources"] == [1, 2]
    assert app.suggestions.view(KEY).state == "ready"


@pytest.mark.parametrize(
    "change,reason",
    [
        ("new_incoming", "replaced"),
        ("new_incoming_deleted", "replaced"),
        ("external_outgoing", "answered"),
        ("app_outgoing", "answered"),
        ("outgoing_deleted", "answered"),
        ("target_edit", "replaced"),
        ("target_delete", "replaced"),
        ("context_edit", "changed"),
        ("context_delete", "changed"),
        ("transcript", "changed"),
        ("image", "changed"),
        ("attribution", "changed"),
    ],
)
async def test_withdrawal_never_revives_older_sets(tmp_path, change, reason):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1, outgoing=True, text="Owner context"))
    app.sync.ingest(message(app, 2, media="image", text=""))
    # A known non-forward: learning a forward header later is a conflicting
    # known value, unlike filling in unknown attribution (ADR 27).
    app.sync.ingest(message(app, 3, forwarded=False))
    with app.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (f"{KEY}/2", 1, json.dumps({"image_context": "Cached meaning"})),
        )
    await ready(app)
    assert app.suggestions.view(KEY).state == "ready"
    if change in ("new_incoming", "new_incoming_deleted"):
        app.sync.ingest(message(app, 4, text="Newer incoming"))
        if change == "new_incoming_deleted":
            app.sync.ingest(message(app, 4, revision=2, deleted=True))
    elif change in ("external_outgoing", "outgoing_deleted"):
        app.sync.ingest(message(app, 4, outgoing=True, text="Sent elsewhere"))
        if change == "outgoing_deleted":
            app.sync.ingest(message(app, 4, outgoing=True, revision=2, deleted=True))
    elif change == "app_outgoing":
        # Delivery applies its confirmed native message directly to the store.
        app.store.apply_message(message(app, 4, outgoing=True, text="Sent here"))
    elif change in ("target_edit", "target_delete"):
        app.sync.ingest(
            message(
                app, 3, revision=2, text="Edited", deleted=change == "target_delete"
            )
        )
    elif change in ("context_edit", "context_delete"):
        app.sync.ingest(
            message(
                app,
                1,
                outgoing=True,
                revision=2,
                text="Edited owner context",
                deleted=change == "context_delete",
            )
        )
    elif change == "transcript":
        with app.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
                (
                    f"{KEY}/1",
                    1,
                    json.dumps({"transcript": {"text": "New", "language": "en"}}),
                ),
            )
    elif change == "image":
        with app.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
                (f"{KEY}/2", 1, json.dumps({"image_context": "Replaced meaning"})),
            )
    elif change == "attribution":
        with app.store.transaction() as db:
            db.execute(
                "UPDATE messages SET body=json_set(body,'$.forwarded',json('true'),'$.forwarded_from','Authored origin') WHERE key=?",
                (f"{KEY}/3",),
            )
    view = app.suggestions.view(KEY)
    assert (view.state, view.reason, view.options) == ("none", reason, [])
    assert not publish(app)
    assert app.suggestions.view(KEY).reason == reason


async def test_account_and_chat_isolation(tmp_path):
    app = fixture(tmp_path)
    other_chat = "1/user:43"
    app.store.put_chat(
        app.store.chat(KEY).model_copy(update={"peer": "user:43", "title": "Other"})
    )
    app.sync.ingest(message(app))
    app.sync.ingest(message(app).model_copy(update={"peer": "user:43"}))
    await ready(app)
    assert app.suggestions.view(KEY).state == "ready"
    assert app.suggestions.view(other_chat).reason == "unavailable"
    with app.store.transaction() as db:
        assert not app.suggestions.publish(db, other_chat, "a" * 32, OPTIONS, 1)
    other = fixture(tmp_path, account=2)
    assert other.suggestions.view("2/user:42").reason == "unavailable"
    assert not other.store.rows("SELECT * FROM suggestion_state")


async def test_source_identity_reuses_unchanged_outcomes_only(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1, outgoing=True, text="Owner context"))
    app.sync.ingest(message(app, 2))
    first = await prepare(app, attempt="a" * 32)
    block = latest_block(app)
    packet = await incoming(app, block)
    with app.store.transaction() as db:
        again = app.suggestions.capture(db, block, packet, [], "sad", "b" * 32)
    # A reread, restart or unchanged retry has the same source identity.
    assert again["source"] == first["source"]
    existing = app.suggestions.record(KEY)
    for state in ("pending", "ready", "failed"):
        assert app.suggestions.reusable({**existing, "state": state}, again)
    assert not app.suggestions.reusable({**existing, "state": "cancelled"}, again)
    publish(app, options=[])
    assert app.suggestions.view(KEY).reason == "empty"
    assert app.suggestions.reusable(app.suggestions.record(KEY), again)
    app.sync.ingest(message(app, 1, outgoing=True, revision=2, text="Edited context"))
    block = latest_block(app)
    packet = await incoming(app, block)
    with app.store.transaction() as db:
        changed = app.suggestions.capture(db, block, packet, [], "sad", "c" * 32)
    assert changed["source"] != first["source"]
    assert not app.suggestions.reusable(app.suggestions.record(KEY), changed)


@pytest.mark.parametrize(
    "job,expected",
    [
        ("queued", ("pending", None)),
        ("claimed", ("pending", None)),
        ("running", ("failed", "interrupted")),
        (None, ("cancelled", None)),
    ],
)
async def test_restart_recovery_never_resamples_dispatched_work(
    tmp_path, job, expected
):
    app = fixture(tmp_path)
    app.sync.ingest(message(app))
    record = await prepare(app)
    if job:
        with app.store.transaction() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,NULL)",
                (
                    record["attempt"],
                    KEY,
                    "response_suggestions",
                    record["source"],
                    job,
                    0,
                    "{}",
                ),
            )
    app.store.recover()
    stored = app.suggestions.record(KEY)
    assert (stored["state"], stored["failure"]) == expected
    assert ("packet" in stored) == (expected[0] == "pending")
    view = app.suggestions.view(KEY)
    if expected[0] == "failed":
        assert (view.state, view.reason) == ("failed", "interrupted")
    elif expected[0] == "pending":
        assert view.state == "preparing"


async def test_expiry_drops_prepared_content_then_the_record(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app))
    record = await prepare(app)
    app.store.expire()
    assert "packet" in app.suggestions.record(KEY)
    now[0] = record["source_time"] + EXPIRY
    app.store.expire()
    stored = app.suggestions.record(KEY)
    assert "packet" not in stored and stored["state"] == "cancelled"
    now[0] = record["source_time"] + RETENTION + 1
    app.store.expire()
    assert not app.store.rows("SELECT * FROM suggestion_state")


async def test_oversized_prepared_input_fails_without_retaining_it(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app))
    huge = [ImageContext(message_id=1, text="x" * 2000)]
    block = latest_block(app)
    packet = await incoming(app, block)
    packet["context"] = [dict(packet["messages"][0], id=n) for n in range(2, 40)]
    for item in packet["context"]:
        item["text"] = "y" * 1500
    with app.store.transaction() as db:
        record = app.suggestions.capture(db, block, packet, huge, "sad", "a" * 32)
        stored = app.suggestions.store_record(db, KEY, record)
    assert (stored["state"], stored["failure"]) == ("failed", "too_large")
    assert "packet" not in app.suggestions.record(KEY)


def test_output_contract_allows_empty_and_rejects_padding_or_blank_options():
    assert ResponseSuggestions.model_validate({"options": []}).options == []
    # No character limit (ADR 25 Q1).
    long = {"options": [{"purpose": "support", "text": "x" * 5000}]}
    assert len(ResponseSuggestions.model_validate(long).options[0].text) == 5000
    for body in (
        {"options": [{"purpose": "support", "text": " "}]},
        {"options": [{"purpose": "advice", "text": "Do this"}]},
        {"options": [{"purpose": "support", "text": "Same"}] * 2},
        {"options": [{"purpose": "support", "text": f"{n}"} for n in range(4)]},
        {"options": [], "extra": True},
    ):
        with pytest.raises(ValueError):
            ResponseSuggestions.model_validate(body)


def option(purpose, text=None):
    return {"purpose": purpose, "text": text or f"Authored {purpose} wording."}


@pytest.mark.parametrize(
    "purposes",
    [
        ("accept", "decline"),
        ("decline", "later"),
        ("accept", "decline", "later"),
        ("accept", "later", "clarify"),
        ("acknowledge", "clarify"),
        # Q20: a deferral commits to nothing and may stand alone.
        ("later", "clarify"),
    ],
)
def test_mixed_answer_stances_remain_valid(purposes):
    body = {"options": [option(p) for p in purposes]}
    assert [o.purpose for o in ResponseSuggestions.model_validate(body).options] == [
        *purposes
    ]


def test_deferrals_alone_are_not_one_sided():
    # ADR 49 Q20: two "let me check" replies to a factual question.
    body = {
        "options": [
            option("later", "Let me check and tell you."),
            option("later", "Good question, I'll find out."),
        ]
    }
    assert len(ResponseSuggestions.model_validate(body).options) == 2


@pytest.mark.parametrize(
    "options",
    [
        [option("accept")],
        [option("accept"), option("clarify")],
        [option("accept", "Yes, I'll be there."), option("accept", "Sure, see you.")],
        [option("decline"), option("acknowledge"), option("support")],
    ],
)
def test_a_one_sided_answer_set_is_valid(options):
    assert ResponseSuggestions.model_validate({"options": options}).model_dump() == {
        "options": options
    }


async def test_upgrade_from_version_23_is_additive(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app))
    app.store.mutate_draft(KEY, 0, lambda d: d.update(text="Keep authored words"))
    with app.store.transaction() as db:
        db.execute("DROP TABLE suggestion_state")
        db.execute("PRAGMA user_version=23")
    reopened = fixture(tmp_path)
    assert (
        reopened.store.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    )
    assert reopened.suggestions.view(KEY).reason == "unavailable"
    assert reopened.store.draft(KEY)["text"] == "Keep authored words"
    await ready(reopened)
    assert reopened.suggestions.view(KEY).state == "ready"
