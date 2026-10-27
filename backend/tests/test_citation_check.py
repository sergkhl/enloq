"""ADR 34's citation check with authored decisions, offline only."""

import copy
import json

import pytest
from enloq.application.group_updates import GroupUpdates
from enloq.domain.citation_check import (
    CHECK_ROLE,
    ClaimCheck,
    checked_packet,
    kept,
    make_packet,
    questions,
)
from enloq.domain.models import Refused
from enloq.inference import decider
from enloq.inference.base import InvalidOutput, ProviderRefused

from backend.tests.test_group_pipeline_reuse import DAY, KEY, create, source
from backend.tests.test_group_updates import finish


def original(identity, text="An authored note.", **values):
    return {"id": identity, "text": text, "speaker": {"name": "Alex"}, **values}


CLAIM = {"title": "Workshop", "text": "The workshop moved to Wednesday afternoon."}


def test_one_question_per_citation_and_one_for_the_whole_claim():
    packet = make_packet(CHECK_ROLE, CLAIM, [original(3), original(5)], [original(2)])
    assert set(questions(packet)) == {"cites_3", "cites_5", "claim"}
    assert checked_packet(packet) == packet
    state = json.loads(packet["state"])
    assert state["claim"] == CLAIM
    assert [m["id"] for m in state["cited_messages"]] == [3, 5]
    assert [m["id"] for m in state["reply_context"]] == [2]


def test_packets_are_whole_bounded_and_proved():
    for cited, context in (
        ([original(3), original(3)], []),
        ([original(i) for i in range(1, 33)], []),
        ([original(3, text="x" * 25_000)], []),
        ([original(3)], [original(3)]),
        ([], []),
    ):
        with pytest.raises(Refused):
            make_packet(CHECK_ROLE, CLAIM, cited, context)
    packet = make_packet(CHECK_ROLE, CLAIM, [original(3)])
    with pytest.raises(Refused):
        checked_packet({**packet, "cited_ids": [4]})
    with pytest.raises(Refused):
        checked_packet(
            {**packet, "state": packet["state"].replace("Wednesday", "Friday")}
        )


def test_floor_removals_and_participants():
    check = ClaimCheck(
        status="checked",
        support=[
            {"message_id": 1, "probability": 0.5},
            {"message_id": 2, "probability": 0.49},
        ],
        claim=0.2,
    )
    assert check.removed == {2}
    assert ClaimCheck(status="unchecked", reason="refused").removed == set()
    authors = {1: "user:7", 2: "user:8", 3: "user:8", 4: None}.get
    # A removed participant source hands its author to a remaining citation.
    assert kept([1, 2, 3, 4], [1, 2, 4], {2, 4}, authors) == ([1, 3], [1, 3])
    # Without one the author goes, and an unknown author is never substituted.
    assert kept([1, 2, 4], [1, 2, 4], {2, 4}, authors) == ([1], [1])


def test_answers_need_exactly_the_asked_names():
    packet = make_packet(CHECK_ROLE, CLAIM, [original(3)])
    answers = {
        "cites_3": {"type": "noul", "noul": 0.2},
        "claim": {"type": "noul", "noul": 0.7},
    }
    assert decider.validate(CHECK_ROLE, answers, packet) == {
        "cites_3": 0.2,
        "claim": 0.7,
    }
    for broken in (
        {"claim": answers["claim"]},
        {**answers, "cites_4": answers["claim"]},
    ):
        with pytest.raises(InvalidOutput):
            decider.validate(CHECK_ROLE, broken, packet)


@pytest.fixture
async def rig(tmp_path):
    value = create(tmp_path, check=True)
    value.store, value.storage = value.app.store, value.app.store.group_briefings
    try:
        yield value
    finally:
        await value.manager.close()


def topic(citations, participants, text="An authored topic."):
    return dict(
        title="Authored",
        text=text,
        citations=citations,
        participant_source_ids=participants,
    )


def final_topics(rig, topics):
    previous = rig.app.engine.generate

    async def generate(role, packet):
        value = await previous(role, packet)
        if role == "group_briefing":
            return dict(
                contract_version=2, outcome="briefing", topics=copy.deepcopy(topics)
            )
        return value

    rig.app.engine.generate = generate


async def briefing(rig):
    result = GroupUpdates(rig.app.briefings).update(KEY)
    await finish(rig, result["attempt"])
    return rig.storage.state(KEY, DAY).final


def shown(rig):
    day = next(d for d in rig.app.briefings.view(KEY) if d["day"] == DAY)
    return day["final"]["topics"]


def checks(rig):
    return [packet for role, packet in rig.app.decider.calls if role == CHECK_ROLE]


async def test_unsupported_citations_leave_with_their_participants(rig):
    source(rig, 1)
    source(rig, 2, author="Sam", author_peer="user:8")
    source(rig, 3, author="Sam", author_peer="user:8")
    final_topics(rig, [topic([1, 2, 3], [1, 2]), topic([2], [2])])
    rig.app.decider.citation_support = {2: 0.1}
    final = await briefing(rig)
    # The final is kept as written; the check is stored beside it.
    assert final.output.topics[0].citations == [1, 2, 3]
    assert [c.status for c in final.citation_check.topics] == ["checked"] * 2
    assert len(checks(rig)) == 2
    # The second topic lost its only citation and is hidden (Q4).
    [first] = shown(rig)
    assert first["citations"] == [1, 3]
    assert first["participant_source_ids"] == [1, 3]
    assert [p["name"] for p in first["participants"]] == ["Alex", "Sam"]
    rig.app.briefings.sources(KEY, DAY, final.id, 3)
    with pytest.raises(Refused):
        rig.app.briefings.sources(KEY, DAY, final.id, 2)


async def test_reply_context_reaches_the_check_but_ignored_bots_do_not(rig):
    source(rig, 1, text="Station one has the small blue board.")
    source(rig, 2, author_peer="user:9", sender_is_bot=True, text="Bot advert.")
    source(rig, 3, author="Sam", author_peer="user:8", reply_to=1, text="Yes, that.")
    source(rig, 4, author="Sam", author_peer="user:8", reply_to=2, text="Thanks.")
    final_topics(rig, [topic([3, 4], [3])])
    await briefing(rig)
    state = json.loads(checks(rig)[0]["state"])
    assert [m["id"] for m in state["cited_messages"]] == [3, 4]
    assert [m["id"] for m in state["reply_context"]] == [1]
    assert all("peer" not in m["speaker"] for m in state["cited_messages"])


async def test_a_refused_route_publishes_unchecked_after_one_call(rig):
    source(rig, 1)
    source(rig, 2)
    final_topics(rig, [topic([1], [1]), topic([2], [2])])

    async def refuse(role, packet):
        rig.app.decider.calls.append((role, packet))
        raise ProviderRefused("Model provider HTTP 429")

    rig.app.decider.generate = refuse
    final = await briefing(rig)
    assert [(c.status, c.reason) for c in final.citation_check.topics] == [
        ("unchecked", "refused")
    ] * 2
    assert len(checks(rig)) == 1
    assert [t["citations"] for t in shown(rig)] == [[1], [2]]


async def test_a_malformed_answer_leaves_only_its_topic_unchecked(rig):
    source(rig, 1)
    source(rig, 2)
    final_topics(rig, [topic([1], [1]), topic([2], [2])])
    answer = rig.app.decider.generate

    async def malformed(role, packet):
        value = await answer(role, packet)
        if role == CHECK_ROLE and packet["cited_ids"] == [1]:
            value.pop("claim")
        return value

    rig.app.decider.generate = malformed
    final = await briefing(rig)
    assert [(c.status, c.reason) for c in final.citation_check.topics] == [
        ("unchecked", "format"),
        ("checked", None),
    ]


async def test_an_update_checks_at_most_twelve_topics(rig):
    for identity in range(1, 14):
        source(rig, identity)
    final_topics(rig, [topic([i], [i]) for i in range(1, 14)])
    final = await briefing(rig)
    assert [c.reason for c in final.citation_check.topics] == [None] * 12 + ["limit"]
    assert len(checks(rig)) == 12
    assert (
        rig.storage.batch(rig.storage.attempt(final.attempt_id).batch_id).check_requests
        == 12
    )


async def test_a_spent_budget_publishes_the_final_unchecked(rig):
    source(rig, 1)
    final_topics(rig, [topic([1], [1])])
    result = GroupUpdates(rig.app.briefings).update(KEY)
    while True:
        job = rig.store.rows(
            "SELECT * FROM jobs WHERE state='queued' AND json_extract(payload,'$.attempt')=?",
            (result["attempt"],),
        )[0]
        if json.loads(job["payload"])["stage"] == "check":
            break
        assert rig.app.briefings.claim(job)
        await rig.app.briefings.run(job)
    rig.manager.registry.set_budget(0, family="group_briefing")
    await finish(rig, result["attempt"])
    final = rig.storage.state(KEY, DAY).final
    assert [(c.status, c.reason) for c in final.citation_check.topics] == [
        ("unchecked", "budget")
    ]
    assert not checks(rig)


async def test_a_changed_source_during_the_check_withdraws_the_final(rig):
    source(rig, 1)
    source(rig, 2)
    final_topics(rig, [topic([1], [1]), topic([2], [2])])
    answer = rig.app.decider.generate

    async def edit_then_answer(role, packet):
        if role == CHECK_ROLE and packet["cited_ids"] == [1]:
            source(rig, 2, revision=2, text="An authored edit.")
        return await answer(role, packet)

    rig.app.decider.generate = edit_then_answer
    result = GroupUpdates(rig.app.briefings).update(KEY)
    for _ in range(30):
        jobs = rig.store.rows(
            "SELECT * FROM jobs WHERE state='queued' AND json_extract(payload,'$.attempt')=?",
            (result["attempt"],),
        )
        if not jobs or not rig.app.briefings.claim(jobs[0]):
            break
        await rig.app.briefings.run(jobs[0])
    rig.app.briefings.reconcile(KEY)
    assert rig.storage.state(KEY, DAY).final is None
    assert rig.storage.attempt(result["attempt"]).state != "completed"
