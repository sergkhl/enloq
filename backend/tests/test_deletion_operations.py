"""Authored deletion intent, hand-off, receipts and exact-read recovery."""

import asyncio
import json
import sqlite3
import uuid

import pytest
from enloq.domain.deletion import DeleteReceipt, DeletionRead
from enloq.domain.models import Conflict, Refused
from enloq.storage.store import RETENTION, SCHEMA_VERSION, WINDOW, Store

from backend.tests.test_rebuild import fixture, message

KEY = "1/user:42"


def originals(app, count=1):
    values = [
        message(app, identity, outgoing=identity % 2 == 0)
        for identity in range(1, count + 1)
    ]
    for value in values:
        app.store.apply_message(value)
    app.transport.messages = list(values)
    return [
        {"message_id": value.message_id, "revision": value.revision} for value in values
    ]


async def accept(app, targets=None, **kwargs):
    return await app.deletion.submit(
        KEY,
        uuid.uuid4().hex,
        targets or originals(app),
        browser="authored-browser",
        **kwargs,
    )


async def wait_for(condition):
    async with asyncio.timeout(5):
        while not condition():
            await asyncio.sleep(0.01)


async def test_acceptance_is_browser_owned_exact_and_idempotent_after_deletion(
    tmp_path,
):
    app = fixture(tmp_path)
    targets = originals(app, 2)
    identity = uuid.uuid4().hex
    first = await app.deletion.submit(
        KEY, identity, targets, browser="authored-browser"
    )
    assert first["state"] == "queued" and not app.transport.deletion_calls
    assert app.store.deletion_operations.reply_hold(KEY, 2) == "pending"
    with pytest.raises(Conflict):
        await app.deletion.submit(KEY, identity, targets, browser="another-browser")
    with pytest.raises(Conflict):
        await app.deletion.submit(
            KEY, identity, targets[:1], browser="authored-browser"
        )
    completed = await app.deletion.dispatch(identity)
    app.transport.connected = False
    repeated = await app.deletion.submit(
        KEY, identity, targets, browser="authored-browser"
    )
    assert repeated == completed and repeated["state"] == "completed"
    assert len(app.transport.deletion_calls) == 1
    assert not {"browser", "wire", "chat_key", "local_applied"} & repeated.keys()
    assert all(app.store.message(KEY, i).deleted for i in (1, 2))


@pytest.mark.parametrize(
    "change",
    [
        "stale",
        "historical",
        "deleted",
        "service",
        "expired",
        "wrong_chat",
        "wrong_account",
        "local_send",
        "inactive",
        "group",
    ],
)
async def test_acceptance_rejects_ineligible_or_unreviewed_sources(tmp_path, change):
    app = fixture(tmp_path)
    targets = originals(app)
    key = KEY
    if change in ("stale", "historical"):
        targets[0]["revision"] += 1
    elif change == "expired":
        with app.store.transaction() as db:
            db.execute("DELETE FROM messages")
        app.store.apply_message(message(app, 1, date=app.store.clock() - RETENTION - 1))
        app.store.apply_message(message(app, 2))
    elif change in ("deleted", "service"):
        source = app.store.message(KEY, 1).model_copy(
            update={
                "deleted": change == "deleted",
                "service": change == "service",
                "date": app.store.clock() - RETENTION - 1
                if change == "expired"
                else app.store.clock(),
                "revision": 2,
            }
        )
        app.store.apply_message(source)
        targets[0]["revision"] = app.store.message(KEY, 1).revision
    elif change == "wrong_chat":
        other = app.store.chat(KEY).model_copy(update={"peer": "user:43"})
        app.store.put_chat(other, activity=app.store.clock())
        key = other.key
    elif change == "wrong_account":
        app.transport.account_id = 2
    elif change == "local_send":
        targets = [{"message_id": 999, "revision": 0}]
    elif change == "inactive":
        with app.store.transaction() as db:
            db.execute("UPDATE chats SET activity=?", (app.store.clock() - WINDOW - 1,))
    elif change == "group":
        app.store.put_chat(app.store.chat(KEY).model_copy(update={"kind": "group"}))
    with pytest.raises((Refused, Conflict)):
        await app.deletion.submit(
            key, uuid.uuid4().hex, targets, browser="authored-browser"
        )
    assert not app.store.rows("SELECT 1 FROM deletion_operations")
    assert not app.transport.deletion_calls


async def test_native_deletion_is_independent_of_ai_eligibility_and_processing(
    tmp_path,
):
    app = fixture(tmp_path)
    targets = originals(app)
    app.store.put_chat(app.store.chat(KEY).model_copy(update={"ai_eligible": False}))
    app.admit_ai = lambda: (_ for _ in ()).throw(Refused("AI disabled"))
    operation = await accept(app, targets)
    assert (await app.deletion.dispatch(operation["id"]))["state"] == "completed"


async def test_intent_persistence_failure_never_dispatches_or_changes_authored_draft(
    tmp_path,
):
    app = fixture(tmp_path)
    targets = originals(app)
    draft = app.store.mutate_draft(
        KEY, 0, lambda d: d.update(text="Keep these authored words", reply_to=1)
    )
    with app.store.transaction() as db:
        db.execute(
            "CREATE TRIGGER fail_deletion BEFORE INSERT ON deletion_operations BEGIN SELECT RAISE(ABORT, 'authored intent failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        await accept(app, targets)
    assert app.store.draft(KEY) == draft and not app.transport.deletion_calls
    assert not app.store.rows("SELECT 1 FROM deletion_operations")


@pytest.mark.parametrize(
    "error,state",
    [
        (Refused("Authored native refusal"), "failed"),
        (TimeoutError(), "uncertain"),
        (OSError(), "uncertain"),
    ],
)
async def test_chunks_are_sequential_independent_and_stop_on_error(
    tmp_path, error, state
):
    app = fixture(tmp_path)
    targets = originals(app, 205)
    operation = await accept(app, targets)
    app.transport.deletion_answers = [DeleteReceipt(100, 100), error]
    stopped = await app.deletion.dispatch(operation["id"])
    assert [c["state"] for c in stopped["chunks"]] == [
        "acknowledged",
        state,
        "unstarted",
    ]
    assert [len(call.identities) for call in app.transport.deletion_calls] == [100, 100]
    assert (
        stopped["counts"]["acknowledged"] == 100 and stopped["counts"]["unstarted"] == 5
    )
    assert all(app.store.message(KEY, i).deleted for i in range(1, 101))
    assert not app.store.message(KEY, 201).deleted


async def test_read_reconciliation_covers_every_id_and_distinguishes_absence(tmp_path):
    app = fixture(tmp_path)
    targets = originals(app, 205)
    operation = await accept(app, targets)
    app.transport.deletion_answers = [DeleteReceipt(100, 100), TimeoutError()]
    await app.deletion.dispatch(operation["id"])
    app.transport.deletion_unavailable = {204}
    app.transport.messages = [m for m in app.transport.messages if m.message_id != 203]
    checked = await app.deletion.check(KEY, operation["id"])
    assert [len(batch) for _, batch in app.transport.deletion_reads] == [100, 100, 5]
    assert len(app.transport.deletion_calls) == 2
    assert checked["state"] == "uncertain" and checked["counts"]["acknowledged"] == 100
    assert checked["counts"]["absent"] == 1
    by_id = {t["message_id"]: t for t in checked["targets"]}
    assert (
        by_id[203]["observation"] == "absent"
        and by_id[204]["observation"] == "unavailable"
    )
    assert (
        app.store.message(KEY, 203).deleted and not app.store.message(KEY, 204).deleted
    )
    retry = {t["message_id"] for t in checked["retry_targets"]}
    assert retry == set(range(101, 206)) - {203, 204}
    assert all(c["receipt"] is None for c in checked["chunks"][1:])


async def test_lost_acknowledgement_followed_by_absence_is_not_for_both_success(
    tmp_path,
):
    app = fixture(tmp_path)
    operation = await accept(app)
    app.transport.lose_delete_receipt = True
    uncertain = await app.deletion.dispatch(operation["id"])
    assert uncertain["state"] == "uncertain"
    checked = await app.deletion.check(KEY, operation["id"])
    assert checked["state"] == "uncertain" and checked["counts"]["acknowledged"] == 0
    assert checked["counts"]["absent"] == 1 and checked["retry_targets"] == []
    assert len(app.transport.deletion_calls) == 1
    assert app.store.deletion_operations.blocks_removal()


@pytest.mark.parametrize("handoff,expected", [(False, "failed"), (True, "uncertain")])
async def test_restart_reconciles_without_further_mutation(tmp_path, handoff, expected):
    app = fixture(tmp_path)
    targets = originals(app, 105)
    operation = await accept(app, targets)
    owner = app.store.deletion_operations
    assert owner.claim(operation["id"])
    _, evidence = owner.start_chunk(operation["id"], 0, app.deletion._guard)
    evidence.calling()
    evidence.watching()
    if handoff:
        evidence.handing_off()
    app.store.recover()
    recovered = owner.get(operation["id"])
    assert recovered["chunks"][0]["state"] == expected
    assert recovered["chunks"][1]["state"] == "unstarted"
    worker = asyncio.create_task(app.deletion.run())
    try:
        await wait_for(lambda: not owner.get(operation["id"])["needs_reconcile"])
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert not app.transport.deletion_calls
    assert [len(batch) for _, batch in app.transport.deletion_reads] == [100, 5]


async def test_queued_before_crash_is_not_automatically_mutated(tmp_path):
    app = fixture(tmp_path)
    operation = await accept(app)
    app.store.recover()
    assert app.store.deletion_operations.waiting() == []
    checked = await app.deletion.check(KEY, operation["id"])
    assert (
        checked["targets"][0]["outcome"] == "unstarted"
        and not app.transport.deletion_calls
    )


async def test_receipt_persistence_failure_remains_uncertain_and_stops_later_chunks(
    tmp_path,
):
    app = fixture(tmp_path)
    operation = await accept(app, originals(app, 105))
    with app.store.transaction() as db:
        db.execute(
            "CREATE TRIGGER fail_receipt BEFORE UPDATE ON deletion_operations WHEN json_extract(NEW.body,'$.chunks[0].state')='acknowledged' BEGIN SELECT RAISE(ABORT, 'authored receipt persistence failure'); END"
        )
    stopped = await app.deletion.dispatch(operation["id"])
    assert (
        stopped["chunks"][0]["state"] == "uncertain"
        and stopped["chunks"][1]["state"] == "unstarted"
    )
    assert len(app.transport.deletion_calls) == 1
    assert not app.store.message(KEY, 1).deleted
    with app.store.transaction() as db:
        db.execute("DROP TRIGGER fail_receipt")
    checked = await app.deletion.check(KEY, operation["id"])
    assert checked["counts"]["absent"] == 100 and checked["counts"]["acknowledged"] == 0


async def test_persisted_receipt_survives_failed_local_consequences_and_repairs(
    tmp_path,
):
    app = fixture(tmp_path)
    operation = await accept(app)
    actual = app.sync.deleted
    app.sync.deleted = lambda *_: (_ for _ in ()).throw(
        RuntimeError("Authored local failure")
    )
    stopped = await app.deletion.dispatch(operation["id"])
    assert (
        stopped["state"] == "completed" and stopped["chunks"][0]["receipt"] is not None
    )
    assert not app.store.message(KEY, 1).deleted
    assert app.store.deletion_operations.get(operation["id"])["needs_reconcile"]
    app.sync.deleted = actual
    app.store.recover()
    checked = await app.deletion.check(KEY, operation["id"])
    assert checked["state"] == "completed" and app.store.message(KEY, 1).deleted
    assert len(app.transport.deletion_calls) == 1


async def test_source_edit_after_receipt_stops_all_later_unstarted_members(tmp_path):
    app = fixture(tmp_path)
    operation = await accept(app, originals(app, 205))
    native = app.transport.delete

    async def editing(description, evidence):
        result = await native(description, evidence)
        changed = app.store.message(KEY, 205).model_copy(
            update={"revision": 2, "text": "Authored revised wording"}
        )
        app.store.apply_message(changed)
        return result

    app.transport.delete = editing
    stopped = await app.deletion.dispatch(operation["id"])
    assert [c["state"] for c in stopped["chunks"]] == [
        "acknowledged",
        "unstarted",
        "unstarted",
    ]
    assert len(app.transport.deletion_calls) == 1
    assert "changed" in stopped["reason"]


@pytest.mark.parametrize("change", ["edit", "drain", "account"])
async def test_final_handoff_rechecks_sources_and_runtime_after_preparation(
    tmp_path, change
):
    app = fixture(tmp_path)
    operation = await accept(app)
    actual = app.transport.delete

    async def before_wire(description, evidence):
        if change == "edit":
            app.store.apply_message(
                app.store.message(KEY, 1).model_copy(
                    update={"revision": 2, "text": "Changed before wire"}
                )
            )
        elif change == "drain":
            app.authorized = lambda: False
        else:
            app.transport.account_id = 2
        return await actual(description, evidence)

    app.transport.delete = before_wire
    stopped = await app.deletion.dispatch(operation["id"])
    assert stopped["state"] == "failed" and not app.transport.deletion_calls
    assert not app.store.message(KEY, 1).deleted


async def test_retry_remaining_requires_check_new_identity_and_fresh_current_review(
    tmp_path,
):
    app = fixture(tmp_path)
    targets = originals(app, 3)
    operation = await accept(app, targets)
    app.transport.deletion_failure = TimeoutError()
    await app.deletion.dispatch(operation["id"])
    with pytest.raises(Conflict):
        await accept(app, targets)
    with pytest.raises(Conflict):
        await accept(app, targets, retry_of=operation["id"])
    app.transport.messages = app.transport.messages[1:]
    checked = await app.deletion.check(KEY, operation["id"])
    assert {t["message_id"] for t in checked["retry_targets"]} == {2, 3}
    with pytest.raises((Conflict, Refused)):
        await accept(app, targets, retry_of=operation["id"])
    app.store.apply_message(
        app.store.message(KEY, 3).model_copy(
            update={"revision": 2, "text": "New source version"}
        )
    )
    with pytest.raises(Conflict):
        await accept(app, [{"message_id": 3, "revision": 2}], retry_of=operation["id"])
    retried = await accept(app, checked["retry_targets"][:1], retry_of=operation["id"])
    assert retried["id"] != operation["id"] and retried["retry_of"] == operation["id"]


@pytest.mark.parametrize("observation", ["present", "unavailable", "absent"])
async def test_expiry_preserves_uncertainty_and_does_not_manufacture_success(
    tmp_path, observation
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    operation = await accept(app)
    app.transport.deletion_failure = TimeoutError()
    await app.deletion.dispatch(operation["id"])
    if observation == "absent":
        app.transport.messages = []
    elif observation == "unavailable":
        app.transport.deletion_unavailable = {1}
    await app.deletion.check(KEY, operation["id"])
    now[0] += RETENTION + 1
    app.store.expire()
    retained = app.deletion.get(KEY, operation["id"])
    assert retained["state"] == "uncertain" and retained["counts"]["acknowledged"] == 0
    assert retained["targets"][0]["preview"] == "" and app.store.message(KEY, 1) is None
    assert app.store.deletion_operations.reply_hold(KEY, 1) == (
        "deleted" if observation == "absent" else "unresolved"
    )
    assert app.store.deletion_operations.blocks_removal()


async def test_wrong_read_identity_is_unavailable_and_every_batch_is_attempted(
    tmp_path,
):
    app = fixture(tmp_path)
    operation = await accept(app, originals(app, 105))
    app.store.recover()
    calls = []

    async def wrong(peer, identities):
        calls.append(identities)
        return DeletionRead(2, peer, identities, (), (), identities)

    app.transport.inspect_deletion = wrong
    checked = await app.deletion.check(KEY, operation["id"])
    assert [len(batch) for batch in calls] == [100, 5]
    assert all(t["observation"] == "unavailable" for t in checked["targets"])
    assert checked["counts"]["absent"] == 0 and not app.transport.deletion_calls


async def test_stale_present_read_cannot_resurrect_acknowledged_tombstone(tmp_path):
    app = fixture(tmp_path)
    targets = originals(app)
    stale = app.transport.messages[0]
    operation = await accept(app, targets)
    await app.deletion.dispatch(operation["id"])
    app.transport.messages = [stale]
    checked = await app.deletion.check(KEY, operation["id"])
    assert (
        checked["state"] == "completed"
        and checked["targets"][0]["observation"] == "unavailable"
    )
    assert app.store.message(KEY, 1).deleted and checked["retry_targets"] == []


async def test_recovery_participates_in_callers_transaction(tmp_path):
    app = fixture(tmp_path)
    operation = await accept(app)
    with pytest.raises(RuntimeError):
        with app.store.transaction() as db:
            app.store.deletion_operations.recover(db)
            assert (
                db.execute("SELECT state FROM deletion_operations").fetchone()[0]
                == "failed"
            )
            raise RuntimeError("Authored rollback")
    assert app.store.deletion_operations.get(operation["id"])["state"] == "queued"


async def test_browser_closure_does_not_cancel_accepted_service_work(tmp_path):
    app = fixture(tmp_path)
    operation = await accept(app)
    worker = asyncio.create_task(app.deletion.run())
    try:
        await wait_for(
            lambda: (
                app.store.deletion_operations.get(operation["id"])["state"]
                == "completed"
            )
        )
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert len(app.transport.deletion_calls) == 1


async def test_fresh_store_upgrade_retains_existing_content_and_deletion_changes_notify(
    tmp_path,
):
    app = fixture(tmp_path)
    targets = originals(app)
    _, before = app.store.changes()
    operation = await accept(app, targets)
    scopes, _ = app.store.changes(before)
    assert KEY in scopes
    path = app.store.path
    app.store.close()
    restarted = Store(
        path,
        account_id=1,
        clock=lambda: 2_000_000_000,
        shared_path=app.store.shared_path,
    )
    assert restarted.deletion_operations.get(operation["id"])["state"] == "queued"
    assert restarted.message(KEY, 1).text.startswith("Authored request")
    assert restarted.rows("PRAGMA user_version")[0]["user_version"] == SCHEMA_VERSION
    restarted.close()


async def test_retry_chain_receipts_resolve_ancestor_holds_without_forging_old_success(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    targets = originals(app, 3)
    first = await accept(app, targets)
    app.transport.deletion_failure = TimeoutError()
    await app.deletion.dispatch(first["id"])
    checked = await app.deletion.check(KEY, first["id"])
    second = await accept(app, checked["retry_targets"], retry_of=first["id"])
    # An active sibling of the requested retry chain cannot be bypassed.
    with pytest.raises(Conflict):
        await accept(app, targets[:1], retry_of=first["id"])
    await app.deletion.dispatch(second["id"])
    checked = await app.deletion.check(KEY, second["id"])
    third = await accept(app, checked["retry_targets"][:1], retry_of=second["id"])
    app.transport.deletion_failure = None
    await app.deletion.dispatch(third["id"])
    assert app.store.deletion_operations.blocks_removal()
    fourth = await accept(app, checked["retry_targets"][1:], retry_of=second["id"])
    await app.deletion.dispatch(fourth["id"])
    for identity in (first["id"], second["id"]):
        historical = app.deletion.get(KEY, identity)
        assert (
            historical["state"] == "uncertain"
            and historical["counts"]["acknowledged"] == 0
        )
        assert (
            historical["counts"]["resolved"] == 3 and historical["retry_targets"] == []
        )
    assert all(
        app.store.deletion_operations.reply_hold(KEY, i) == "deleted" for i in (1, 2, 3)
    )
    assert not app.store.deletion_operations.blocks_removal()
    now[0] += RETENTION + 1
    app.store.expire()
    assert not app.store.rows("SELECT 1 FROM deletion_operations")


async def test_old_unresolved_operation_remains_discoverable_beyond_terminal_limit(
    tmp_path,
):
    app = fixture(tmp_path)
    unresolved = await accept(app)
    template = app.store.deletion_operations.get(unresolved["id"])
    with app.store.transaction() as db:
        for _ in range(101):
            terminal = json.loads(json.dumps(template))
            terminal.update(id=uuid.uuid4().hex, state="completed")
            terminal["chunks"][0].update(
                state="acknowledged", receipt={"pts": 1, "pts_count": 1}
            )
            db.execute(
                "INSERT INTO deletion_operations VALUES (?,?,?,?)",
                (terminal["id"], KEY, "completed", json.dumps(terminal)),
            )
    displayed = app.store.deletion_operations.recent(KEY)
    assert len(displayed) == 101 and displayed[-1]["id"] == unresolved["id"]
    assert (
        app.store.deletion_operations.recent(KEY, limit=1)[-1]["id"] == unresolved["id"]
    )


async def test_recent_operation_preview_expires_with_original_source_date(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    source = message(
        app, 1, date=now[0] - RETENTION + 10, text="Authored older original"
    )
    app.store.apply_message(source)
    app.store.apply_message(message(app, 2))
    operation = await accept(app, [{"message_id": 1, "revision": source.revision}])
    app.store.recover()
    now[0] += 11
    # Browser views enforce retention even before housekeeping runs.
    assert app.deletion.get(KEY, operation["id"])["targets"][0]["preview"] == ""
    app.store.expire()
    stored = app.store.deletion_operations.get(operation["id"])
    assert (
        stored["created"] > now[0] - RETENTION and stored["targets"][0]["preview"] == ""
    )
    assert app.store.message(KEY, 1) is None


async def test_definitive_refusal_does_not_erase_a_separate_prior_wire_exposure(
    tmp_path,
):
    app = fixture(tmp_path)
    operation = await accept(app)
    owner = app.store.deletion_operations
    assert owner.claim(operation["id"])
    _, evidence = owner.start_chunk(operation["id"], 0, app.deletion._guard)
    evidence.calling()
    evidence.watching()
    evidence.handing_off()
    evidence.handing_off()
    evidence.refused("Authored second-attempt refusal")
    owner.settle(operation["id"], 0, evidence)
    assert app.deletion.get(KEY, operation["id"])["state"] == "uncertain"


async def test_failed_mandatory_wire_record_prevents_native_mutation(tmp_path):
    app = fixture(tmp_path)
    operation = await accept(app)
    with app.store.transaction() as db:
        db.execute(
            "CREATE TRIGGER fail_exposure BEFORE UPDATE ON deletion_operations WHEN NEW.body LIKE '%exposed%' BEGIN SELECT RAISE(ABORT, 'authored hand-off record failure'); END"
        )
    stopped = await app.deletion.dispatch(operation["id"])
    assert stopped["state"] == "failed" and not app.transport.deletion_calls
    assert not app.store.message(KEY, 1).deleted


async def test_stranded_dispatch_after_persistence_failure_can_only_read_recover(
    tmp_path,
):
    app = fixture(tmp_path)
    operation = await accept(app)
    owner = app.store.deletion_operations
    assert owner.claim(operation["id"])
    _, evidence = owner.start_chunk(operation["id"], 0, app.deletion._guard)
    evidence.calling()
    evidence.watching()
    evidence.handing_off()
    worker = asyncio.create_task(app.deletion.run())
    try:
        await wait_for(
            lambda: (
                owner.get(operation["id"])["state"] == "uncertain"
                and not owner.get(operation["id"])["needs_reconcile"]
            )
        )
    finally:
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
    assert not app.transport.deletion_calls and len(app.transport.deletion_reads) == 1


async def test_later_unavailable_read_preserves_earlier_native_absence_evidence(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    operation = await accept(app)
    app.transport.lose_delete_receipt = True
    await app.deletion.dispatch(operation["id"])
    await app.deletion.check(KEY, operation["id"])
    app.transport.deletion_unavailable = {1}
    checked = await app.deletion.check(KEY, operation["id"])
    assert checked["targets"][0]["observation"] == "unavailable"
    assert checked["targets"][0]["absent_at"] == now[0]
    assert checked["counts"]["absent"] == 1 and checked["counts"]["acknowledged"] == 0
    now[0] += RETENTION + 1
    app.store.expire()
    assert app.store.message(KEY, 1) is None
    assert app.store.deletion_operations.reply_hold(KEY, 1) == "deleted"
    assert app.deletion.get(KEY, operation["id"])["state"] == "uncertain"
