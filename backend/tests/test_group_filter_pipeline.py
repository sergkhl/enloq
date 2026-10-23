"""Classifier-to-briefing integration regressions over authored local stores."""

import copy
import json

import pytest
from enloq.application import group_context
from enloq.application.group_updates import GroupUpdates
from enloq.application.media import transcript_fingerprint
from enloq.domain.group_filtering import AD_ROLE, BOT_ROLE

from backend.tests.group_test_support import briefing_output, known_non_ads
from backend.tests.test_group_briefing_views import freeze
from backend.tests.test_group_pipeline_reuse import (
    DAY,
    KEY,
    NOW,
    create,
    photo,
    settle,
    source,
)
from backend.tests.test_group_updates import finish


@pytest.fixture
async def rig(tmp_path):
    value = create(tmp_path, classify_non_ads=False)
    value.store, value.storage = value.app.store, value.app.store.group_briefings
    yield value
    await value.manager.close()


def derivative(rig, message, **fields):
    with rig.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (message.key, message.revision, json.dumps(fields)),
        )


def ignore(rig, *, day=DAY):
    with rig.store.transaction() as db:
        rig.store.group_filters.ignore_bot(
            KEY,
            "user:7",
            day=day,
            fingerprint="authored-ten-message-decision",
            probability=0.99,
            db=db,
        )


def saved_chunk(rig, identity, *, prepared=None):
    snapshot = freeze(rig, [identity])
    attempt = rig.storage.create_attempt(snapshot)
    chunk = rig.storage.store_chunk(
        attempt.id, [identity], briefing_output([identity]), packet=prepared
    )
    final = rig.storage.publish(
        attempt.id,
        briefing_output([identity]),
        covered_ids=[identity],
        chunk_ids=[chunk.id],
        filter_state=rig.app.briefings.filtering.state_hash(KEY),
    )
    rig.storage.finish_batch(attempt.batch_id)
    return chunk, final


async def run_next(rig, attempt, expected):
    jobs = rig.store.rows(
        "SELECT * FROM jobs WHERE state='queued' AND json_extract(payload,'$.attempt')=?",
        (attempt,),
    )
    assert len(jobs) == 1
    assert json.loads(jobs[0]["payload"])["stage"] == expected
    assert rig.app.briefings.claim(jobs[0])
    await rig.app.briefings.run(jobs[0])


async def test_excluded_cached_speech_publishes_with_exact_consumed_fingerprint(rig):
    spoken = source(rig, 1, text="", media="audio")
    transcript = dict(text="Buy the authored promotion.", language="en")
    derivative(rig, spoken, transcript=transcript)
    rig.app.decider.group_probability = 0.99
    updates = GroupUpdates(rig.app.briefings)
    result = updates.update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and final.excluded_ids == final.ad_ids == [1]
    assert final.snapshot.packets[0]["transcript_inputs"] == {
        spoken.key: transcript_fingerprint(transcript)
    }
    assert rig.storage.current(final)
    assert not rig.calls and len(rig.app.decider.calls) == 1
    assert updates.update(KEY)["status"] == "up_to_date"
    assert len(rig.app.decider.calls) == 1


def test_completed_speech_keeps_completed_source_creation_guard(rig):
    spoken = source(rig, 1, media="audio", text="Authored spoken progress.")
    transcript = dict(text="We finished the authored task.", language="en")
    derivative(rig, spoken, transcript=transcript)
    packet = copy.deepcopy(freeze(rig, [1]).packets[0])
    packet["messages"][0]["transcript"] = transcript
    packet["transcript_inputs"] = {spoken.key: transcript_fingerprint(transcript)}
    _, final = saved_chunk(rig, 1, prepared=packet)
    assert rig.storage.current(final)
    assert not rig.storage.can_create(freeze(rig, [1]))


async def test_ignored_supporting_context_never_reaches_summary_or_media(rig):
    source(rig, 1, date=NOW - 86400, sender_is_bot=True, text="An ignored bot advert.")
    ignore(rig, day="2026-10-10")
    source(rig, 2, author_peer="user:8", reply_to=1)
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == [2]
    assert len(rig.calls) == 1
    packet = rig.calls[0][1]
    assert packet["context"] == []
    for field in (
        "inputs",
        "attribution_inputs",
        "speaker_inputs",
        "image_inputs",
        "transcript_inputs",
    ):
        assert f"{KEY}/1" not in packet.get(field, {})
    assert rig.storage.current(final)


async def test_mixed_two_hundred_source_update_keeps_proofs_outside_model_bound(rig):
    messages = [
        source(rig, identity, text="Authored substantive post: " + "x" * 70)
        for identity in range(1, 201)
    ]
    filtering = rig.app.briefings.filtering
    excluded = [41, 81, 121, 161]
    for identity in excluded:
        request = filtering._request(KEY, DAY, AD_ROLE, [messages[identity - 1]])
        filtering.storage.record(request, {f"ad_{identity}": 0.99})
    # Ordinary authored discussion already has exact non-ad outcomes. This
    # isolates model packet bounds without dispatching another classifier.
    known_non_ads(rig.app)
    packets = [
        group_context.packet(rig.store, KEY, DAY, chunk)
        for chunk in group_context.partition(messages, store=rig.store).chunks
    ]
    assert any(
        group_context.encoded(filtering.exclude(packet)) > group_context.PACKET_BYTES
        for packet in packets
    )
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == list(range(1, 201))
    assert final.excluded_ids == final.ad_ids == excluded
    assert not final.omissions and len(final.filter_evidence) == 4
    assert rig.storage.attempt(result["attempt"]).state == "completed"
    assert rig.storage.current(final)
    assert not rig.app.decider.calls
    assert 1 <= len(rig.calls) <= 5
    metadata = {"filter_evidence", "exclusion_ids", "ad_ids", "excluded_context_ids"}
    assert all(not metadata.intersection(packet) for _, packet in rig.calls)
    source(rig, 40, revision=2, text="A corrected substantive supporting point.")
    assert not rig.storage.current(final)


async def test_new_ignore_cannot_reuse_earlier_summary_chunk(rig):
    source(rig, 1, sender_is_bot=True)
    chunk, _ = saved_chunk(rig, 1)
    ignore(rig)
    selected = rig.app.briefings.planner.manual(KEY)
    reused, covered = rig.app.briefings._reusable(selected, rig.app.briefings._guide())
    assert not reused and not covered
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and final.excluded_ids == [1] and final.local_outcome == "bots_skipped"
    assert chunk.id not in final.chunk_ids and not rig.calls


async def test_new_image_meaning_cannot_reuse_caption_only_chunk(rig):
    message = source(rig, 1, media="image", text="Authored caption.")
    old, _ = saved_chunk(rig, 1)
    derivative(
        rig, message, image_context="The authored chart reports 42 completed tasks."
    )
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and old.id not in final.chunk_ids
    assert len(rig.calls) == 1
    assert (
        rig.calls[0][1]["messages"][0]["image_context"]
        == "The authored chart reports 42 completed tasks."
    )
    assert GroupUpdates(rig.app.briefings).update(KEY)["status"] == "up_to_date"


async def test_new_context_ad_rebuilds_prior_notes_without_that_supporting_text(rig):
    advert = source(rig, 1, date=NOW - 86400, text="An authored advertisement.")
    source(rig, 2, author_peer="user:8", reply_to=1)
    saved_chunk(rig, 2)
    filtering = rig.app.briefings.filtering
    request = filtering._request(KEY, "2026-10-10", AD_ROLE, [advert])
    filtering.storage.record(request, {"ad_1": 0.99})
    source(rig, 3, author_peer="user:8")
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == [2, 3]
    assert all(f"{KEY}/1" not in packet["inputs"] for packet in final.snapshot.packets)
    assert any(
        message["id"] == 2 for _, packet in rig.calls for message in packet["messages"]
    )
    assert final.filter_evidence
    source(
        rig,
        1,
        date=NOW - 86400,
        revision=advert.revision + 1,
        text="An authored correction with useful information.",
    )
    assert not rig.storage.current(final)


async def test_owner_allow_during_final_rejects_stale_exclusions(rig):
    source(rig, 1, sender_is_bot=True)
    ignore(rig)
    source(rig, 2, author_peer="user:8")
    result = GroupUpdates(rig.app.briefings).update(KEY)

    async def allow_during_call(role, packet):
        assert role == "group_briefing" and packet["covered_ids"] == [2]
        assert rig.app.briefings.filtering.restore_bot(KEY, "user:7")

    rig.model_hook = allow_during_call
    await finish(rig, result["attempt"])
    assert not rig.storage.state(KEY, DAY).final
    assert rig.storage.attempt(result["attempt"]).state == "failed"
    assert not rig.app.briefings.filtering.annotations(rig.store.message(KEY, 1))[
        "ignored_bot"
    ]


async def test_changed_ad_media_before_publication_cannot_stamp_stale_exclusion(rig):
    spoken = source(rig, 1, text="", media="audio")
    derivative(
        rig, spoken, transcript=dict(text="An authored promotion.", language="en")
    )
    rig.app.decider.group_probability = 0.99
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await run_next(rig, result["attempt"], "filter")
    assert rig.app.briefings.filtering.annotations(spoken)["ad"]
    derivative(
        rig,
        spoken,
        transcript=dict(text="An authored urgent bridge closure.", language="en"),
    )
    assert not rig.app.briefings.filtering.annotations(spoken)["ad"]
    assert rig.app.briefings.next_job() is None
    assert not rig.storage.state(KEY, DAY).final
    assert rig.storage.attempt(result["attempt"]).state != "active"
    assert not rig.calls


async def test_media_prepared_by_the_same_update_keeps_its_earlier_exclusion(rig):
    picture = photo(rig, 1)
    advert = source(rig, 2, author_peer="user:8", text="Buy the authored promotion.")
    filtering = rig.app.briefings.filtering
    request = filtering._request(KEY, DAY, AD_ROLE, [advert])
    # The classifier read the neighbouring picture before it had a meaning.
    assert picture.key in request["evidence"]
    filtering.storage.record(request, {"ad_2": 0.99})
    known_non_ads(rig.app)
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, result["attempt"])
    assert rig.storage.attempt(result["attempt"]).state == "completed"
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == [1, 2]
    assert final.excluded_ids == final.ad_ids == [2]
    assert rig.storage.current(final)
    cached = rig.store.rows(
        "SELECT body FROM derivatives WHERE source=?", (picture.key,)
    )
    assert json.loads(cached[0]["body"])["image_context"]
    # No second classifier pass inside this update; a later one reads the
    # prepared meaning and classifies that packet again.
    assert not rig.app.decider.calls
    del filtering.plan
    later = filtering.plan(KEY, [rig.store.message(KEY, 2)], DAY)
    assert [item["target_keys"] for item in later] == [[advert.key]]


async def test_routine_replay_during_a_classifier_request_keeps_its_answer(
    rig, monkeypatch
):
    advert = source(rig, 1, text="Buy the authored promotion.")
    decider = rig.app.decider
    decider.group_probability = 0.99
    generate = decider.generate

    async def answered_during_replay(role, packet):
        answer = await generate(role, packet)
        rig.store.groups.begin_replay(KEY, epoch=rig.store.groups.intake(KEY).epoch)
        return answer

    monkeypatch.setattr(decider, "generate", answered_during_replay)
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await run_next(rig, result["attempt"], "filter")
    assert len(decider.calls) == 1
    decisions = rig.store.group_filters.source_decisions(advert.key)
    assert [(item["pending"], item["failed"]) for item in decisions] == [(False, False)]
    assert rig.app.briefings.filtering.annotations(advert)["ad"]


async def test_hide_bots_leaves_verified_bots_out_of_reading_and_briefing(rig):
    source(rig, 1, author_peer="user:8", sender_is_bot=False)
    notice = source(rig, 2, sender_is_bot=True, text="The authored bridge is closed.")
    source(rig, 3, author_peer="user:9")
    updates = GroupUpdates(rig.app.briefings)
    result = updates.update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == [1, 2, 3]
    assert final.excluded_ids == [2] and not final.ad_ids
    summarized = [m["id"] for _, packet in rig.calls for m in packet["messages"]]
    assert summarized == [1, 3]
    # No model judged the bot: not as an advertisement, not as an author.
    assert all(role == AD_ROLE for role, _ in rig.app.decider.calls)
    assert 2 not in [
        i for _, packet in rig.app.decider.calls for i in packet["target_ids"]
    ]
    assert rig.app.briefings.filtering.annotations(notice)["ignored_bot"]
    assert updates.update(KEY) == dict(status="up_to_date", day=DAY)


async def test_a_day_of_only_bot_posts_completes_without_a_model_call(rig):
    source(rig, 1, sender_is_bot=True)
    source(rig, 2, sender_is_bot=True, author_peer="user:8")
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and final.local_outcome == "bots_skipped"
    assert final.excluded_ids == [1, 2]
    assert not rig.calls and not rig.app.decider.calls


async def test_turning_hide_bots_off_makes_the_day_updateable_and_reads_the_bot(rig):
    source(rig, 1, author_peer="user:8", sender_is_bot=False)
    source(rig, 2, sender_is_bot=True, text="The authored bridge is closed.")
    updates = GroupUpdates(rig.app.briefings)
    await finish(rig, updates.update(KEY)["attempt"])
    first = rig.storage.state(KEY, DAY).final
    rig.manager.registry.set_hide_bots(False)
    # A reading preference withdraws nothing; the next update follows it.
    assert rig.storage.state(KEY, DAY).final == first
    assert updates.view(KEY)["status"] == "ready"
    await finish(rig, updates.update(KEY)["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final.id != first.id and not final.excluded_ids
    assert 2 in [m["id"] for _, packet in rig.calls for m in packet["messages"]]
    rig.manager.registry.set_hide_bots(True)
    assert updates.view(KEY)["status"] == "ready"


async def test_an_exempted_bot_is_read_while_hide_bots_stays_on(rig):
    source(rig, 1, author_peer="user:8", sender_is_bot=False)
    source(rig, 2, sender_is_bot=True, text="The authored bridge is closed.")
    assert rig.app.briefings.filtering.restore_bot(KEY, "user:7")
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == [1, 2] and not final.excluded_ids


async def test_guidance_refresh_reapplies_exclusions_before_rebuilding_packets(rig):
    source(rig, 1, text="Buy an authored advertising package.")
    source(rig, 2, author_peer="user:8", text="The authored bridge is closed today.")

    async def classify(role, packet):
        rig.app.decider.calls.append((role, packet))
        return {
            f"ad_{identity}": {"type": "noul", "noul": 0.99 if identity == 1 else 0.0}
            for identity in packet["target_ids"]
        }

    rig.app.decider.generate = classify
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await run_next(rig, result["attempt"], "filter")
    rig.manager.registry.set_guidance(
        {"group_briefing": "Retain every authored warning and uncertainty."}
    )
    await finish(rig, result["attempt"])
    assert rig.calls and all(1 not in packet["covered_ids"] for _, packet in rig.calls)
    final = rig.storage.state(KEY, DAY).final
    assert final and final.excluded_ids == final.ad_ids == [1]
    assert final.covered_ids == [1, 2]
    assert len(rig.app.decider.calls) == 1


@pytest.mark.parametrize("failure", ["format", "unknown"])
async def test_classifier_failure_keeps_sources_and_does_not_repeat(failure, rig):
    source(rig, 1)

    async def fail(role, packet):
        rig.app.decider.calls.append((role, packet))
        if failure == "unknown":
            raise ConnectionError("Authored uncertain classifier result")
        return {"unrecognized": "authored invalid decision"}

    rig.app.decider.generate = fail
    updates = GroupUpdates(rig.app.briefings)
    result = updates.update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == [1] and final.excluded_ids == []
    batch = rig.storage.batch(rig.storage.attempt(result["attempt"]).batch_id)
    assert batch.requests == batch.classifier_requests == 1
    assert [role for role, _ in rig.app.decider.calls] == [AD_ROLE]
    assert updates.update(KEY)["status"] == "up_to_date"
    assert len(rig.app.decider.calls) == len(rig.calls) == 1


async def test_bot_sample_outside_fresh_cap_is_skipped_without_failing_briefing(rig):
    rig.manager.registry.set_hide_bots(False)
    for identity in range(1, 10):
        source(rig, identity, date=NOW - 86400, sender_is_bot=True)
    source(rig, 10, sender_is_bot=True)
    for identity in range(11, 210):
        source(rig, identity, author_peer="user:8")
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and rig.storage.current(final)
    batch = rig.storage.batch(rig.storage.attempt(result["attempt"]).batch_id)
    assert batch.source_ids == list(range(10, 210))
    assert batch.requests <= 5 and batch.classifier_requests <= 12
    assert all(role != BOT_ROLE for role, _ in rig.app.decider.calls)


async def test_all_ad_first_day_advances_same_frozen_automatic_batch(tmp_path):
    rig = create(tmp_path, first_enabled=NOW - 86400, classify_non_ads=False)
    rig.store, rig.storage = rig.app.store, rig.app.store.group_briefings
    try:
        source(rig, 1, text="An authored useful update from yesterday.")
        rig.now[0] = NOW
        source(rig, 2, text="An authored advertisement from today.")
        rig.now[0] += 31

        async def classify(role, packet):
            rig.app.decider.calls.append((role, packet))
            assert role == AD_ROLE
            return {
                f"ad_{identity}": {
                    "type": "noul",
                    "noul": 0.99 if identity == 2 else 0.0,
                }
                for identity in packet["target_ids"]
            }

        rig.app.decider.generate = classify
        first = rig.app.briefings.next_job()
        assert first and rig.app.briefings.claim(first)
        frozen = json.loads(first["payload"])
        assert [day["snapshot"]["day"] for day in frozen["days"]] == ["2026-10-10"]
        clock = rig.storage.run_state(KEY).last_auto_started
        await rig.app.briefings.run(first)
        await settle(rig)
        today = rig.storage.state(KEY, DAY).final
        yesterday = rig.storage.state(KEY, "2026-10-10").final
        assert today and today.local_outcome == "ads_skipped"
        assert yesterday and yesterday.covered_ids == [1]
        batches = rig.store.rows("SELECT id FROM group_briefing_batches")
        assert len(batches) == 1 and batches[0]["id"] == frozen["batch"]
        batch = rig.storage.batch(frozen["batch"])
        assert batch.state == "completed" and batch.requests == 1
        assert batch.classifier_requests == 2 and batch.source_ids == [1, 2]
        assert [packet["day"] for _, packet in rig.calls] == ["2026-10-10"]
        assert rig.storage.run_state(KEY).last_auto_started == clock
    finally:
        await rig.manager.close()


async def test_bot_arrival_during_native_enrichment_does_not_replace_frozen_sample(rig):
    rig.manager.registry.set_hide_bots(False)
    for identity in range(1, 11):
        source(rig, identity, sender_is_bot=None)

    async def refresh(peer, authors):
        assert peer == "channel:42" and authors == ["user:7"]
        rig.store.group_filters.native_bot(KEY, "user:7", True)
        source(rig, 11, sender_is_bot=True)
        return 10

    async def classify(role, packet):
        rig.app.decider.calls.append((role, packet))
        if role == BOT_ROLE:
            return {"all_ten_useless": {"type": "noul", "noul": 0.99}}
        return {
            f"ad_{identity}": {"type": "noul", "noul": 0.0}
            for identity in packet["target_ids"]
        }

    rig.app.transport.refresh_group_authors = refresh
    rig.app.decider.generate = classify
    result = GroupUpdates(rig.app.briefings).update(KEY)
    frozen = rig.storage.attempt(result["attempt"]).snapshot
    assert frozen.bot_samples == {
        "user:7": [f"{KEY}/{identity}" for identity in range(10, 0, -1)]
    }
    await run_next(rig, result["attempt"], "native")
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final and final.covered_ids == list(range(1, 11))
    assert not rig.store.group_filters.bot(KEY, "user:7")
    assert [role for role, _ in rig.app.decider.calls] == [AD_ROLE]
    assert all(11 not in packet["target_ids"] for _, packet in rig.app.decider.calls)
    assert all(f"{KEY}/11" not in packet["inputs"] for _, packet in rig.calls)
    assert rig.storage.attempt(result["attempt"]).snapshot == frozen
