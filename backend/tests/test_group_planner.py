"""Automatic cached-day admission, independent of native/provider execution."""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.application import group_context
from enloq.application.group_planner import (
    AUTO_INTERVAL,
    MAX_BACKOFF,
    CharacterDeferred,
    Planner,
)
from enloq.domain.models import Chat, Message, ProcessingPaused, Refused
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import WINDOW, Store

NOW = datetime(2026, 10, 11, 12, tzinfo=UTC).timestamp()
KEY, DAY = "1/channel:42", "2026-10-11"


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
    app = SimpleNamespace(
        store=store,
        group_runtime_admit=lambda: None,
        account_processing_enabled=lambda: registry.processing(1)["ai_enabled"],
    )
    result = SimpleNamespace(
        store=store, registry=registry, clock=clock, app=app, planner=Planner(app)
    )
    group(result)
    yield result
    store.close()


def group(rig, peer="channel:42", *, enabled=True):
    key = f"1/{peer}"
    rig.store.put_chat(
        Chat(account_id=1, peer=peer, title="Authored group", kind="group")
    )
    rig.store.groups.observe(key, access="available", reason="authored_native")
    intake = rig.store.groups.intake(key)
    rig.store.groups.scanned(
        key,
        epoch=intake.epoch,
        before=0,
        top=0,
        cutoff=rig.clock[0] - WINDOW,
        oldest=None,
        count=0,
        complete=True,
    )
    rig.store.groups.set_processing_enabled(key, enabled)
    return key


def source(rig, identity, *, peer="channel:42", notify=True, **values):
    message = Message(
        account_id=1,
        peer=peer,
        message_id=identity,
        revision=0,
        date=rig.clock[0],
        text="A" * 200,
        author="Alex",
        author_peer="user:7",
    ).model_copy(update=values)
    key = f"1/{peer}"
    rig.store.apply_message(message, group_epoch=rig.store.groups.intake(key).epoch)
    with rig.store.transaction() as db:
        db.execute("DELETE FROM cursors WHERE key=?", ("group-effects:" + message.key,))
    saved = rig.store.message(key, identity)
    if notify and saved is not None:
        rig.planner.changed(saved)
    return saved


def settled(rig):
    rig.clock[0] += 30
    return rig.planner.selection(KEY)


def attempt(rig, ids, *, day=DAY, automatic=False, omissions=()):
    originals = [rig.store.message(KEY, identity) for identity in ids]
    packets = [
        group_context.packet(
            rig.store,
            KEY,
            day,
            chunk,
        )
        for chunk in group_context.partition(originals).chunks
    ]
    snapshot = rig.store.group_briefings.freeze(
        KEY,
        day,
        packets,
        intended_ids=ids,
        omissions=omissions,
        guidance_hash="authored-group-guidance",
        route="openrouter/xiaomi/mimo-v2.6-flash@xiaomi",
    )
    return rig.store.group_briefings.create_attempt(snapshot, automatic=automatic)


def complete(rig, ids):
    value = attempt(rig, ids)
    final = rig.store.group_briefings.publish(
        value.id,
        dict(outcome="briefing", topics=[dict(text="Authored topic.", citations=ids)]),
        covered_ids=ids,
    )
    rig.store.group_briefings.finish_batch(value.batch_id)
    return final


def test_first_opt_in_uses_all_today_cached_originals_but_no_prior_day(rig):
    group(rig, enabled=False)
    source(rig, 1, date=NOW - 86400)
    source(rig, 2, date=NOW - 3600, outgoing=True, author_peer="user:99")
    source(rig, 3, date=NOW - 1800, author_peer="user:8")
    source(rig, 4, service=True, text="Joined the group")
    source(rig, 5, media="video", text="")
    assert rig.planner.pending(include_deferred=True) == []
    rig.store.groups.set_processing_enabled(KEY, True)
    selected = settled(rig)
    assert selected.day == DAY and selected.intended_ids == selected.new_ids == (2, 3)
    assert selected.sources[0].outgoing
    assert rig.store.groups.get(KEY).first_enabled_at == NOW


def test_midnight_catch_up_since_opt_in_day_is_newest_first(rig):
    rig.clock[0] = datetime(2026, 10, 11, 23, 59, 50, tzinfo=UTC).timestamp()
    group(rig, peer="chat:42", enabled=False)
    key = "1/chat:42"
    rig.store.groups.set_processing_enabled(key, True)
    source(rig, 1, peer="chat:42", date=NOW - 86400)
    source(rig, 2, peer="chat:42", date=NOW)
    rig.clock[0] += 60
    source(rig, 3, peer="chat:42")
    rig.clock[0] += 30
    selections = rig.planner.pending(key)
    assert [(value.day, value.intended_ids) for value in selections] == [
        ("2026-10-12", (3,)),
        (DAY, (2,)),
    ]
    rig.store.groups.set_processing_enabled(key, False)
    rig.store.groups.set_processing_enabled(key, True)
    assert rig.planner.pending(key) == selections


def test_automatic_target_window_is_rolling_seven_days(rig):
    source(rig, 1)
    rig.clock[0] += WINDOW - 1
    source(rig, 2)
    rig.clock[0] += 30
    assert [value.intended_ids for value in rig.planner.pending()] == [(2,)]
    # The old original remains readable; automatic selection never reaches it.
    assert rig.store.message(KEY, 1) is not None


def test_configured_timezone_owns_first_opt_in_day(rig):
    rig.store.timezone = "Asia/Ho_Chi_Minh"
    rig.clock[0] = datetime(2026, 10, 11, 18, tzinfo=UTC).timestamp()
    group(rig, peer="chat:43", enabled=False)
    key = "1/chat:43"
    rig.store.groups.set_processing_enabled(key, True)
    source(rig, 1, peer="chat:43", date=rig.clock[0] - 7200)
    source(rig, 2, peer="chat:43", date=rig.clock[0] - 1800)
    rig.clock[0] += 30
    value = rig.planner.selection(key)
    assert value.day == "2026-10-12" and value.intended_ids == (2,)


def test_quiet_is_durable_and_unchanged_replay_does_not_extend_it(rig):
    message = source(rig, 1)
    assert rig.planner.selection(KEY) is None and rig.planner.has_pending()
    rig.clock[0] += 29
    assert not rig.planner.changed(message, arrival=False)
    assert rig.planner.selection(KEY) is None
    rig.clock[0] += 1
    rig.planner = Planner(rig.app)
    assert rig.planner.selection(KEY).new_ids == (1,)
    # Display-name and newly learned forward metadata are presentation repairs.
    assert not rig.planner.changed(
        message.model_copy(update={"author": "Renamed", "forwarded_from": "Learned"})
    )
    assert rig.planner.selection(KEY) is not None
    source(rig, 1, author_peer="user:9")
    assert rig.planner.selection(KEY) is None
    assert settled(rig).new_ids == (1,)


def test_groups_with_same_numeric_id_have_independent_quiet_clocks(rig):
    other = group(rig, "chat:42")
    source(rig, 1)
    rig.clock[0] += 30
    source(rig, 1, peer="chat:42")
    assert rig.planner.selection(KEY) is not None
    assert rig.planner.selection(other) is None


def test_selection_does_not_start_jobs_attempts_batches_or_the_cadence_clock(rig):
    source(rig, 1)
    settled(rig)
    for _ in range(3):
        assert rig.planner.pending() and rig.planner.selection(KEY)
    assert rig.store.group_briefings.run_state(KEY).last_auto_started is None
    for table in (
        "jobs",
        "group_briefing_runs",
        "group_briefing_attempts",
        "group_briefing_batches",
        "model_reservations",
    ):
        if table == "model_reservations":
            assert rig.registry.rows("SELECT * FROM model_reservations") == []
        else:
            assert rig.store.rows(f"SELECT * FROM {table}") == []


def test_whole_day_trimmed_unicode_threshold_includes_owner_bot_and_captions(rig):
    source(rig, 1, text="  😀" * 1 + "A" * 97 + "  ")
    source(rig, 2, text="B" * 50, outgoing=True, author_peer="user:99")
    source(rig, 3, text="C" * 50, media="document", author_peer="user:8")
    source(rig, 4, text="Service" * 100, service=True)
    source(rig, 5, text="Supporting context" * 100, date=NOW - 86400)
    assert settled(rig) is None
    assert not rig.planner.has_pending() and rig.planner.has_pending(
        ignore_threshold=True
    )
    source(rig, 3, text="C" * 52, media="document", author_peer="user:8")
    assert settled(rig).intended_ids == (1, 2, 3)
    final = complete(rig, [1, 2, 3])
    source(rig, 6, text="Short addition")
    selected = settled(rig)
    assert selected.new_ids == (6,) and selected.intended_ids == (1, 2, 3, 6)
    assert rig.store.group_briefings.current(final)


def test_generated_derivatives_never_contribute_to_character_admission(rig):
    message = source(rig, 1, text="A" * 199)
    with rig.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (
                message.key,
                message.revision,
                json.dumps(
                    {"transcript": {"text": "B" * 1000}, "image_context": "C" * 1000}
                ),
            ),
        )
    assert settled(rig) is None and not rig.planner.has_pending()
    rig.registry.set_processing_threshold(199)
    assert rig.planner.selection(KEY) is not None


def test_boundary_day_counts_earlier_cached_originals_without_admitting_old_targets(
    rig,
):
    source(rig, 1, date=NOW - 3600, text="A" * 100)
    source(rig, 2, date=NOW - 900, text="B" * 100)
    rig.clock[0] = NOW + WINDOW - 1800
    selection = rig.planner.selection(KEY)
    assert selection.day == DAY and selection.intended_ids == (2,)
    assert rig.planner.admit(KEY, DAY, [2]) == selection
    assert rig.store.message(KEY, 1) is not None


@pytest.mark.parametrize("media", ["image", "audio", "video_note"])
def test_new_analysable_media_exempts_whole_update(rig, media):
    source(rig, 1, text="Earlier short text")
    source(rig, 2, text="", media=media, attachment_id="authored-attachment")
    selection = settled(rig)
    assert selection.new_ids == (1, 2)
    assert rig.planner.admit(KEY, DAY, selection.intended_ids) == selection


@pytest.mark.parametrize("media", ["document", "video", "gif", "sticker"])
def test_non_ai_media_does_not_exempt_a_below_threshold_caption(rig, media):
    source(rig, 1, media=media, text="A" * 199, attachment_id="authored-attachment")
    assert settled(rig) is None and not rig.planner.has_pending()
    source(rig, 1, media=media, text="A" * 200, attachment_id="authored-attachment")
    assert settled(rig).new_ids == (1,)


def test_media_covered_by_a_completed_result_does_not_exempt_later_short_text(rig):
    source(rig, 1, text="", media="image", attachment_id="authored-image")
    settled(rig)
    complete(rig, [1])
    assert rig.planner.pending() == []
    source(rig, 2, text="Short addition")
    assert settled(rig) is None and not rig.planner.has_pending()
    source(
        rig,
        1,
        text="Changed image caption",
        media="image",
        attachment_id="authored-image",
    )
    assert settled(rig).new_ids == (1, 2)


def test_other_target_edit_does_not_turn_completed_media_into_a_new_exemption(rig):
    source(rig, 1, text="", media="image", attachment_id="authored-image")
    source(rig, 2, text="Short initial text")
    settled(rig)
    complete(rig, [1, 2])
    source(rig, 2, text="Short edited text")
    assert settled(rig) is None
    assert not rig.planner.has_pending()


def test_later_media_cannot_exempt_a_frozen_text_only_attempt(rig):
    source(rig, 1)
    settled(rig)
    current = attempt(rig, [1], automatic=True)
    rig.registry.set_processing_threshold(1000)
    source(rig, 2, text="", media="image", attachment_id="authored-image")
    rig.clock[0] += 30
    with pytest.raises(CharacterDeferred):
        rig.planner.admit(KEY, DAY, current.snapshot.intended_ids, continuation=True)
    assert rig.store.group_briefings.current(current)


def test_threshold_change_preserves_started_cache_and_never_resets_cadence(rig):
    source(rig, 1)
    settled(rig)
    current = attempt(rig, [1], automatic=True)
    started = rig.store.group_briefings.run_state(KEY).last_auto_started
    rig.registry.set_processing_threshold(201)
    with pytest.raises(CharacterDeferred):
        rig.planner.admit(KEY, DAY, [1], continuation=True)
    assert not rig.planner.has_pending()
    assert rig.store.group_briefings.current(current)
    rig.registry.set_processing_threshold(200)
    assert rig.planner.admit(KEY, DAY, [1], continuation=True)
    assert rig.store.group_briefings.run_state(KEY).last_auto_started == started


def test_four_hour_start_clock_survives_midnight_restart_and_processing_toggles(rig):
    rig.clock[0] = datetime(2026, 10, 11, 23, 58, tzinfo=UTC).timestamp()
    source(rig, 1)
    settled(rig)
    batch = rig.store.group_briefings.begin_batch(KEY)
    rig.store.group_briefings.finish_batch(batch.id)
    started = batch.started
    rig.clock[0] += 120
    source(rig, 2)
    rig.clock[0] += 30
    rig.store.groups.set_processing_enabled(KEY, False)
    rig.store.groups.set_processing_enabled(KEY, True)
    path = rig.store.path
    rig.store.close()
    rig.store = Store(
        path,
        account_id=1,
        clock=lambda: rig.clock[0],
        timezone="UTC",
        shared_path=rig.registry.path,
    )
    rig.app.store = rig.store
    rig.planner = Planner(rig.app)
    assert rig.planner.selection(KEY) is None and rig.planner.has_pending()
    assert rig.store.group_briefings.run_state(KEY).last_auto_started == started
    rig.clock[0] = started + AUTO_INTERVAL
    assert [value.day for value in rig.planner.pending(KEY)] == ["2026-10-12", DAY]
    rig.clock[0] = started - 1
    assert rig.planner.pending(KEY) == []


def test_only_a_real_active_automatic_batch_can_bypass_cadence(rig):
    source(rig, 1)
    settled(rig)
    with pytest.raises(Refused, match="active batch"):
        rig.planner.admit(KEY, DAY, [1], continuation=True)
    batch = rig.store.group_briefings.begin_batch(KEY)
    assert rig.planner.selection(KEY) is None
    assert rig.planner.selection(KEY, continuation=True) is not None
    rig.store.group_briefings.finish_batch(batch.id)
    assert rig.planner.selection(KEY, continuation=True) is None
    explicit_batch = rig.store.group_briefings.begin_batch(KEY, automatic=False)
    with pytest.raises(Refused, match="active batch"):
        rig.planner.admit(KEY, DAY, [1], continuation=True)
    rig.store.group_briefings.finish_batch(explicit_batch.id)


def test_active_attempts_are_not_selected_as_new_automatic_work(rig):
    source(rig, 1)
    settled(rig)
    current = attempt(rig, [1], automatic=True)
    assert rig.planner.pending(include_deferred=True) == []
    assert rig.planner.has_pending()
    assert rig.planner.admit(KEY, DAY, [1], continuation=True)
    assert rig.store.group_briefings.current(current)


@pytest.mark.parametrize("interrupted", [False, True])
def test_unchanged_failed_native_snapshot_requires_retry_even_after_setting_changes(
    rig, interrupted
):
    source(rig, 1)
    settled(rig)
    current = attempt(rig, [1], automatic=True)
    rig.store.group_briefings.fail(
        current.id, "Authored counted/unknown outcome", interrupted=interrupted
    )
    rig.store.group_briefings.finish_batch(
        current.batch_id, state="interrupted" if interrupted else "failed"
    )
    rig.clock[0] += AUTO_INTERVAL
    rig.registry.set_processing_threshold(0)
    rig.registry.set_budget(
        3_000_000_000, clock=lambda: rig.clock[0], family="group_briefing"
    )
    assert rig.planner.pending(include_deferred=True) == []
    assert not rig.planner.has_pending()
    # A genuine new native post may form a new snapshot under ordinary admission.
    source(rig, 2, text="Added evidence")
    assert settled(rig).intended_ids == (1, 2)


def test_explicit_guard_reaches_retained_snapshot_but_keeps_current_switch_access_gates(
    rig,
):
    source(rig, 1, text="Short text")
    settled(rig)
    current = attempt(rig, [1], automatic=True)
    rig.store.group_briefings.fail(current.id, "Authored failure")
    rig.store.group_briefings.finish_batch(current.batch_id, state="failed")
    rig.clock[0] += 10 * 86400
    assert rig.planner.admit(KEY, DAY, [1], explicit=True).intended_ids == (1,)
    assert rig.planner.pending(include_deferred=True) == []
    rig.registry.set_processing(1, False)
    with pytest.raises(ProcessingPaused):
        rig.planner.admit(KEY, DAY, [1], explicit=True)
    rig.registry.set_processing(1, True)
    rig.store.groups.set_processing_enabled(KEY, False)
    with pytest.raises(ProcessingPaused):
        rig.planner.admit(KEY, DAY, [1], explicit=True)
    rig.store.groups.set_processing_enabled(KEY, True)
    rig.store.groups.observe(KEY, access="lost", reason="authored_loss")
    with pytest.raises(Refused):
        rig.planner.admit(KEY, DAY, [1], explicit=True)


def test_unchanged_failed_capped_snapshot_and_its_omissions_stay_retry_only(rig):
    for identity in range(1, 202):
        source(rig, identity, text="A")
    settled(rig)
    current = attempt(
        rig,
        list(range(1, 201)),
        automatic=True,
        omissions=[dict(message_id=201, reason="source work limit")],
    )
    rig.store.group_briefings.fail(current.id, "Authored counted failure")
    rig.store.group_briefings.finish_batch(current.batch_id, state="failed")
    rig.clock[0] += AUTO_INTERVAL
    assert rig.planner.pending(include_deferred=True) == []
    assert rig.planner.selection(KEY) is None
    assert rig.store.group_briefings.run_state(KEY).last_auto_started == current.created


def test_latest_policy_and_native_targets_are_rechecked_at_every_stage(rig):
    source(rig, 1)
    settled(rig)
    batch = rig.store.group_briefings.begin_batch(KEY)
    rig.planner.admit(KEY, DAY, [1], continuation=True)
    rig.store.groups.begin_replay(KEY, epoch=rig.store.groups.intake(KEY).epoch)
    with pytest.raises(ProcessingPaused, match="intake"):
        rig.planner.admit(KEY, DAY, [1], continuation=True)
    rig.store.groups.checkpoint(KEY, 1, epoch=rig.store.groups.intake(KEY).epoch)
    source(rig, 1, deleted=True)
    with pytest.raises(Refused, match="unavailable"):
        rig.planner.admit(KEY, DAY, [1], continuation=True)
    rig.store.group_briefings.finish_batch(batch.id)


@pytest.mark.parametrize("ids", [[], [True], [0], [-1], [1, 1], [1.0], ["1"], [999]])
def test_stage_target_identity_is_strict_and_cached(rig, ids):
    source(rig, 1)
    settled(rig)
    with pytest.raises(Refused):
        rig.planner.admit(KEY, DAY, ids)


def test_no_work_backoff_is_bounded_durable_and_new_evidence_clears_it(rig):
    message = source(rig, 1)
    settled(rig)
    until = rig.planner.refuse(KEY, delay=99999)
    assert until == rig.clock[0] + MAX_BACKOFF
    assert rig.planner.selection(KEY) is None and rig.planner.has_pending()
    rig.planner = Planner(rig.app)
    rig.clock[0] += 30
    assert not rig.planner.changed(message, arrival=False)
    assert rig.planner.selection(KEY) is None
    source(rig, 2)
    assert settled(rig) is not None
    rig.planner.refuse(KEY, delay=10)
    rig.clock[0] += 10
    assert rig.planner.selection(KEY) is not None


def test_held_database_reads_never_call_the_registry_or_reenter_store(rig, monkeypatch):
    source(rig, 1)
    settled(rig)
    rig.app.admit_ai = lambda: (_ for _ in ()).throw(AssertionError("Registry reentry"))
    rig.app.account_processing_enabled = rig.app.admit_ai
    with rig.store.transaction() as db:
        monkeypatch.setattr(rig.store, "transaction", rig.app.admit_ai)
        assert rig.planner.eligible(KEY, db=db)
        assert rig.planner.selection(KEY, db=db)
        assert rig.planner.admit(KEY, DAY, [1], db=db)


def test_runtime_refusal_and_wrong_account_scope_start_no_work(rig):
    source(rig, 1)
    settled(rig)

    def unavailable():
        raise ProcessingPaused("Waiting for this account's Telegram connection")

    rig.app.group_runtime_admit = unavailable
    assert rig.planner.pending() == [] and not rig.planner.has_pending()
    assert not rig.planner.eligible(KEY)
    rig.app.group_runtime_admit = lambda: None
    with pytest.raises(Refused):
        rig.planner.admit("2/channel:42", DAY, [1])
