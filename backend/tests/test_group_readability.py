"""Readability and filtering publication over authored cached sources only."""

import pytest
from enloq.application.group_updates import GroupUpdates
from enloq.domain.briefing_contract import (
    BriefingError,
    validate_group_output,
)
from enloq.domain.models import Refused

from backend.tests.test_group_pipeline_reuse import DAY, KEY, create, source
from backend.tests.test_group_updates import finish


@pytest.fixture
async def rig(tmp_path):
    value = create(tmp_path, classify_non_ads=False)
    value.store, value.storage = value.app.store, value.app.store.group_briefings
    yield value
    await value.manager.close()


def topic(ids, **changes):
    return dict(
        title="A stated proposal",
        text="Alex proposed Friday; Bea has not confirmed.",
        citations=ids,
        participant_source_ids=ids,
        **changes,
    )


def test_v2_requires_explicit_title_and_native_participant_sources():
    packet = dict(
        contract_version=2, messages=[dict(id=1), dict(id=2)], covered_ids=[1, 2]
    )
    value = dict(
        contract_version=2, outcome="briefing", topics=[topic([2]), topic([1])]
    )
    validated = validate_group_output("group_briefing", value, packet)
    assert [t.citations for t in validated.topics] == [[1], [2]]
    assert validated.text.startswith("A stated proposal\n")
    for mutation in (
        dict(title=None),
        dict(participant_source_ids=[]),
        dict(participant_source_ids=[99]),
    ):
        bad = dict(value, topics=[{**topic([1]), **mutation}])
        with pytest.raises(BriefingError):
            validate_group_output("group_briefing", bad, packet)
    legacy = dict(
        outcome="briefing", topics=[dict(text="A retained old summary.", citations=[1])]
    )
    assert (
        validate_group_output(
            "group_briefing", legacy, {**packet, "contract_version": 1}
        ).text
        == "A retained old summary."
    )
    with pytest.raises(BriefingError):
        validate_group_output("group_briefing", legacy, packet)


async def test_all_ads_are_handled_locally_without_a_generative_request(rig):
    source(rig, 1, text="Buy our authored tour. Contact our seller.")
    with rig.store.transaction() as db:
        db.execute("DELETE FROM group_filter_decisions")
    rig.app.decider.group_probability = 0.99
    accepted = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, accepted["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert final.covered_ids == final.excluded_ids == final.ad_ids == [1]
    assert final.local_outcome == "ads_skipped" and final.output.topics == []
    assert not rig.calls
    batch = rig.storage.batch(rig.storage.attempt(accepted["attempt"]).batch_id)
    assert batch.classifier_requests == 1 and batch.requests == 0
    jobs = rig.store.rows("SELECT * FROM jobs")
    assert GroupUpdates(rig.app.briefings).update(KEY)["status"] == "up_to_date"
    assert rig.store.rows("SELECT * FROM jobs") == jobs
    assert rig.app.conversation(KEY)["messages"][0]["group_filter"]["ad"]


def test_classifier_and_generation_have_independent_durable_caps(rig):
    source(rig, 1)
    batch = rig.storage.begin_batch(KEY)
    for i in range(12):
        rig.storage.record_work(
            batch.id, classifier_requests=1, source_ids=[1], dispatch_token=f"ad:{i}"
        )
    with pytest.raises(Refused, match="work limit"):
        rig.storage.record_work(
            batch.id, classifier_requests=1, source_ids=[1], dispatch_token="ad:13"
        )
    for i in range(5):
        rig.storage.record_work(
            batch.id, requests=1, source_ids=[1], dispatch_token=f"text:{i}"
        )
    saved = rig.storage.batch(batch.id)
    assert (
        saved.requests == 5
        and saved.classifier_requests == 12
        and saved.source_ids == [1]
    )


async def test_native_participant_names_and_exclusion_citations_are_guarded(rig):
    source(rig, 1, author="Alex", author_peer="user:7")
    source(rig, 2, author="Bea", author_peer="user:8")
    with rig.store.transaction() as db:
        db.execute("DELETE FROM group_filter_decisions")
    accepted = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, accepted["attempt"])
    final = rig.storage.state(KEY, DAY).final
    view = rig.app.briefings.view(KEY)[0]["final"]
    assert view["topics"][0]["participants"] == [dict(peer="user:7", name="Alex")]
    assert view["topics"][1]["participants"] == [dict(peer="user:8", name="Bea")]
    # A disposition never grants a generated topic permission to cite it.
    newer = rig.storage.freeze(
        KEY,
        DAY,
        final.snapshot.packets,
        intended_ids=[1, 2],
        guidance_hash="authored-changed-rules",
        route=final.snapshot.route,
    )
    attempt = rig.storage.create_attempt(newer)
    with pytest.raises(Refused, match="Invalid final briefing"):
        rig.storage.publish(
            attempt.id,
            dict(contract_version=2, outcome="briefing", topics=[topic([1, 2])]),
            covered_ids=[1, 2],
            excluded_ids=[2],
            ad_ids=[2],
        )
