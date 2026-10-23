"""Authored deliberate briefing updates and frozen one-time rollout intent."""

import asyncio
import json
import threading
from datetime import UTC, datetime

import pytest
from enloq.api.server import create_app
from enloq.application.group_rollout import GroupBriefingRollout
from enloq.application.group_updates import GroupUpdates
from enloq.config import Settings
from enloq.domain.models import Chat, Message, ProcessingPaused, Refused
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_group_briefing_views import complete, failed
from backend.tests.test_group_pipeline_reuse import (
    DAY,
    KEY,
    NOW,
    create,
    restart,
    source,
)


@pytest.fixture
async def rig(tmp_path):
    value = create(tmp_path)
    # The storage-only completion helpers use these same canonical owners.
    value.store, value.storage = value.app.store, value.app.store.group_briefings
    yield value
    await value.manager.close()


async def finish(rig, attempt, *, maximum=30):
    """Run only this accepted intent, leaving automatic planning outside it."""
    for _ in range(maximum):
        jobs = rig.app.store.rows(
            "SELECT * FROM jobs WHERE state='queued' AND json_extract(payload,'$.attempt')=? ORDER BY rowid",
            (attempt,),
        )
        if not jobs:
            return
        assert len(jobs) == 1
        assert rig.app.briefings.claim(jobs[0])
        await rig.app.briefings.run(jobs[0])
    pytest.fail("Authored accepted briefing did not settle")


async def test_manual_first_generation_bypasses_admission_and_coalesces(rig):
    rig.manager.set_processing_threshold(10000)
    source(rig, 1, text="One short authored update.")
    updates = GroupUpdates(rig.app.briefings)
    assert rig.app.briefings.planner.pending() == []
    first = updates.update(KEY)
    assert first["status"] == "started" and first["day"] == DAY
    assert not rig.calls
    assert updates.view(KEY)["status"] == "active"
    again = updates.update(KEY)
    assert again == {**first, "status": "active"}
    assert len(rig.store.rows("SELECT id FROM group_briefing_attempts")) == 1
    await finish(rig, first["attempt"])
    assert rig.storage.state(KEY, DAY).final.covered_ids == [1]
    assert rig.storage.run_state(KEY).last_auto_started is None


async def test_manual_freezes_day_and_sources_while_new_arrivals_wait(rig):
    rig.now[0] = datetime(2026, 10, 11, 23, 59, 55, tzinfo=UTC).timestamp()
    source(rig, 1)
    result = GroupUpdates(rig.app.briefings).update(KEY)
    source(rig, 2)
    rig.now[0] += 10
    source(rig, 3)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final.covered_ids == [1]
    assert final.snapshot.day == DAY
    assert rig.storage.state(KEY, "2026-10-12").final is None
    assert rig.storage.run_state(KEY).last_auto_started is None


async def test_unchanged_click_reuses_valid_result_without_jobs_or_spending(rig):
    source(rig, 1)
    updates = GroupUpdates(rig.app.briefings)
    first = updates.update(KEY)
    await finish(rig, first["attempt"])
    jobs = rig.store.rows("SELECT * FROM jobs")
    spending = rig.manager.registry.rows("SELECT * FROM model_reservations")
    calls = list(rig.calls)
    assert updates.view(KEY)["status"] == "up_to_date"
    assert updates.update(KEY) == dict(status="up_to_date", day=DAY)
    assert rig.store.rows("SELECT * FROM jobs") == jobs
    assert rig.manager.registry.rows("SELECT * FROM model_reservations") == spending
    assert rig.calls == calls


async def test_changed_rules_rebuild_today_without_invalidating_old_final(rig):
    source(rig, 1)
    final = complete(rig, [1])
    updates = GroupUpdates(rig.app.briefings)
    assert updates.update(KEY)["status"] == "up_to_date"
    rig.manager.registry.set_guidance({"group_briefing": "Preserve authored caveats."})
    result = updates.update(KEY)
    assert result["status"] == "started"
    assert rig.storage.state(KEY, DAY).final.id == final.id
    assert not rig.calls
    await finish(rig, result["attempt"])
    assert rig.storage.state(KEY, DAY).final.id != final.id


def test_new_cached_media_meaning_qualifies_without_native_reads(rig):
    source(rig, 1, text="Caption with an unavailable image.", media="image")
    complete(rig, [1])
    assert GroupUpdates(rig.app.briefings).view(KEY)["status"] == "up_to_date"
    message = rig.store.message(KEY, 1)
    with rig.store.transaction() as db:
        db.execute(
            "INSERT INTO derivatives VALUES (?,?,?)",
            (
                message.key,
                message.revision,
                json.dumps(dict(image_context="Authored new image meaning.")),
            ),
        )
    assert GroupUpdates(rig.app.briefings).update(KEY)["status"] == "started"
    assert not rig.app.transport.exact_reads
    assert not rig.calls


@pytest.mark.parametrize("blocked", ["account", "group", "access", "budget"])
def test_manual_preserves_current_account_group_access_and_budget_gates(rig, blocked):
    source(rig, 1)
    if blocked == "account":
        rig.manager.set_processing(1, False)
    elif blocked == "group":
        rig.app.set_group_processing(KEY, False)
    elif blocked == "access":
        rig.store.groups.observe(KEY, access="lost", reason="authored_loss")
    else:
        rig.manager.registry.set_budget(0, family="group_briefing")
    updates = GroupUpdates(rig.app.briefings)
    assert not updates.view(KEY)["enabled"]
    with pytest.raises(Refused):
        updates.update(KEY)
    assert not rig.store.rows("SELECT id FROM group_briefing_attempts")


@pytest.mark.parametrize(
    "blocked, status",
    [("account", "unavailable"), ("group", "unavailable"), ("budget", "budget")],
)
def test_the_band_slot_names_each_blocked_state(rig, blocked, status):
    source(rig, 1)
    if blocked == "account":
        rig.manager.set_processing(1, False)
    elif blocked == "group":
        rig.app.set_group_processing(KEY, False)
    else:
        rig.manager.registry.set_budget(0, family="group_briefing")
    view = GroupUpdates(rig.app.briefings).view(KEY)
    assert view["status"] == status and not view["enabled"]


@pytest.mark.parametrize(
    "earlier, status", [(failed, "budget"), (complete, "up_to_date")]
)
def test_a_spent_budget_outranks_retry_but_not_up_to_date(rig, earlier, status):
    source(rig, 1)
    earlier(rig, [1])
    rig.manager.registry.set_budget(0, family="group_briefing")
    assert GroupUpdates(rig.app.briefings).view(KEY)["status"] == status


def test_a_catch_up_in_flight_is_a_transient_syncing_state(rig):
    source(rig, 1)
    rig.store.groups.begin_replay(KEY, epoch=rig.store.groups.intake(KEY).epoch)
    view = GroupUpdates(rig.app.briefings).view(KEY)
    assert view["status"] == "syncing" and view["transient"] is True


def test_ready_work_is_the_one_enabled_update(rig):
    source(rig, 1)
    view = GroupUpdates(rig.app.briefings).view(KEY)
    assert view == dict(day=DAY, enabled=True, reason=None, status="ready")


def test_unchanged_failed_work_requires_retry_and_cannot_open_another_attempt(rig):
    source(rig, 1)
    previous = failed(rig, [1])
    updates = GroupUpdates(rig.app.briefings)
    assert updates.update(KEY) == dict(
        status="retry_required", day=DAY, attempt=previous.id
    )
    # The band's single slot offers Retry for that attempt, never Update.
    view = updates.view(KEY)
    assert view["status"] == "retry_required" and view["enabled"]
    assert view["attempt"] == previous.id
    assert len(rig.store.rows("SELECT id FROM group_briefing_attempts")) == 1


def beside(action):
    """What a reading request or the scheduler does meanwhile, on its own thread."""
    worker = threading.Thread(target=action)
    worker.start()
    worker.join()


async def test_reconciliation_beside_a_starting_update_leaves_it_runnable(
    rig, monkeypatch
):
    source(rig, 1)
    briefings = rig.app.briefings
    plan = briefings.filtering.plan

    def plan_a_large_day(*args, **kwargs):
        # Planning reads every frozen source after the batch exists and before
        # its first stage is queued: seconds on a two-hundred-source day.
        beside(briefings.reconcile)
        return plan(*args, **kwargs)

    monkeypatch.setattr(briefings.filtering, "plan", plan_a_large_day)
    result = GroupUpdates(briefings).update(KEY)
    monkeypatch.undo()
    attempt = rig.storage.attempt(result["attempt"])
    assert rig.storage.batch(attempt.batch_id).state == "active"
    assert rig.storage.run_state(KEY).active_batch == attempt.batch_id
    await finish(rig, result["attempt"])
    assert rig.storage.attempt(result["attempt"]).state == "completed"
    assert rig.storage.state(KEY, DAY).final.covered_ids == [1]


async def test_reconciliation_between_stages_never_interrupts_running_work(
    rig, monkeypatch
):
    for identity in (1, 2):
        source(rig, identity)
    briefings = rig.app.briefings
    enqueue = briefings._enqueue

    def queue_after_a_reading_request(payload, **kwargs):
        # One stage is done and its successor is not queued yet.
        beside(briefings.reconcile)
        return enqueue(payload, **kwargs)

    monkeypatch.setattr(briefings, "_enqueue", queue_after_a_reading_request)
    result = GroupUpdates(briefings).update(KEY)
    await finish(rig, result["attempt"])
    assert rig.storage.attempt(result["attempt"]).state == "completed"
    assert rig.storage.state(KEY, DAY).final.covered_ids == [1, 2]
    assert (
        rig.storage.batch(rig.storage.attempt(result["attempt"]).batch_id).state
        == "completed"
    )


async def test_reconciliation_beside_a_retry_leaves_it_runnable(rig, monkeypatch):
    source(rig, 1)
    previous = failed(rig, [1])
    briefings = rig.app.briefings
    reusable = briefings._reusable

    def reuse_after_a_reading_request(*args, **kwargs):
        # Retry's batch exists before its reusable work is read and queued.
        beside(briefings.reconcile)
        return reusable(*args, **kwargs)

    monkeypatch.setattr(briefings, "_reusable", reuse_after_a_reading_request)
    job = briefings.retry(previous.id)
    monkeypatch.undo()
    attempt = rig.storage.attempt(json.loads(job["payload"])["attempt"])
    assert attempt.retry_of == previous.id
    assert rig.storage.batch(attempt.batch_id).state == "active"
    await finish(rig, attempt.id)
    assert rig.storage.attempt(attempt.id).state == "completed"
    assert rig.storage.state(KEY, DAY).final.covered_ids == [1]


def test_an_abandoned_batch_is_still_interrupted_once_nothing_holds_it(rig):
    source(rig, 1)
    briefings = rig.app.briefings
    result = GroupUpdates(briefings).update(KEY)
    attempt = rig.storage.attempt(result["attempt"])
    with rig.store.transaction() as db:
        db.execute("UPDATE jobs SET state='done' WHERE state='queued'")
    beside(briefings.reconcile)
    assert rig.storage.batch(attempt.batch_id).state == "interrupted"
    assert rig.storage.run_state(KEY).active_batch is None
    # Its attempt ends too; left active it would block every later update.
    assert rig.storage.attempt(attempt.id).state == "interrupted"
    assert GroupUpdates(briefings).view(KEY)["status"] != "active"


def test_a_start_that_fails_after_its_batch_exists_leaves_a_retry(rig, monkeypatch):
    source(rig, 1)
    briefings = rig.app.briefings

    def unavailable(*args, **kwargs):
        raise RuntimeError("authored store contention")

    monkeypatch.setattr(briefings.filtering, "plan", unavailable)
    with pytest.raises(RuntimeError):
        GroupUpdates(briefings).update(KEY)
    monkeypatch.undo()
    rows = rig.store.rows("SELECT id,state FROM group_briefing_attempts")
    assert [row["state"] for row in rows] == ["interrupted"]
    assert rig.storage.run_state(KEY).active_batch is None
    assert not rig.store.rows("SELECT id FROM jobs WHERE state='queued'")
    assert GroupUpdates(briefings).update(KEY) == dict(
        status="retry_required", day=DAY, attempt=rows[0]["id"]
    )
    job = briefings.retry(rows[0]["id"])
    await_attempt = json.loads(job["payload"])["attempt"]
    assert rig.storage.attempt(await_attempt).state == "active"


async def test_context_whose_media_was_prepared_since_freezing_gives_way(rig):
    earlier = NOW - 86400
    picture = source(
        rig, 1, date=earlier, text="", media="image", attachment_id="photo-1"
    )
    for identity in range(2, 8):
        source(
            rig,
            identity,
            date=earlier,
            author_peer=f"user:{identity + 6}",
            text="B" * 580,
        )
    source(rig, 20, text="An authored point for today.")
    result = GroupUpdates(rig.app.briefings).update(KEY)
    frozen = rig.storage.attempt(result["attempt"]).snapshot.packets[0]
    assert [message["id"] for message in frozen["context"]] == list(range(1, 8))
    # Another packet's image request describes the picture after this freeze.
    # The frozen context was just inside its bound without that wording.
    with rig.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (
                picture.key,
                picture.revision,
                json.dumps(dict(image_context="An authored chart. " + "D" * 240)),
            ),
        )
    await finish(rig, result["attempt"])
    assert rig.storage.attempt(result["attempt"]).state == "completed"
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == [20] and rig.storage.current(final)
    packet = rig.calls[-1][1]
    assert [message["id"] for message in packet["context"]] == list(range(2, 8))
    assert picture.key not in packet["inputs"]


def queued(rig, attempt):
    jobs = rig.store.rows(
        "SELECT * FROM jobs WHERE state='queued' AND json_extract(payload,'$.attempt')=?",
        (attempt,),
    )
    assert len(jobs) == 1
    return jobs[0]


async def test_an_unchanged_reread_keeps_the_briefing_and_regenerates_nothing(rig):
    source(rig, 1)
    updates = GroupUpdates(rig.app.briefings)
    result = updates.update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    calls = len(rig.calls)
    # Telegram could not replay what was missed, so intake reads the day again.
    rig.store.groups.mark_gap(KEY)
    rig.app.briefings.reconcile(KEY)
    assert rig.storage.state(KEY, DAY).final == final
    assert updates.view(KEY)["status"] == "up_to_date"
    assert updates.update(KEY) == dict(status="up_to_date", day=DAY)
    source(rig, 1)
    assert rig.storage.state(KEY, DAY).final == final
    assert updates.update(KEY) == dict(status="up_to_date", day=DAY)
    assert rig.app.briefings.planner.pending() == []
    assert len(rig.calls) == calls
    assert len(rig.store.rows("SELECT id FROM group_briefing_attempts")) == 1


async def test_new_work_waits_while_the_intake_reads_the_day_again(rig):
    source(rig, 1)
    updates = GroupUpdates(rig.app.briefings)
    await finish(rig, updates.update(KEY)["attempt"])
    final = rig.storage.state(KEY, DAY).final
    rig.store.groups.mark_gap(KEY)
    # A new original arrives while the earlier one is not proven again yet.
    source(rig, 2, author_peer="user:8")
    rig.now[0] += 31
    # The scheduler neither starts a partial day nor stops on a refusal.
    rig.manager.dispatch()
    assert rig.app.briefings.next_job() is None
    assert rig.app.briefings.local_idle() in (True, False)
    assert len(rig.store.rows("SELECT id FROM group_briefing_attempts")) == 1
    view = updates.view(KEY)
    assert view["status"] == "syncing" and "verified again" in view["reason"]
    assert view["transient"] is False and not view["enabled"]
    with pytest.raises(Refused, match="verified again"):
        updates.update(KEY)
    assert rig.storage.state(KEY, DAY).final == final
    # The read reaches the earlier original: the day can be updated, once.
    source(rig, 1)
    result = updates.update(KEY)
    assert result["status"] == "started"
    await finish(rig, result["attempt"])
    assert rig.storage.state(KEY, DAY).final.covered_ids == [1, 2]


async def test_a_running_update_waits_for_the_reread_and_then_finishes(rig):
    source(rig, 1)
    result = GroupUpdates(rig.app.briefings).update(KEY)
    rig.store.groups.mark_gap(KEY)
    rig.app.briefings.reconcile(KEY)
    # Nothing was seen to change, so the update stands; its next stage is
    # admitted only once the read has proven the original again.
    assert not rig.app.briefings.claim(queued(rig, result["attempt"]))
    assert rig.storage.attempt(result["attempt"]).state == "active"
    assert not rig.calls
    source(rig, 1)
    await finish(rig, result["attempt"])
    assert rig.storage.attempt(result["attempt"]).state == "completed"
    assert rig.storage.state(KEY, DAY).final.covered_ids == [1]


async def test_a_new_epoch_during_the_final_request_keeps_its_publication(rig):
    source(rig, 1)
    result = GroupUpdates(rig.app.briefings).update(KEY)
    while json.loads(queued(rig, result["attempt"])["payload"])["stage"] != "final":
        job = queued(rig, result["attempt"])
        assert rig.app.briefings.claim(job)
        await rig.app.briefings.run(job)
    started, release = asyncio.Event(), asyncio.Event()

    async def hold(role, packet):
        if role == "group_briefing":
            started.set()
            await release.wait()

    rig.model_hook = hold
    job = queued(rig, result["attempt"])
    assert rig.app.briefings.claim(job)
    running = asyncio.create_task(rig.app.briefings.run(job))
    await asyncio.wait_for(started.wait(), 2)
    try:
        rig.store.groups.mark_gap(KEY)
        rig.app.briefings.reconcile(KEY)
    finally:
        release.set()
        await running
    assert rig.storage.attempt(result["attempt"]).state == "completed"
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == [1] and rig.storage.current(final)


async def test_an_original_the_reread_misses_withdraws_the_briefing_for_an_update(rig):
    source(rig, 1)
    source(rig, 2)
    updates = GroupUpdates(rig.app.briefings)
    result = updates.update(KEY)
    await finish(rig, result["attempt"])
    rig.store.groups.mark_gap(KEY)
    source(rig, 2)
    intake = rig.store.groups.intake(KEY)
    rig.store.groups.scanned(
        KEY,
        epoch=intake.epoch,
        before=1,
        top=2,
        cutoff=NOW - 7 * 86400,
        oldest=NOW,
        count=1,
        complete=True,
    )
    assert rig.storage.state(KEY, DAY).final is None
    assert updates.view(KEY)["status"] == "ready"


def another_group(rig, peer="channel:43", *, enabled=True, account=1):
    app = rig.manager.get(account)
    key = f"{account}/{peer}"
    app.store.put_chat(
        Chat(account_id=account, peer=peer, title="Other authored group", kind="group"),
        activity=rig.now[0],
    )
    app.store.groups.observe(key, access="available", reason="authored_native")
    app.set_group_processing(key, enabled)
    app.sync.ingest(
        Message(
            account_id=account,
            peer=peer,
            message_id=1,
            revision=1,
            date=rig.now[0],
            text="An authored rollout source.",
            author="Dana",
            author_peer="user:8",
        ),
        group_epoch=app.store.groups.intake(key).epoch,
    )
    return key


def test_rollout_freezes_day_and_enabled_set_without_later_opt_in_expansion(rig):
    source(rig, 1, date=NOW - 3600)
    off = another_group(rig, enabled=False)
    rollout = GroupBriefingRollout(rig.manager, "authored-readability")
    before = rollout.start()
    assert [(t["key"], t["day"]) for t in before["targets"]] == [(KEY, DAY)]
    rig.app.set_group_processing(off, True)
    rig.now[0] += 86400
    again = GroupBriefingRollout(rig.manager, "authored-readability").start()
    assert again == before
    assert not rig.store.rows("SELECT id FROM group_briefing_attempts")


def test_rollout_freezes_enabled_accounts_and_finishes_empty_today_without_calls(rig):
    source(rig, 1, date=NOW - 86400)
    rig.manager._create(rig.manager.registry.register(2, "authored-second.session"))
    second = rig.manager.get(2)
    second.store.clock = lambda: rig.now[0]
    later = another_group(rig, account=2)
    rig.manager.set_processing(2, False)
    rollout = GroupBriefingRollout(rig.manager, "authored-readability")
    assert [target["key"] for target in rollout.start()["targets"]] == [KEY]
    rig.manager.set_processing(2, True)
    assert rollout.tick() is None
    assert rollout.read()["targets"] == [
        dict(account_id=1, key=KEY, day=DAY, state="empty")
    ]
    assert later not in {target["key"] for target in rollout.read()["targets"]}
    assert not rig.calls and not second.engine.calls


def test_rollout_bypasses_only_interval_and_preserves_quiet_and_budget(rig):
    source(rig, 1)
    old = rig.storage.begin_batch(KEY, automatic=True)
    rig.storage.finish_batch(old.id)
    rollout = GroupBriefingRollout(rig.manager, "authored-readability")
    assert rollout.tick() is None
    assert not rig.store.rows("SELECT id FROM group_briefing_attempts")
    rig.now[0] += 31
    rig.manager.registry.set_budget(0, family="group_briefing")
    assert rollout.tick() is None
    assert not rig.store.rows("SELECT id FROM group_briefing_attempts")
    rig.manager.registry.set_budget(2_000_000_000, family="group_briefing")
    assert rollout.tick()["status"] == "started"
    assert rig.storage.run_state(KEY).last_auto_started == old.started


async def test_rollout_freezes_cached_targets_before_native_connection_is_ready(rig):
    source(rig, 1)
    rig.now[0] += 31
    await restart(rig)
    connected = [False]

    def runtime_admission():
        if not connected[0]:
            raise ProcessingPaused("Authored native connection is still starting")

    rig.app.group_runtime_admit = runtime_admission
    rollout = GroupBriefingRollout(rig.manager, "authored-startup")
    assert [target["key"] for target in rollout.start()["targets"]] == [KEY]
    assert rollout.tick() is None
    assert rollout.read()["targets"][0]["state"] == "pending"
    assert not rig.app.store.rows("SELECT id FROM group_briefing_attempts")
    connected[0] = True
    rig.now[0] += 16
    assert rollout.tick()["status"] == "started"
    assert rollout.read()["targets"][0]["day"] == DAY
    assert not rig.calls


async def test_rollout_pauses_off_resumes_frozen_day_and_preserves_automatic_clock(rig):
    source(rig, 1, date=NOW - 3600)
    rig.now[0] += 31
    old = rig.storage.begin_batch(KEY, automatic=True)
    rig.storage.finish_batch(old.id)
    clock = rig.storage.run_state(KEY).last_auto_started
    rollout = GroupBriefingRollout(rig.manager, "authored-readability")
    rollout.start()
    rig.app.set_group_processing(KEY, False)
    assert rollout.tick() is None
    assert rollout.read()["targets"][0]["state"] == "pending"
    rig.now[0] += 86400
    rig.app.set_group_processing(KEY, True)
    restarted = GroupBriefingRollout(rig.manager, "authored-readability")
    accepted = restarted.tick()
    assert accepted["status"] == "started" and accepted["day"] == DAY
    await finish(rig, accepted["attempt"])
    restarted.tick()
    assert restarted.read()["targets"][0]["state"] == "completed"
    assert rig.storage.run_state(KEY).last_auto_started == clock
    assert rig.storage.state(KEY, DAY).final.covered_ids == [1]
    assert not rig.store.rows(
        "SELECT id FROM group_briefing_results WHERE day!=?", (DAY,)
    )


def test_rollout_keeps_character_gate_and_rechecks_it_before_new_stages(rig):
    source(rig, 1, text="Short authored source.")
    rig.now[0] += 31
    rig.manager.set_processing_threshold(10000)
    rollout = GroupBriefingRollout(rig.manager, "authored-readability")
    assert rollout.tick() is None
    assert not rig.store.rows("SELECT id FROM group_briefing_attempts")
    rig.manager.set_processing_threshold(0)
    result = rollout.tick()
    assert result["status"] == "started"
    job = rig.store.rows(
        "SELECT payload FROM jobs WHERE json_extract(payload,'$.attempt')=?",
        (result["attempt"],),
    )[0]
    payload = json.loads(job["payload"])
    rig.manager.set_processing_threshold(10000)
    with pytest.raises(Refused, match="character"):
        rollout.admit(rig.app.briefings, payload)
    assert not rig.calls


@pytest.mark.parametrize("origin", ["rollout", "manual", "retry"])
async def test_threshold_hold_releases_memory_only_for_rollout_work(rig, origin):
    for identity in range(1, 7):
        source(rig, identity, text="😀" * 1200)
    rig.now[0] += 31
    rollout = GroupBriefingRollout(rig.manager, "authored-memory-admission")
    if origin == "manual":
        result = GroupUpdates(rig.app.briefings).update(KEY)
    else:
        result = rollout.tick()
        assert result["status"] == "started"
        if origin == "retry":
            original = rig.storage.attempt(result["attempt"])
            rig.store.recover()
            rollout.tick()
            assert rollout.read()["targets"][0]["state"] == "retry_required"
            job = rig.app.briefings.retry(original.id)
            result = dict(attempt=json.loads(job["payload"])["attempt"])
            assert result["attempt"] != original.id

    attempt = rig.storage.attempt(result["attempt"])
    snapshot = attempt.snapshot
    clock = rig.storage.run_state(KEY).last_auto_started
    for _ in range(4):
        first = rig.app.briefings.next_job()
        assert rig.app.briefings.claim(first)
        await rig.app.briefings.run(first)
        if rig.calls:
            break
    assert [role for role, _ in rig.calls] == ["group_briefing_chunk"]
    assert rig.storage.batch(attempt.batch_id).requests == 1

    rig.manager.set_processing_threshold(10000)
    spending = rig.manager.registry.rows("SELECT * FROM model_reservations")
    if origin == "rollout":
        assert not rig.app.briefings.planner.active_pending()
        assert rig.app.briefings.planner.active_pending(ignore_threshold=True)
        assert rig.manager.memory_idle()
        assert rig.app.briefings.next_job() is None
    else:
        assert rig.app.briefings.planner.active_pending()
        assert not rig.manager.memory_idle()
        assert rig.app.briefings.next_job()
    assert len(rig.calls) == 1
    assert rig.manager.registry.rows("SELECT * FROM model_reservations") == spending
    assert rig.storage.attempt(attempt.id).snapshot == snapshot
    assert rig.storage.run_state(KEY).last_auto_started == clock

    # The held rollout resumes the same frozen work; manual Update and Retry
    # finish with the high threshold still set, qualifying their bypass.
    if origin == "rollout":
        rig.manager.set_processing_threshold(0)
        assert not rig.manager.memory_idle()
    await finish(rig, attempt.id)
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == list(range(1, 7))
    assert rig.storage.batch(attempt.batch_id).requests <= 5
    assert rig.storage.run_state(KEY).last_auto_started == clock


def test_interrupted_started_rollout_stays_retry_only_after_restart(rig):
    source(rig, 1)
    rig.now[0] += 31
    rollout = GroupBriefingRollout(rig.manager, "authored-readability")
    result = rollout.tick()
    snapshot = rig.storage.attempt(result["attempt"]).snapshot
    rig.store.recover()
    resumed = GroupBriefingRollout(rig.manager, "authored-readability")
    resumed.tick()
    assert resumed.read()["targets"][0]["state"] == "retry_required"
    assert len(rig.store.rows("SELECT id FROM group_briefing_attempts")) == 1
    assert rig.storage.attempt(result["attempt"]).snapshot == snapshot
    assert not rig.calls


def test_http_update_requires_trusted_browser_and_never_accepts_a_day_override(
    tmp_path,
):
    directory = tmp_path / "served"
    server = create_app(Settings(data=directory))
    with TestClient(server, base_url="http://127.0.0.1:8765") as client:
        manager = server.state.accounts
        app = manager.get(1)
        app.store.put_chat(
            Chat(
                account_id=1,
                peer="channel:42",
                title="Authored HTTP group",
                kind="group",
            ),
            activity=app.store.clock(),
        )
        app.store.groups.observe(KEY, access="available", reason="authored_native")
        app.set_group_processing(KEY, True)
        app.sync.ingest(
            Message(
                account_id=1,
                peer="channel:42",
                message_id=1,
                revision=1,
                date=app.store.clock(),
                text="An authored HTTP update.",
            ),
            group_epoch=app.store.groups.intake(KEY).epoch,
        )
        base = "/api/accounts/1/chats/channel:42/briefings/update"
        assert client.post(base, json={}).status_code == 403
        client.headers["Origin"] = str(client.base_url).rstrip("/")
        assert client.post(base, json={}).status_code == 401
        trust_browser(client, directory)
        state = client.get(base)
        assert state.status_code == 200 and state.json()[
            "day"
        ] == app.briefings.planner._day(app.store.clock())
        accepted = client.post(base, json={"day": "2000-01-01"})
        assert accepted.status_code == 200
        assert accepted.json()["day"] == state.json()["day"]
        assert not app.transport.exact_reads
