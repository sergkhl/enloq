"""Durable briefing evidence over authored cached sources, without live calls."""

import copy
import json
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.application import group_context
from enloq.application.media import transcript_fingerprint
from enloq.domain.briefing_contract import NO_SUBSTANCE
from enloq.domain.models import Chat, Message, Refused
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import RETENTION, SCHEMA_VERSION, Store

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC).timestamp()
KEY, DAY = "1/channel:42", "2026-10-11"
ROUTE = "openrouter/xiaomi/mimo-v2.6-flash@xiaomi"


@pytest.fixture
def rig(tmp_path):
    clock = [NOW]
    registry = AccountRegistry(tmp_path)
    registry.register(1, "authored.session", directory="account")
    store = Store(
        tmp_path / "account" / "content.sqlite3",
        account_id=1,
        clock=lambda: clock[0],
        timezone="UTC",
        shared_path=registry.path,
    )
    store.put_chat(
        Chat(account_id=1, peer="channel:42", title="Authored group", kind="group")
    )
    store.groups.observe(KEY, access="available", reason="authored_native")
    intake = store.groups.intake(KEY)
    store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=0,
        top=0,
        cutoff=NOW - 7 * 86400,
        oldest=None,
        count=0,
        complete=True,
    )
    store.groups.set_processing_enabled(KEY, True)
    return SimpleNamespace(
        store=store, registry=registry, clock=clock, briefings=store.group_briefings
    )


def source(rig, identity, **values):
    message = Message(
        account_id=1,
        peer="channel:42",
        message_id=identity,
        revision=0,
        date=rig.clock[0],
        text=f"Authored native post {identity}",
        author="Alex",
        author_peer="user:7",
    ).model_copy(update=values)
    rig.store.apply_message(message, group_epoch=rig.store.groups.intake(KEY).epoch)
    # Pure cached-store fixture: the sync owner's durable native effects have
    # completed; no private AI/readiness worker is installed in this rig.
    with rig.store.transaction() as db:
        db.execute("DELETE FROM cursors WHERE key=?", ("group-effects:" + message.key,))
    return rig.store.message(KEY, identity)


def freeze(
    rig,
    ids,
    *,
    chunks=None,
    guidance="authored-group-guidance",
    route=ROUTE,
    omissions=(),
):
    packets = [
        group_context.packet(
            rig.store,
            KEY,
            DAY,
            [rig.store.message(KEY, identity) for identity in chunk],
        )
        for chunk in (chunks or [ids])
    ]
    return rig.briefings.freeze(
        KEY,
        DAY,
        packets,
        intended_ids=ids,
        omissions=omissions,
        guidance_hash=guidance,
        route=route,
    )


def output(*citations):
    return dict(
        outcome="briefing",
        topics=[
            dict(text="Authored substantive discussion.", citations=list(citations))
        ],
    )


def complete(rig, ids):
    snapshot = freeze(rig, ids)
    attempt = rig.briefings.create_attempt(snapshot)
    result = rig.briefings.publish(attempt.id, output(*ids), covered_ids=ids)
    return snapshot, attempt, result


def test_days_use_separate_storage_and_typed_group_identity(rig):
    source(rig, 1)
    _, attempt, final = complete(rig, [1])
    day = rig.briefings.state(KEY, DAY)
    assert day.final == final and day.attempt.state == "completed"
    assert day.covered_ids == [1] and day.uncovered_ids == [] and not day.partial
    assert final.covered_start == final.covered_end == NOW
    assert rig.briefings.current(final) and not rig.briefings.current(attempt)
    assert all(
        not rig.store.rows(f"SELECT * FROM {table}")
        for table in (
            "blocks",
            "emotion_state",
            "memory_entries",
            "analyzed",
            "suggestion_state",
            "jobs",
            "reservations",
            "receipts",
        )
    )
    for peer, kind in (("user:42", "private"), ("chat:42", "group")):
        rig.store.put_chat(
            Chat(account_id=1, peer=peer, title="Separate peer", kind=kind)
        )
    assert rig.briefings.state("1/chat:42", DAY).final is None
    for key in ("1/user:42", "2/channel:42", "1/channel:43"):
        with pytest.raises(Refused):
            rig.briefings.state(key, DAY)
    with pytest.raises(Refused):
        rig.briefings.state(KEY, "20261011")


def test_append_keeps_final_and_failed_ephemeral_update_restores_it(rig):
    source(rig, 1)
    _, _, final = complete(rig, [1])
    source(rig, 2)
    day = rig.briefings.state(KEY, DAY)
    assert day.final == final and day.covered_ids == [1] and day.uncovered_ids == [2]
    snapshot = freeze(rig, [1, 2])
    attempt = rig.briefings.create_attempt(snapshot)
    assert rig.briefings.publish_prefix(attempt.id, "Provisional briefing")
    assert rig.briefings.prefix(attempt.id) == "Provisional briefing"
    assert rig.briefings.state(KEY, DAY).final == final
    assert len(rig.store.rows("SELECT * FROM group_briefing_results")) == 1
    source(rig, 3)
    assert rig.briefings.current(attempt) and attempt.snapshot.intended_ids == [1, 2]
    rig.briefings.fail(attempt.id, "authored format failure")
    assert rig.briefings.prefix(attempt.id) is None
    assert rig.briefings.state(KEY, DAY).final == final
    for operation in (
        lambda: rig.briefings.publish(attempt.id, output(1, 2), covered_ids=[1, 2]),
        lambda: rig.briefings.store_chunk(attempt.id, [1], output(1)),
    ):
        with pytest.raises(Refused):
            operation()
    assert not rig.briefings.publish_prefix(attempt.id, "Late provisional text")


@pytest.mark.parametrize("effect", ["edit", "delete", "withdraw"])
def test_material_source_changes_withdraw_affected_records_before_late_output(
    rig, effect
):
    source(rig, 1)
    source(rig, 2)
    snapshot = freeze(rig, [1, 2], chunks=[[1], [2]])
    attempt = rig.briefings.create_attempt(snapshot)
    first = rig.briefings.store_chunk(attempt.id, [1], output(1))
    second = rig.briefings.store_chunk(attempt.id, [2], output(2))
    final = rig.briefings.publish(
        attempt.id, output(1, 2), covered_ids=[1, 2], chunk_ids=[first.id, second.id]
    )
    source(rig, 3)
    update = rig.briefings.create_attempt(freeze(rig, [1, 2, 3]))
    rig.briefings.publish_prefix(update.id, "Held final stream")
    if effect == "withdraw":
        rig.store.groups.withdraw_source(f"{KEY}/2")
    else:
        source(
            rig, 2, revision=1, text="Authored changed post", deleted=effect == "delete"
        )
    assert rig.store.rows(
        "SELECT valid FROM group_briefing_results WHERE id=?", (final.id,)
    ) == [{"valid": 0}]
    assert rig.briefings.attempt(update.id).state == "superseded"
    assert not rig.briefings.current(final) and not rig.briefings.current(second)
    assert rig.briefings.current(first) and rig.briefings.prefix(update.id) is None
    with pytest.raises(Refused):
        rig.briefings.publish(update.id, output(1, 2, 3), covered_ids=[1, 2, 3])


def test_editing_new_uncovered_post_keeps_earlier_cutoff_final(rig):
    source(rig, 1)
    _, _, final = complete(rig, [1])
    source(rig, 2)
    attempt = rig.briefings.create_attempt(freeze(rig, [1, 2]))
    source(rig, 2, revision=1, text="Changed uncovered post")
    assert rig.briefings.attempt(attempt.id).state == "superseded"
    assert rig.briefings.current(final) and rig.briefings.state(KEY, DAY).final == final


def test_unaffected_chunk_reuses_after_other_chunk_changes_and_append(rig):
    source(rig, 1)
    source(rig, 2)
    snapshot = freeze(rig, [1, 2], chunks=[[1], [2]])
    attempt = rig.briefings.create_attempt(snapshot)
    chunk = rig.briefings.store_chunk(attempt.id, [1], output(1))
    assert set(chunk.snapshot.packets[0]["inputs"]) == {f"{KEY}/1"}
    assert chunk.snapshot.cutoff_id == 1 and chunk.snapshot.source_dates == {1: NOW}
    source(rig, 2, revision=1, text="Changed later chunk")
    source(rig, 3)
    newer = freeze(rig, [1, 2, 3], chunks=[[1], [2, 3]])
    assert rig.briefings.chunk(newer, [1]) == chunk
    assert rig.briefings.chunk(snapshot, [2]) is None
    assert (
        rig.briefings.chunk(freeze(rig, [1], guidance="different group guidance"), [1])
        is None
    )
    assert (
        rig.briefings.chunk(freeze(rig, [1], route="different exact route"), [1])
        is None
    )


@pytest.mark.parametrize(
    "defect",
    [
        "wrong_target",
        "missing_native",
        "foreign_source",
        "foreign_derivative",
        "bool_id",
        "duplicate_id",
    ],
)
def test_prepared_chunk_requires_each_claimed_target_and_exact_frozen_proof(
    rig, defect
):
    source(rig, 1)
    source(rig, 2)
    snapshot = freeze(rig, [1, 2])
    attempt = rig.briefings.create_attempt(snapshot)
    packet = group_context.packet(rig.store, KEY, DAY, [rig.store.message(KEY, 2)])
    if defect == "wrong_target":
        packet = copy.deepcopy(snapshot.packets[0])
        packet["messages"] = packet["messages"][:1]
        packet["covered_ids"] = [2]
    elif defect == "missing_native":
        packet["inputs"].pop(f"{KEY}/2")
    elif defect == "foreign_source":
        packet["inputs"]["1/chat:42/2"] = 0
    elif defect == "foreign_derivative":
        packet["image_inputs"] = {"2/channel:42/2": "Invented meaning"}
    elif defect == "bool_id":
        packet["messages"][0]["id"] = True
    else:
        packet["messages"] *= 2
    with pytest.raises(Refused):
        rig.briefings.store_chunk(attempt.id, [2], output(2), packet=packet)
    assert not rig.store.rows("SELECT * FROM group_briefing_chunks")


@pytest.mark.parametrize("media", ["audio", "image"])
def test_prepared_media_dependencies_and_bytes_are_separate_from_native_freeze(
    rig, media
):
    message = source(rig, 1, media=media, attachment_id="authored-media", text="")
    snapshot = freeze(rig, [1])
    attempt = rig.briefings.create_attempt(snapshot)
    prepared = copy.deepcopy(snapshot.packets[0])
    cached = (
        {"transcript": {"text": "Authored local speech", "language": "en"}}
        if media == "audio"
        else {"image_context": "Authored image meaning"}
    )
    with rig.store.transaction() as db:
        db.execute(
            "INSERT INTO derivatives VALUES (?,?,?)",
            (message.key, message.revision, json.dumps(cached)),
        )
    if media == "audio":
        prepared["messages"][0]["transcript"] = cached["transcript"]
        prepared["transcript_inputs"] = {
            message.key: transcript_fingerprint(cached["transcript"])
        }
    else:
        prepared["messages"][0]["image_context"] = cached["image_context"]
        prepared["image_inputs"] = {message.key: cached["image_context"]}
        prepared["images"] = [
            dict(
                message_id=1,
                data_url="data:image/jpeg;base64,AUTHORED_RAW_BYTES",
                raw=b"AUTHORED_RAW_BYTES",
            )
        ]
    chunk = rig.briefings.store_chunk(attempt.id, [1], output(1), packet=prepared)
    assert rig.briefings.chunk(snapshot, [1]) == chunk
    bodies = " ".join(
        row["body"]
        for table in ("attempts", "chunks")
        for row in rig.store.rows(f"SELECT body FROM group_briefing_{table}")
    )
    assert "AUTHORED_RAW_BYTES" not in bodies and "data_url" not in bodies
    final = rig.briefings.publish(
        attempt.id, output(1), covered_ids=[1], chunk_ids=[chunk.id]
    )
    changed = (
        {"transcript": {"text": "Changed local speech", "language": "en"}}
        if media == "audio"
        else {"image_context": "Changed image meaning"}
    )
    with rig.store.transaction() as db:
        db.execute(
            "UPDATE derivatives SET body=? WHERE source=?",
            (json.dumps(changed), message.key),
        )
    assert not rig.briefings.current(chunk) and not rig.briefings.current(final)
    assert rig.briefings.state(KEY, DAY).final is None


def test_final_coverage_is_exact_chunk_original_union_and_citations_exclude_context(
    rig,
):
    source(rig, 1)
    source(rig, 2)
    source(rig, 3)
    snapshot = freeze(rig, [2, 3], chunks=[[2], [3]])
    attempt = rig.briefings.create_attempt(snapshot)
    chunk = rig.briefings.store_chunk(attempt.id, [2], output(2))
    for call in (
        lambda: rig.briefings.store_chunk(attempt.id, [2], output(1)),
        lambda: rig.briefings.publish(
            attempt.id, output(2, 3), covered_ids=[2, 3], chunk_ids=[chunk.id]
        ),
        lambda: rig.briefings.publish(
            attempt.id,
            output(1),
            covered_ids=[2, 3],
            chunk_ids=[chunk.id],
            original_ids=[3],
        ),
        lambda: rig.briefings.publish(
            attempt.id, output(2), covered_ids=[2, 3, 4], original_ids=[2, 3, 4]
        ),
    ):
        with pytest.raises(Refused):
            call()
    final = rig.briefings.publish(
        attempt.id,
        output(2, 3),
        covered_ids=[2, 3],
        chunk_ids=[chunk.id],
        original_ids=[3],
    )
    assert final.covered_ids == [2, 3] and final.chunk_ids == [chunk.id]


def test_partial_continuation_is_firsttime_uncovered_work_and_full_coverage_never_regenerates(
    rig,
):
    source(rig, 1)
    source(rig, 2)
    snapshot = freeze(rig, [1, 2])
    first = rig.briefings.create_attempt(snapshot)
    chunk = rig.briefings.store_chunk(first.id, [1], output(1))
    partial = rig.briefings.publish(
        first.id, output(1), covered_ids=[1], chunk_ids=[chunk.id]
    )
    rig.briefings.finish_batch(first.batch_id)
    assert rig.briefings.state(KEY, DAY).uncovered_ids == [2]
    continuation = rig.briefings.create_attempt(snapshot)
    assert rig.briefings.chunk(snapshot, [1]) == chunk
    assert rig.briefings.state(KEY, DAY).final == partial
    rig.briefings.publish(
        continuation.id,
        output(1, 2),
        covered_ids=[1, 2],
        chunk_ids=[chunk.id],
        original_ids=[2],
    )
    with pytest.raises(Refused, match="Completed briefing sources"):
        rig.briefings.create_attempt(snapshot)
    # Adopted manual Update may replace completed wording when its rules change.
    changed = freeze(rig, [1, 2], guidance="new guidance")
    replacement = rig.briefings.create_attempt(changed)
    assert rig.briefings.state(KEY, DAY).final.covered_ids == [1, 2]
    rig.briefings.fail(replacement.id, "authored rules replacement failed")
    with pytest.raises(Refused):
        rig.briefings.retry(continuation.id)


def test_no_substance_has_exact_final_wording_and_coverage(rig):
    source(rig, 1)
    attempt = rig.briefings.create_attempt(freeze(rig, [1]))
    final = rig.briefings.publish(
        attempt.id, dict(outcome="no_substance", topics=[]), covered_ids=[1]
    )
    assert final.text == NO_SUBSTANCE and final.covered_ids == [1]
    with pytest.raises(Refused):
        rig.briefings.retry(attempt.id)


def reread(rig, *, before, complete):
    intake = rig.store.groups.intake(KEY)
    rig.store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=before,
        top=intake.top or 1,
        cutoff=NOW - 7 * 86400,
        oldest=NOW,
        count=intake.count,
        complete=complete,
    )


def test_replay_and_a_new_epoch_keep_results_until_the_read_misses_an_original(rig):
    source(rig, 1)
    source(rig, 2)
    snapshot, _, final = complete(rig, [1])
    epoch = rig.store.groups.intake(KEY).epoch
    rig.store.groups.begin_replay(KEY, epoch=epoch)
    assert rig.briefings.current(snapshot) and rig.briefings.current(final)
    assert not rig.briefings.current(snapshot, admission=True)
    assert rig.briefings.state(KEY, DAY).final == final
    with pytest.raises(Refused):
        freeze(rig, [1])
    # Telegram could not replay what was missed. Originals are read again from
    # the newest down; that alone changes none of them.
    rig.store.groups.mark_gap(KEY)
    assert rig.briefings.current(final)
    state = rig.briefings.state(KEY, DAY)
    assert state.final == final and state.gap
    assert not rig.briefings.current(snapshot, admission=True)
    with pytest.raises(Refused):
        freeze(rig, [1])
    # The read has confirmed only the newer original so far.
    source(rig, 2)
    reread(rig, before=2, complete=False)
    assert rig.briefings.state(KEY, DAY).final == final
    # It then passes the covered original without finding it.
    reread(rig, before=1, complete=True)
    assert (
        not rig.briefings.current(final) and rig.briefings.state(KEY, DAY).final is None
    )
    assert rig.briefings.cached_final(KEY, DAY) == final.model_copy(
        update={"valid": False}
    )


def test_a_new_epoch_that_reads_the_same_originals_again_regenerates_nothing(rig):
    source(rig, 1)
    snapshot, _, final = complete(rig, [1])
    rig.store.groups.mark_gap(KEY)
    source(rig, 1)
    reread(rig, before=1, complete=True)
    assert not rig.store.groups.intake(KEY).gap
    assert rig.briefings.current(final) and rig.briefings.state(KEY, DAY).final == final
    assert rig.briefings.current(snapshot, admission=True)
    assert not rig.briefings.can_create(freeze(rig, [1]))


def test_gap_coverage_improvement_preserves_same_material_snapshot(rig):
    rig.store.groups.mark_gap(KEY)
    source(rig, 1)
    snapshot, _, final = complete(rig, [1])
    assert snapshot.gap and rig.briefings.state(KEY, DAY).partial
    intake = rig.store.groups.intake(KEY)
    rig.store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=0,
        top=1,
        cutoff=NOW - 7 * 86400,
        oldest=NOW,
        count=1,
        complete=True,
    )
    assert not rig.store.groups.intake(KEY).gap
    assert rig.briefings.current(final) and rig.briefings.state(KEY, DAY).final == final
    assert rig.briefings.state(
        KEY, DAY
    ).partial  # This final advertised its captured gap.


@pytest.mark.parametrize(
    "complete_scan,omitted,cutoff,range_complete",
    [
        (True, 2, NOW - 7 * 86400, True),
        (False, 0, NOW - 7 * 86400, False),
        (True, 0, NOW - 60, False),
    ],
)
def test_native_intake_coverage_keeps_fully_cached_day_truthfully_partial(
    rig, complete_scan, omitted, cutoff, range_complete
):
    source(rig, 1)
    intake = rig.store.groups.intake(KEY)
    rig.store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=1,
        top=1,
        cutoff=cutoff,
        oldest=NOW,
        count=1,
        complete=complete_scan,
        omitted=omitted,
    )
    snapshot, _, final = complete(rig, [1])
    coverage = snapshot.intake_coverage
    assert coverage.native_omitted == omitted
    assert coverage.intake_complete is complete_scan
    assert coverage.range_complete is range_complete
    assert (coverage.before, coverage.top, coverage.count) == (1, 1, 1)
    assert coverage.cutoff == cutoff and coverage.oldest == NOW
    day = rig.briefings.state(KEY, DAY)
    assert day.partial and not day.gap
    assert day.covered_ids == [1] and day.uncovered_ids == [] and day.omissions == []
    assert day.intake_coverage == coverage and day.final == final
    # Later metadata cannot expand the coverage the already published final
    # actually represented. It also cannot supersede material-valid wording.
    rig.store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=0,
        top=1,
        cutoff=NOW - 7 * 86400,
        oldest=NOW,
        count=1,
        complete=True,
    )
    assert rig.briefings.current(final)
    assert rig.briefings.state(KEY, DAY).partial
    rig.store.recover()
    assert rig.briefings.state(KEY, DAY).final.snapshot.intake_coverage == coverage


def test_completed_seven_day_intake_does_not_prove_an_older_cached_calendar_day(rig):
    old_date = NOW - 8 * 86400
    rig.clock[0] = old_date
    message = source(rig, 1)
    rig.clock[0] = NOW
    old_day = datetime.fromtimestamp(old_date, tz=UTC).date().isoformat()
    packet = group_context.packet(rig.store, KEY, old_day, [message])
    snapshot = rig.briefings.freeze(
        KEY,
        old_day,
        [packet],
        intended_ids=[1],
        guidance_hash="authored-group-guidance",
        route=ROUTE,
    )
    attempt = rig.briefings.create_attempt(snapshot)
    rig.briefings.publish(attempt.id, output(1), covered_ids=[1])
    day = rig.briefings.state(KEY, old_day)
    assert day.covered_ids == [1] and not day.gap and day.partial
    assert day.intake_coverage.intake_complete
    assert not day.intake_coverage.range_complete


def test_intake_coverage_improvement_does_not_change_prepared_chunk_reuse(rig):
    source(rig, 1)
    intake = rig.store.groups.intake(KEY)
    rig.store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=1,
        top=1,
        cutoff=NOW - 7 * 86400,
        oldest=NOW,
        count=1,
        omitted=2,
        complete=False,
    )
    snapshot = freeze(rig, [1])
    attempt = rig.briefings.create_attempt(snapshot)
    chunk = rig.briefings.store_chunk(
        attempt.id, [1], output(1), packet=snapshot.packets[0]
    )
    rig.store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=0,
        top=1,
        cutoff=NOW - 7 * 86400,
        oldest=NOW,
        count=1,
        complete=True,
    )
    current = freeze(rig, [1])
    assert current.intake_coverage != snapshot.intake_coverage
    assert rig.briefings.chunk(current, [1], packet=current.packets[0]) == chunk


def test_access_loss_keeps_material_cached_final_readonly_but_not_reusable(rig):
    source(rig, 1)
    snapshot, _, final = complete(rig, [1])
    rig.store.groups.observe(KEY, access="lost", reason="authored_membership_loss")
    assert (
        not rig.briefings.current(final) and rig.briefings.state(KEY, DAY).final is None
    )
    cached = rig.briefings.cached_final(KEY, DAY)
    assert cached.text == final.text and not cached.valid
    assert rig.briefings.state(KEY, DAY, cached_access=True).cached
    assert (
        not rig.briefings.current(cached) and rig.briefings.chunk(snapshot, [1]) is None
    )
    rig.store.groups.observe(KEY, access="available", reason="authored_restoration")
    source(rig, 1)
    assert not rig.briefings.current(snapshot)
    assert rig.briefings.cached_final(KEY, DAY).text == final.text
    source(rig, 1, revision=1, text="Confirmed changed cached source")
    assert rig.briefings.cached_final(KEY, DAY) is None


def test_restart_discards_prefix_and_retry_keeps_exact_failed_snapshot_and_fresh_caps(
    rig,
):
    source(rig, 1)
    _, _, final = complete(rig, [1])
    source(rig, 2)
    snapshot = freeze(rig, [1, 2])
    batch = rig.briefings.begin_batch(KEY, automatic=True)
    attempt = rig.briefings.create_attempt(snapshot, automatic=True, batch_id=batch.id)
    rig.briefings.record_work(
        batch.id,
        requests=1,
        source_ids=[2],
        speech_seconds=23,
        dispatch_token="authored-dispatch",
    )
    rig.briefings.publish_prefix(attempt.id, "Provisional source-free prefix")
    source(rig, 3)
    rig.store.recover()
    assert rig.briefings.prefix(attempt.id) is None
    assert rig.briefings.attempt(attempt.id).state == "interrupted"
    assert rig.briefings.state(KEY, DAY).final == final
    retry = rig.briefings.retry(attempt.id)
    assert retry.id != attempt.id and retry.retry_of == attempt.id
    assert retry.snapshot == snapshot and retry.snapshot.intended_ids == [1, 2]
    assert not retry.automatic and rig.briefings.batch(retry.batch_id).requests == 0
    assert rig.briefings.batch(batch.id).requests == 1
    assert rig.briefings.run_state(KEY).last_auto_started == NOW


@pytest.mark.parametrize(
    "change", ["group_off", "master_off", "edit", "loss", "expiry"]
)
def test_retry_requires_current_frozen_sources_and_both_processing_switches(
    rig, change
):
    source(rig, 1)
    snapshot = freeze(rig, [1])
    attempt = rig.briefings.create_attempt(snapshot)
    rig.briefings.fail(attempt.id, "authored counted failure")
    if change == "group_off":
        rig.store.groups.set_processing_enabled(KEY, False)
    elif change == "master_off":
        rig.registry.set_processing(1, False)
    elif change == "edit":
        source(rig, 1, revision=1, text="Changed failed source")
    elif change == "loss":
        rig.store.groups.observe(KEY, access="lost", reason="authored_loss")
    else:
        rig.clock[0] += RETENTION + 1
    with pytest.raises(Refused):
        rig.briefings.retry(attempt.id)
    assert len(rig.store.rows("SELECT * FROM group_briefing_attempts")) == 1


def test_guidance_change_does_not_automatically_retry_unchanged_failure(rig):
    source(rig, 1)
    attempt = rig.briefings.create_attempt(freeze(rig, [1]))
    rig.briefings.fail(attempt.id, "authored uncertain failure")
    for snapshot in (freeze(rig, [1]), freeze(rig, [1], guidance="new group guidance")):
        with pytest.raises(Refused, match="failed-attempt Retry"):
            rig.briefings.create_attempt(snapshot, automatic=True)
    retry = rig.briefings.retry(attempt.id)
    assert retry.snapshot == attempt.snapshot


def test_valid_started_attempt_finishes_with_processing_off_and_omissions_truthful(rig):
    source(rig, 1)
    source(rig, 2)
    snapshot = freeze(
        rig, [1], omissions=[dict(message_id=2, reason="source work limit")]
    )
    attempt = rig.briefings.create_attempt(snapshot, automatic=True)
    rig.store.groups.set_processing_enabled(KEY, False)
    rig.registry.set_processing(1, False)
    final = rig.briefings.publish(attempt.id, output(1), covered_ids=[1])
    day = rig.briefings.state(KEY, DAY)
    assert day.final == final and day.partial and day.uncovered_ids == [2]
    assert (
        day.omissions[0].message_id == 2
        and day.omissions[0].reason == "source work limit"
    )


def test_work_caps_are_atomic_global_to_batch_and_dispatches_idempotent(rig):
    batch = rig.briefings.begin_batch(KEY, automatic=True)
    first = rig.briefings.record_work(
        batch.id,
        requests=1,
        source_ids=range(1, 201),
        speech_seconds=1200,
        dispatch_token="one",
    )
    assert (
        rig.briefings.record_work(
            batch.id,
            requests=1,
            source_ids=range(1, 201),
            speech_seconds=1200,
            dispatch_token="one",
        )
        == first
    )
    assert rig.briefings.record_work(batch.id, source_ids=[1, 2, 2]) == first
    for tokens in (
        dict(requests=1, source_ids=[201]),
        dict(requests=1, speech_seconds=0.1),
        dict(requests=5),
        dict(requests=1, dispatch_token="one", source_ids=[1]),
    ):
        with pytest.raises(Refused):
            rig.briefings.record_work(batch.id, **tokens)
        assert rig.briefings.batch(batch.id) == first
    for index in range(2, 6):
        rig.briefings.record_work(batch.id, requests=1, dispatch_token=str(index))
    assert rig.briefings.batch(batch.id).requests == 5
    with pytest.raises(Refused):
        rig.briefings.record_work(batch.id, requests=1, dispatch_token="six")
    rig.briefings.finish_batch(batch.id, state="failed")
    with pytest.raises(Refused):
        rig.briefings.record_work(batch.id, requests=1, dispatch_token="late")


def test_automatic_clock_survives_midnight_toggles_restart_and_rollback(rig):
    batch = rig.briefings.begin_batch(KEY, automatic=True)
    rig.briefings.finish_batch(batch.id)
    rig.clock[0] += 86400
    rig.store.groups.set_processing_enabled(KEY, False)
    rig.store.groups.set_processing_enabled(KEY, True)
    manual = rig.briefings.begin_batch(KEY, automatic=False)
    rig.briefings.finish_batch(manual.id)
    rig.store.close()
    reopened = Store(
        rig.store.path,
        account_id=1,
        clock=lambda: rig.clock[0],
        timezone="UTC",
        shared_path=rig.registry.path,
    )
    assert reopened.group_briefings.run_state(KEY).last_auto_started == NOW
    rig.clock[0] = NOW - 1
    newer = reopened.group_briefings.begin_batch(KEY, automatic=True)
    assert newer.started == NOW - 1
    assert reopened.group_briefings.run_state(KEY).last_auto_started == NOW


def test_expired_older_context_removes_artifacts_without_resetting_clock_or_spending(
    rig,
):
    rig.clock[0] = NOW - 29 * 86400
    source(rig, 1)
    rig.clock[0] = NOW
    source(rig, 2, reply_to=1)
    snapshot = freeze(rig, [2])
    attempt = rig.briefings.create_attempt(snapshot, automatic=True)
    chunk = rig.briefings.store_chunk(attempt.id, [2], output(2))
    rig.briefings.publish(attempt.id, output(2), covered_ids=[2], chunk_ids=[chunk.id])
    rig.clock[0] += 2 * 86400
    rig.store.expire()
    assert rig.store.message(KEY, 2) is not None
    assert all(
        not rig.store.rows(f"SELECT * FROM group_briefing_{table}")
        for table in ("attempts", "chunks", "results", "dependencies")
    )
    assert rig.briefings.run_state(KEY).last_auto_started == NOW


def test_schema31_upgrade_is_additive_and_preserves_real_content_policy_and_ledgers(
    rig,
):
    source(rig, 1)
    rig.registry.set_budget(765432100)
    rig.registry.set_budget(2370000000, family="group_briefing")
    rig.registry.set_processing_threshold(345)
    rig.store.groups.set_processing_enabled(KEY, False)
    rig.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored private", kind="private")
    )
    rig.store.apply_message(
        Message(
            account_id=1,
            peer="user:42",
            message_id=1,
            revision=0,
            date=NOW,
            text="Kept private original",
        )
    )
    with rig.store.transaction() as db:
        db.execute(
            "INSERT INTO reservations (id,bucket,engine,reserved,actual,status,created,family) VALUES ('kept','day:2026-10-11','mimo',77,73,'receipted',?,'daily')",
            (NOW,),
        )
        db.execute(
            "INSERT INTO receipts (id,role,model,provider,usage,error,family) VALUES ('kept','outgoing_rewrite','authored','xiaomi','{}',NULL,'daily')"
        )
        db.execute(
            "INSERT INTO shared.model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created,family) VALUES (1,'kept',?,'outgoing_rewrite','kept','day:2026-10-11','mimo',77,73,'receipted',?,'daily')",
            ("1/user:42", NOW),
        )
    tables = [
        row["name"]
        for row in rig.store.rows(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'group_briefing_%' AND name!='changes'"
        )
    ]
    before = {table: rig.store.rows(f"SELECT * FROM {table}") for table in tables}
    shared = {
        table: rig.registry.rows(f"SELECT * FROM {table}")
        for table in (
            "metadata",
            "model_reservations",
            "model_receipts",
            "budget_changes",
        )
    }
    rig.store.close()
    with sqlite3.connect(rig.store.path) as db:
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'group_briefing_%'"
        ).fetchall():
            db.execute(f"DROP TRIGGER {name}")
        for (name,) in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'group_briefing_%'"
        ).fetchall():
            db.execute(f"DROP TABLE {name}")
        db.execute("PRAGMA user_version=31")
    reopened = Store(
        rig.store.path,
        account_id=1,
        clock=lambda: rig.clock[0],
        timezone="UTC",
        shared_path=rig.registry.path,
    )
    assert (
        reopened.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION == 38
    )
    assert {
        table: reopened.rows(f"SELECT * FROM {table}") for table in tables
    } == before
    assert {
        table: rig.registry.rows(f"SELECT * FROM {table}") for table in shared
    } == shared
    assert not reopened.groups.get(KEY).processing_enabled
    assert reopened.group_briefings.run_state(KEY).last_auto_started is None
    assert all(
        not reopened.rows(f"SELECT * FROM group_briefing_{table}")
        for table in (
            "days",
            "runs",
            "batches",
            "attempts",
            "chunks",
            "results",
            "dependencies",
        )
    )
