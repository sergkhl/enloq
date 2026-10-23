"""Authored cached group sources exercise bounds without a native/model call."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.application import group_context as context
from enloq.domain.models import Chat, Message, Refused
from enloq.storage.store import RETENTION, Store

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC).timestamp()
KEY = "1/channel:42"
DAY = "2026-10-11"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "account.sqlite3", account_id=1, clock=lambda: NOW)
    store.put_chat(
        Chat(account_id=1, peer="channel:42", title="Authored group", kind="group")
    )
    store.groups.observe(KEY, access="available", reason="authored_native")
    return store


def source(store, identity, **values):
    message = Message(
        account_id=1,
        peer="channel:42",
        message_id=identity,
        revision=0,
        date=NOW,
        text="Authored participant post",
        author="Alex",
        author_peer="user:7",
    ).model_copy(update=values)
    store.apply_message(message, group_epoch=store.groups.intake(KEY).epoch)
    # The sync owner clears this marker after its durable source-change effects.
    # This pure context fixture has no running pipeline or readiness effects.
    with store.transaction() as db:
        db.execute("DELETE FROM cursors WHERE key=?", ("group-effects:" + message.key,))
    return store.message(KEY, identity)


def change_body(store, message, **values):
    value = message.model_copy(update=values)
    with store.transaction() as db:
        db.execute(
            "UPDATE messages SET body=? WHERE key=?",
            (value.model_dump_json(), value.key),
        )


def derivative(store, message, **values):
    with store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (message.key, message.revision, json.dumps(values)),
        )


def test_eligibility_includes_owner_informative_bot_and_captions_not_service(store):
    incoming = source(store, 1, text="  Привет 😀  ")
    own = source(store, 2, outgoing=True, author_peer="user:99", text="  own  ")
    bot = source(store, 3, author_peer="user:8", text="Bot report")
    caption = source(store, 4, media="document", text="Invoice is attached")
    service = source(store, 5, service=True, text="Someone joined")
    non_ai = source(store, 6, media="video", text="")
    image = source(store, 7, media="image", text="", attachment_id="image1")
    values = context.day_sources(store, KEY, DAY)
    assert [m.message_id for m in values] == [1, 2, 3, 4, 7]
    assert context.original_characters(
        [incoming, own, bot, caption, service, non_ai, image]
    ) == (
        len("Привет 😀") + len("own") + len("Bot report") + len("Invoice is attached")
    )


def test_projection_preserves_native_speaker_and_forward_identity(store):
    forwarded = source(
        store,
        1,
        author_peer="channel:8",
        author="Sender channel",
        author_signature="Signed",
        forwarded=True,
        forwarded_from="Original channel",
        forward_date=NOW - 3600,
        forward_signature="Original signature",
    )
    value = context.projection(forwarded)
    assert value["speaker"] == dict(
        peer="channel:8", name="Sender channel", signature="Signed"
    )
    assert (
        value["forwarded_from"] == "Original channel"
        and value["forward_date"] == NOW - 3600
    )
    assert value["forward_signature"] == "Original signature"
    unknown = source(
        store, 2, author="Unproved display name", author_peer=None, outgoing=True
    )
    assert context.projection(unknown)["speaker"]["name"] == "Unknown"
    imported = source(
        store,
        3,
        imported=True,
        forwarded=True,
        forwarded_from="Imported source",
        forward_date=NOW - 7200,
    )
    assert (
        context.projection(imported)["imported"]
        and not context.projection(imported)["forwarded"]
    )


def test_calendar_day_and_dst_bounds_use_configured_timezone(store):
    start, end = context.day_bounds(store, DAY)
    assert datetime.fromtimestamp(start, UTC).hour == 17 and end - start == 86400
    value = source(store, 1, date=start)
    assert context.calendar_day(store, value) == DAY
    store.timezone = "America/New_York"
    start, end = context.day_bounds(store, "2026-11-01")
    assert end - start == 25 * 3600


@pytest.mark.parametrize("day", ["2026-10-1", "2026-02-30", "20261011", "", None])
def test_noncanonical_calendar_day_refuses(store, day):
    with pytest.raises(Refused, match="calendar"):
        context.day_bounds(store, day)


def test_day_sources_excludes_other_days_accounts_chats_and_unproved_sources(store):
    first = source(store, 1)
    source(store, 2, date=NOW - 86400)
    second = source(store, 3)
    store.groups.withdraw_source(second.key)
    store.put_chat(
        Chat(account_id=1, peer="chat:42", title="Other typed group", kind="group")
    )
    store.groups.observe("1/chat:42", access="available", reason="authored")
    store.apply_message(
        first.model_copy(update={"peer": "chat:42"}),
        group_epoch=store.groups.intake("1/chat:42").epoch,
    )
    assert [m.key for m in context.day_sources(store, KEY, DAY)] == [first.key]
    store.put_chat(Chat(account_id=1, peer="user:42", title="Private", kind="private"))
    with pytest.raises(Refused):
        context.day_sources(store, "1/user:42", DAY)
    with pytest.raises(Refused):
        context.day_sources(store, "2/channel:42", DAY)


def test_partition_preserves_whole_oldest_sources_and_explicit_omissions(store):
    values = [source(store, i, text="Я" * 5000) for i in range(1, 6)]
    values.append(source(store, 6, text="Я" * 13000))
    values.append(source(store, 7, text="Final short post"))
    plan = context.partition(reversed(values), max_sources=5)
    assert plan.selected_ids == (1, 2, 3, 4, 5)
    assert [len(chunk) for chunk in plan.chunks] == [2, 2, 1]
    assert plan.omitted == (
        dict(message_id=6, reason="source work limit"),
        dict(message_id=7, reason="source work limit"),
    )
    full = context.partition(values)
    assert full.selected_ids == (1, 2, 3, 4, 5, 7)
    assert full.omitted == (dict(message_id=6, reason="source exceeds request bound"),)


def test_partition_limits_images_and_reuses_cached_meanings(store):
    values = [
        source(store, i, media="image", attachment_id=f"img{i}", text="")
        for i in range(1, 10)
    ]
    assert [len(chunk) for chunk in context.partition(values).chunks] == [4, 4, 1]
    for message in values:
        derivative(store, message, image_context="Authored image meaning")
    assert [len(chunk) for chunk in context.partition(values, store=store).chunks] == [
        9
    ]


def test_partition_counts_cached_transcript_and_omits_oversized_source(store):
    first = source(store, 1, media="audio", text="", attachment_id="speech1")
    second = source(store, 2, media="audio", text="", attachment_id="speech2")
    derivative(store, first, transcript=dict(text="Я" * 13000, language="ru"))
    derivative(
        store, second, transcript=dict(text="Authored transcript", language="en")
    )
    plan = context.partition([first, second], store=store)
    assert plan.selected_ids == (2,)
    assert plan.omitted == (dict(message_id=1, reason="source exceeds request bound"),)


@pytest.mark.parametrize("limit", [-1, 201, True, 1.5])
def test_partition_rejects_invalid_caps(store, limit):
    with pytest.raises(Refused, match="bound"):
        context.partition([source(store, 1)], max_sources=limit)


def test_partition_refuses_mixed_peer_or_duplicate_identity(store):
    value = source(store, 1)
    for values in (
        [value, value],
        [value, value.model_copy(update={"peer": "chat:42", "message_id": 2})],
    ):
        with pytest.raises(Refused, match="exact group"):
            context.partition(values)


def test_packet_uses_up_to_twenty_recent_and_one_cached_direct_reply_no_chain(store):
    for identity in range(1, 24):
        source(store, identity, text=f"Authored context {identity}", date=NOW - 86400)
    parent = source(
        store,
        30,
        text="Old directly replied-to source",
        date=NOW - 10 * 86400,
        reply_to=29,
    )
    # Existing retained history may be repaired exactly; it is never fetched by context.
    assert parent is None
    old = Message(
        account_id=1,
        peer="channel:42",
        message_id=30,
        revision=0,
        date=NOW - 10 * 86400,
        text="Old directly replied-to source",
        reply_to=29,
        author="Alex",
        author_peer="user:7",
    )
    with store.transaction() as db:
        db.execute(
            "INSERT INTO messages (key,chat_key,message_id,revision,date,deleted,body) VALUES (?,?,?,?,?,?,?)",
            (
                old.key,
                KEY,
                old.message_id,
                old.revision,
                old.date,
                0,
                old.model_dump_json(),
            ),
        )
        store.groups.record_source(db, old, store.groups._intake(db, KEY).epoch)
    target = source(store, 40, reply_to=30)
    packet = context.packet(store, KEY, DAY, [target])
    ids = [m["id"] for m in packet["context"]]
    assert len(ids) <= 21 and ids[-2:] == [23, 30]
    assert set(ids[:-1]) <= set(range(4, 24))
    assert packet["direct_reply"] == 30 and packet["covered_ids"] == [40]
    assert not any(m["id"] == 29 for m in packet["context"])
    assert "memory" not in packet and "emotion" not in packet
    assert context.current(store, packet)


def test_unavailable_direct_reply_is_explicit_no_fetch_or_chain(store):
    target = source(store, 1, reply_to=999)
    packet = context.packet(store, KEY, DAY, [target])
    assert not packet["context"] and packet["direct_reply"] is None
    assert packet["missing_context"] == [
        dict(message_id=999, reason="direct reply is not currently cached")
    ]


def test_packet_keeps_direct_reply_within_context_byte_bound(store):
    source(store, 1, text="Я" * 4000)
    target = source(store, 2, reply_to=1)
    with pytest.raises(Refused, match="Direct reply"):
        context.packet(store, KEY, DAY, [target])


def test_a_new_intake_epoch_keeps_consumed_originals_and_admits_only_proven(store):
    target = source(store, 1)
    packet = context.packet(store, KEY, DAY, [target])
    store.groups.mark_gap(KEY)
    # Nothing was seen to change; a new stage still waits for the read.
    assert context.current(store, packet)
    assert not context.current(store, packet, admission=True)
    source(store, 1)
    assert context.current(store, packet, admission=True)
    store.groups.mark_gap(KEY)
    intake = store.groups.intake(KEY)
    store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=1,
        top=1,
        cutoff=NOW - 7 * 86400,
        oldest=NOW,
        count=0,
        complete=True,
    )
    # The read passed the original without finding it.
    assert not context.current(store, packet)


def test_an_original_older_than_the_reread_window_keeps_its_earlier_proof(store):
    old = Message(
        account_id=1,
        peer="channel:42",
        message_id=1,
        revision=0,
        date=NOW - 8 * 86400,
        text="An authored original from last week",
        author="Alex",
        author_peer="user:7",
    )
    with store.transaction() as db:
        db.execute(
            "INSERT INTO messages (key,chat_key,message_id,revision,date,deleted,body) VALUES (?,?,?,?,?,?,?)",
            (old.key, KEY, 1, 0, old.date, 0, old.model_dump_json()),
        )
        store.groups.record_source(db, old, store.groups._intake(db, KEY).epoch)
    store.groups.mark_gap(KEY)
    intake = store.groups.intake(KEY)
    store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=2,
        top=2,
        cutoff=NOW - 7 * 86400,
        oldest=NOW,
        count=0,
        complete=True,
    )
    assert store.groups.standing(old) and not store.groups.current(old)


def test_reopening_an_earlier_store_drops_its_epoch_withdrawal_trigger(tmp_path):
    path = tmp_path / "account.sqlite3"
    earlier = Store(path, account_id=1, clock=lambda: NOW)
    with earlier.transaction() as db:
        db.execute(
            "CREATE TRIGGER group_briefing_epoch_update AFTER UPDATE ON group_intake "
            "BEGIN UPDATE group_briefing_results SET valid=0 "
            "WHERE chat_key=NEW.chat_key; END"
        )
    earlier.close()
    reopened = Store(path, account_id=1, clock=lambda: NOW)
    assert not reopened.rows(
        "SELECT name FROM sqlite_master WHERE name='group_briefing_epoch_update'"
    )
    reopened.close()


@pytest.mark.parametrize(
    "change",
    ["edit", "delete", "loss", "speaker", "signature", "expiry", "proof"],
)
def test_material_dependencies_invalidate_while_append_does_not(store, change):
    target = source(store, 1)
    packet = context.packet(store, KEY, DAY, [target])
    source(store, 2, text="Later uncovered source")
    assert context.current(store, packet)
    if change in ("edit", "delete"):
        source(store, 1, text="Changed", deleted=change == "delete", revision=1)
    elif change == "loss":
        store.groups.observe(KEY, access="lost", reason="authored_loss")
    elif change == "speaker":
        change_body(store, target, author_peer="user:8")
    elif change == "signature":
        change_body(store, target, author_signature="Changed")
    elif change == "expiry":
        store.clock = lambda: NOW + RETENTION + 1
    else:
        store.groups.withdraw_source(target.key)
    assert not context.current(store, packet)


def test_routine_replay_only_blocks_new_admission_not_started_dependencies(store):
    target = source(store, 1)
    packet = context.packet(store, KEY, DAY, [target])
    store.groups.begin_replay(KEY, epoch=store.groups.intake(KEY).epoch)
    assert context.current(store, packet)
    assert not context.current(store, packet, admission=True)
    assert not context.day_sources(store, KEY, DAY)
    assert context.day_sources(store, KEY, DAY, admission=False) == [target]


def test_display_name_and_filled_forward_details_do_not_invalidate(store):
    target = source(store, 1, forwarded=True, forwarded_from=None)
    packet = context.packet(store, KEY, DAY, [target])
    change_body(
        store,
        target,
        author="New display name",
        forwarded_from="Learned channel",
        forward_date=NOW - 3600,
    )
    assert context.current(store, packet)


def test_derivative_change_invalidates_prepared_dependency(store):
    from enloq.application.media import transcript_fingerprint

    target = source(store, 1, media="audio", attachment_id="speech")
    packet = context.packet(store, KEY, DAY, [target])
    transcript = dict(text="Authored transcript", language="en")
    derivative(store, target, transcript=transcript)
    packet["transcript_inputs"] = {target.key: transcript_fingerprint(transcript)}
    assert context.current(store, packet)
    derivative(store, target, transcript=dict(text="Changed transcript", language="en"))
    assert not context.current(store, packet)


def test_current_can_use_atomic_storage_connection_without_reentering(
    store, monkeypatch
):
    target = source(store, 1)
    packet = context.packet(store, KEY, DAY, [target])
    with store.transaction() as db:
        monkeypatch.setattr(
            store,
            "transaction",
            lambda: (_ for _ in ()).throw(AssertionError("Nested transaction")),
        )
        assert context.current(store, packet, db=db)


@pytest.mark.parametrize(
    "change",
    [
        "missing_target",
        "missing_context",
        "changed_coverage",
        "projected_revision",
        "duplicate_projection",
        "boolean_id",
    ],
)
def test_current_rejects_incomplete_or_forged_dependency_projection(store, change):
    parent, target = source(store, 1), source(store, 2, reply_to=1)
    packet = context.packet(store, KEY, DAY, [target])
    if change == "missing_target":
        packet["inputs"].pop(target.key)
    elif change == "missing_context":
        packet["inputs"].pop(parent.key)
    elif change == "changed_coverage":
        packet["covered_ids"] = [1]
    elif change == "projected_revision":
        packet["messages"][0]["revision"] += 1
    elif change == "duplicate_projection":
        packet["context"].append(packet["messages"][0])
    else:
        packet["context"][0]["id"] = True
    assert not context.current(store, packet)


async def test_prepare_passes_per_call_guards_without_mutating_frozen_packet(store):
    target = source(store, 1, media="image", attachment_id="authored-image")
    packet = context.packet(store, KEY, DAY, [target])
    hooks = []
    guard, valid, speech = lambda: None, lambda: True, lambda duration: None

    class Media:
        async def enrich(self, value, key, **kwargs):
            hooks.append((key, kwargs))
            value["messages"][0]["image_context"] = "Authored meaning"
            # Actual enrich always records source-bound derivatives before return.
            derivative(store, target, image_context="Authored meaning")
            return value

    prepared = await context.prepare(
        SimpleNamespace(store=store, media=Media()),
        packet,
        guard=guard,
        valid=valid,
        on_speech_start=speech,
    )
    assert "image_context" not in packet["messages"][0]
    assert prepared["image_inputs"] == {target.key: "Authored meaning"}
    assert hooks[0][1]["guard"] is guard and hooks[0][1]["valid"] is valid
    assert hooks[0][1]["on_speech_start"] is speech


@pytest.mark.parametrize("characters, count", [(20000, 1), (6000, 2)])
async def test_fresh_transcripts_recheck_bounds_and_can_repartition_without_repeat(
    store, characters, count
):
    targets = [
        source(store, i, media="audio", attachment_id=f"authored-{i}", text="")
        for i in range(1, count + 1)
    ]
    frozen = context.packet(store, KEY, DAY, targets)

    class Media:
        async def enrich(self, value, key, **kwargs):
            for original, projection in zip(targets, value["messages"], strict=True):
                transcript = dict(text="Я" * characters, language="ru")
                projection["transcript"] = transcript
                derivative(store, original, transcript=transcript)
            return value

    with pytest.raises(Refused, match="Prepared briefing inputs"):
        await context.prepare(SimpleNamespace(store=store, media=Media()), frozen)
    plan = context.partition(targets, store=store)
    if count == 1:
        assert (
            not plan.chunks
            and plan.omitted[0]["reason"] == "source exceeds request bound"
        )
    else:
        assert [len(chunk) for chunk in plan.chunks] == [1, 1] and not plan.omitted
    assert all(
        store.rows("SELECT 1 FROM derivatives WHERE source=?", (m.key,))
        for m in targets
    )


async def test_fresh_supporting_transcript_counts_dependency_metadata_in_bound(store):
    parent = source(store, 1, media="audio", attachment_id="authored-context", text="")
    target = source(store, 2, reply_to=1)
    frozen = context.packet(store, KEY, DAY, [target])

    class Media:
        async def enrich(self, value, key, **kwargs):
            transcript = dict(text="Я" * 2820, language="ru")
            value["context"][0]["transcript"] = transcript
            derivative(store, parent, transcript=transcript)
            assert context.encoded(value["context"]) < 6000
            assert context._enriched_footprint(value, value["context"][0]) > 6000
            return value

    with pytest.raises(Refused, match="Prepared briefing inputs"):
        await context.prepare(SimpleNamespace(store=store, media=Media()), frozen)


def chunk(packet, *, output=None, valid=True):
    from enloq.domain.briefing_contract import BriefingOutput

    return SimpleNamespace(
        snapshot=SimpleNamespace(key=KEY, day=DAY, packets=[packet]),
        covered_ids=packet["covered_ids"],
        output=BriefingOutput.model_validate(
            output
            or dict(
                outcome="briefing",
                topics=[dict(text="Authored topic", citations=packet["covered_ids"])],
            )
        ),
        valid=valid,
    )


def test_small_day_synthesis_uses_raw_originals_without_chunk_or_model_work(store):
    source(store, 1, text="Earlier context")
    target = source(store, 2)
    packet = context.packet(store, KEY, DAY, [target])
    value = context.synthesis(store, KEY, DAY, packets=[packet])
    assert value["messages"] == packet["messages"] and not value["chunks"]
    assert value["covered_ids"] == [2] and value["context"][0]["id"] == 1
    assert context.current(store, value)


def test_synthesis_merges_valid_chunks_and_exact_raw_targets_only(store):
    first, second = source(store, 1), source(store, 2)
    p1 = context.packet(store, KEY, DAY, [first])
    p2 = context.packet(store, KEY, DAY, [second])
    value = context.synthesis(store, KEY, DAY, packets=[p2], chunks=[chunk(p1)])
    assert value["covered_ids"] == [1, 2]
    assert [m["id"] for m in value["messages"]] == [2]
    assert [m["id"] for m in value["context"]] == [1]
    assert value["chunks"][0]["covered_ids"] == [1]
    assert context.current(store, value)


@pytest.mark.parametrize(
    "change", ["duplicate", "invalid", "scope", "edit", "raw_image", "empty"]
)
def test_synthesis_refuses_changed_mixed_duplicate_or_unprepared_inputs(store, change):
    target = source(store, 1)
    packet = context.packet(store, KEY, DAY, [target])
    packets, chunks = [], [chunk(packet)]
    if change == "duplicate":
        packets = [packet]
    elif change == "invalid":
        chunks[0].valid = False
    elif change == "scope":
        chunks[0].snapshot.key = "1/chat:42"
    elif change == "edit":
        source(store, 1, text="Changed source", revision=1)
    elif change == "raw_image":
        packet["images"] = [
            dict(message_id=1, data_url="data:image/png;base64,authored")
        ]
        chunks, packets = [], [packet]
    else:
        chunks = []
    with pytest.raises(Refused):
        context.synthesis(store, KEY, DAY, packets=packets, chunks=chunks)


def test_synthesis_refuses_oversized_notes_without_silent_truncation(store):
    chunks = []
    for identity in range(1, 6):
        message = source(store, identity)
        packet = context.packet(store, KEY, DAY, [message])
        chunks.append(
            chunk(
                packet,
                output=dict(
                    outcome="briefing",
                    topics=[dict(text="Я" * 4000, citations=[identity])],
                ),
            )
        )
    with pytest.raises(Refused, match="Final briefing inputs"):
        context.synthesis(store, KEY, DAY, chunks=chunks)
    assert len(chunks) == 5 and all(value.valid for value in chunks)
