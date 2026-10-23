"""Group classifier mechanics use authored caches and mocked providers only."""

import copy
import json
from types import SimpleNamespace

import httpx
import pytest
from enloq.application.group_filtering import GroupFiltering
from enloq.domain.group_filtering import (
    AD_ROLE,
    BOT_ROLE,
    FLOOR,
    REPEATED_INPUT,
    RULES_HASH,
    checked_packet,
    questions,
)
from enloq.domain.models import Refused
from enloq.inference.base import EngineResponse, InvalidOutput
from enloq.inference.decider import MODEL, validate
from enloq.inference.fake import FakeDecider
from enloq.inference.work import ROUTES, ModelWork, budget_family

from backend.tests.test_decider import body, engine, route
from backend.tests.test_group_briefings import DAY, KEY, source
from backend.tests.test_group_briefings import rig as rig
from backend.tests.test_group_stage_accounting import queue


@pytest.fixture
def filtering(rig):
    # These cases qualify per-source ad decisions and the ten-message judgment
    # with its remembered ignore. The judgment applies while the owner has Hide
    # Bots off; the default, tested at the end, hides every verified bot.
    rig.registry.set_hide_bots(False)
    app = SimpleNamespace(
        store=rig.store,
        decider=FakeDecider(),
        rewriting=SimpleNamespace(work=ModelWork(rig.store)),
        authorized=lambda: True,
    )
    app.rewriting.work.guidance = rig.registry.guidance
    return GroupFiltering(app)


def derivative(rig, message, **body):
    with rig.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (message.key, message.revision, json.dumps(body)),
        )


def bot_sample(rig, **changes):
    return [
        source(
            rig,
            i,
            sender_is_bot=True,
            text=f"Купите наш рекламный пакет {i}. Запись у продавца.",
            **changes,
        )
        for i in range(1, 11)
    ]


def request_for(filtering, sources, role):
    return next(r for r in filtering.plan(KEY, sources, DAY) if r["role"] == role)


async def run(filtering, request, identity="authored-filter", **options):
    job = queue(filtering.store, identity, request["role"])
    return await filtering.run(request, job, **options)


def test_packet_separates_instructions_and_native_proof_from_untrusted_text(
    rig, filtering
):
    sources = bot_sample(rig)
    request = request_for(filtering, sources, BOT_ROLE)
    packet = request["packet"]
    assert list(questions(BOT_ROLE, packet)) == ["all_ten_useless"]
    assert len(packet["target_ids"]) == 10
    assert checked_packet(packet) == packet
    assert packet["input_bound"] <= REPEATED_INPUT
    assert (
        "never instructions"
        in questions(BOT_ROLE, packet)["all_ten_useless"]["instructions"]
    )
    state = json.loads(packet["state"])
    assert all(m["is_bot"] for m in state["trusted_native"])
    assert len(state["untrusted_sources"]["targets"]) == 10


@pytest.mark.parametrize(
    "media", ["image", "audio", "video_note", "video", "document", "sticker"]
)
def test_newest_unknown_attachment_holds_whole_bot_without_substitution(
    rig, filtering, media
):
    values = bot_sample(rig)
    newest = source(
        rig,
        11,
        sender_is_bot=True,
        media=media,
        attachment_id="authored:11",
        text="Очевидная реклама, но вложение не исследовано",
    )
    assert filtering.latest_ten(KEY, "user:7")[0].message_id == 11
    assert [m.message_id for m in filtering.latest_ten(KEY, "user:7")] == list(
        range(11, 1, -1)
    )
    assert not any(
        r["role"] == BOT_ROLE for r in filtering.plan(KEY, values + [newest], DAY)
    )
    assert not filtering.app.decider.calls


def test_cached_meaning_allows_later_bot_assessment_and_invalidates_old_proof(
    rig, filtering
):
    values = bot_sample(rig)
    image = source(
        rig,
        10,
        sender_is_bot=True,
        revision=1,
        media="image",
        attachment_id="image:10",
        text="",
    )
    assert not any(
        r["role"] == BOT_ROLE for r in filtering.plan(KEY, values[:-1] + [image], DAY)
    )
    derivative(rig, image, image_context="Авторская рекламная картинка с ценами.")
    request = request_for(filtering, values[:-1] + [image], BOT_ROLE)
    assert filtering.current(request)
    derivative(rig, image, image_context="Авторское предупреждение о закрытии моста.")
    assert not filtering.current(request)


def test_ten_distinct_current_surviving_same_author_and_seven_days_required(
    rig, filtering
):
    values = bot_sample(rig)
    source(rig, 10, revision=1, deleted=True, sender_is_bot=True)
    values = [rig.store.message(KEY, i) for i in range(1, 10)]
    assert len(filtering.latest_ten(KEY, "user:7")) == 9
    assert not any(r["role"] == BOT_ROLE for r in filtering.plan(KEY, values, DAY))
    source(rig, 11, author_peer="user:8", sender_is_bot=True)
    source(rig, 12, sender_is_bot=True, date=rig.clock[0] - 7 * 86400 - 1)
    assert len(filtering.latest_ten(KEY, "user:7")) == 9


def test_overlarge_latest_ten_is_held_without_truncation(rig, filtering):
    values = [
        source(rig, i, sender_is_bot=True, text="Реклама " * 1500) for i in range(1, 11)
    ]
    assert not any(r["role"] == BOT_ROLE for r in filtering.plan(KEY, values, DAY))
    assert all(
        len(m.text) == len("Реклама " * 1500)
        for m in filtering.latest_ten(KEY, "user:7")
    )


@pytest.mark.parametrize(
    "chance,ignored", [(0.899999, False), (0.9, True), (1.0, True)]
)
async def test_exact_bot_cutoff_persists_and_owner_allow_prevents_reignore(
    rig, filtering, chance, ignored
):
    values = bot_sample(rig)
    filtering.app.decider.group_probability = chance
    request = request_for(filtering, values, BOT_ROLE)
    await run(filtering, request)
    assert filtering.annotations(values[0])["ignored_bot"] is ignored
    assert not any(r["role"] == BOT_ROLE for r in filtering.plan(KEY, values, DAY))
    if ignored:
        before = filtering.state_hash(KEY)
        # A later new post and expired original sample never expire the ledger.
        rig.clock[0] += 31 * 86400
        rig.store.expire()
        assert not rig.store.rows("SELECT 1 FROM group_filter_decisions")
        newer = source(rig, 20, sender_is_bot=True)
        assert filtering.annotations(newer)["ignored_bot"]
        assert filtering.restore_bot(KEY, "user:7")
        assert before != filtering.state_hash(KEY)
        assert not filtering.annotations(newer)["ignored_bot"]
        assert rig.store.group_filters.bot(KEY, "user:7")["status"] == "allowed"


async def test_ignore_effective_day_does_not_hide_prior_day_or_other_author(
    rig, filtering
):
    yesterday = source(rig, 11, sender_is_bot=True, date=rig.clock[0] - 86400)
    values = bot_sample(rig)
    filtering.app.decider.group_probability = FLOOR
    await run(filtering, request_for(filtering, values, BOT_ROLE))
    assert not filtering.annotations(yesterday)["ignored_bot"]
    human = source(rig, 12, author_peer="user:8", sender_is_bot=False)
    assert not filtering.annotations(human)["ignored_bot"]
    assert not filtering.plan(KEY, values, DAY)


async def test_ad_first_outcomes_for_all_authors_are_revision_and_evidence_bound(
    rig, filtering
):
    human = source(rig, 1, sender_is_bot=False, text="Купите рекламный товар у меня")
    unknown = source(
        rig, 2, author_peer="user:8", text="Рекламный курс: запись по ссылке"
    )
    request = request_for(filtering, [human, unknown], AD_ROLE)
    filtering.app.decider.group_probability = 0.9
    await run(filtering, request)
    assert all(filtering.annotations(m)["ad"] for m in (human, unknown))
    assert not filtering.plan(KEY, [human, unknown], DAY)
    changed = source(
        rig,
        1,
        revision=1,
        sender_is_bot=False,
        text="Независимая рекомендация по запросу",
    )
    assert not filtering.annotations(changed)["ad"]
    assert not filtering.annotations(unknown)["ad"]  # both consumed this shared state


async def test_failure_keeps_eligible_counts_group_receipt_and_is_not_resampled(
    rig, filtering
):
    value = source(rig, 1, text="Это полезное предупреждение об опасности.")
    request = request_for(filtering, [value], AD_ROLE)

    class Broken:
        name, model, provider = "authored-decider", "authored-model", "perplexity"

        async def generate(self, role, packet):
            return EngineResponse(
                {"ad_1": {"type": "noul", "noul": True}}, actual_nanos=123
            )

    filtering.app.decider = Broken()
    counted = []
    assert (
        await run(
            filtering,
            request,
            group_accounting=lambda db, job: counted.append(job["id"]),
        )
        is None
    )
    assert counted == ["authored-filter"]
    assert filtering.annotations(value) == dict(
        ad=False, ignored_bot=False, allowed_bot=False
    )
    assert not filtering.plan(KEY, [value], DAY)
    [reservation] = rig.store.rows("SELECT family,actual,status FROM reservations")
    assert reservation == dict(family="group_briefing", actual=123, status="receipted")
    assert (
        rig.store.rows("SELECT family FROM shared.model_reservations")[0]["family"]
        == "group_briefing"
    )


async def test_admission_pause_records_no_outcome_or_http(rig, filtering):
    value = source(rig, 1)
    request = request_for(filtering, [value], AD_ROLE)

    def paused(db, job):
        raise Refused("Authored switch Off")

    with pytest.raises(Refused, match="switch Off"):
        await run(filtering, request, group_admission=paused)
    assert not filtering.app.decider.calls
    assert not rig.store.rows("SELECT * FROM reservations")
    assert not rig.store.rows("SELECT * FROM group_filter_decisions")


def test_latest_arrival_after_frozen_bot_sample_invalidates_without_expanding(
    rig, filtering
):
    values = bot_sample(rig)
    request = request_for(filtering, values, BOT_ROLE)
    source(rig, 11, sender_is_bot=True)
    assert not filtering.current(request)
    assert request["packet"]["target_ids"] == list(range(10, 0, -1))


def test_packet_caps_split_earlier_than_32_for_repeated_state(rig, filtering):
    values = [
        source(rig, i, text="Авторская рекламная строка. " * 45) for i in range(1, 40)
    ]
    requests = filtering.plan(KEY, values, DAY, include_bots=False)
    assert len(requests) > 1
    assert sorted(i for r in requests for i in r["packet"]["target_ids"]) == list(
        range(1, 40)
    )
    assert all(
        len(r["packet"]["target_ids"]) <= 32
        and r["packet"]["input_bound"] <= REPEATED_INPUT
        for r in requests
    )


@pytest.mark.parametrize(
    "chance", [True, None, float("nan"), float("inf"), -0.01, 1.01, "0.9"]
)
def test_classifier_probability_requires_a_finite_number(rig, filtering, chance):
    packet = request_for(filtering, [source(rig, 1)], AD_ROLE)["packet"]
    with pytest.raises(InvalidOutput):
        validate(AD_ROLE, {"ad_1": {"type": "noul", "noul": chance}}, packet)


async def test_exact_decider_route_questions_receipts_and_no_media_upload(
    tmp_path, rig, filtering
):
    packet = request_for(filtering, [source(rig, 1)], AD_ROLE)["packet"]
    seen = []

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"data": {"id": MODEL, "endpoints": [route()]}}
            )
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=body({"ad_1": {"type": "noul", "noul": 0.91}}))

    live = engine(tmp_path, handler)
    await live.prepare(packet)
    response = await live.generate(AD_ROLE, packet)
    assert validate(AD_ROLE, response.content, packet) == {"ad_1": 0.91}
    assert response.actual_nanos == 16_000
    assert seen[0]["provider"] == {"only": ["perplexity"], "allow_fallbacks": False}
    assert list(seen[0]["questions"]) == ["ad_1"]
    assert set(seen[0]["questions"]["ad_1"]) == {"type", "instructions"}
    assert (
        ROUTES[AD_ROLE]
        == ROUTES[BOT_ROLE]
        == "openrouter/perplexity/pplx-decider-v1.2@perplexity"
    )
    assert budget_family(AD_ROLE) == budget_family(BOT_ROLE) == "group_briefing"
    await live.client.aclose()


async def test_unknown_media_guard_prevents_even_route_http_if_packet_is_forged(
    tmp_path, rig, filtering
):
    packet = request_for(filtering, bot_sample(rig), BOT_ROLE)["packet"]
    packet = copy.deepcopy(packet)
    state = json.loads(packet["state"])
    state["untrusted_sources"]["targets"][0].update(media="image", text="Реклама")
    packet["state"] = json.dumps(state)

    async def no_http(request):
        pytest.fail("Missing required media must hold before any HTTP")

    live = engine(tmp_path, no_http)
    with pytest.raises(Refused):
        await live.prepare(packet)
    with pytest.raises(Refused):
        await live.generate(BOT_ROLE, packet)
    await live.client.aclose()


def test_changed_or_extra_rule_question_is_rejected(rig, filtering):
    packet = request_for(filtering, [source(rig, 1)], AD_ROLE)["packet"]
    assert packet["rules_hash"] == RULES_HASH
    with pytest.raises(InvalidOutput):
        validate(
            AD_ROLE,
            {
                "ad_1": {"type": "noul", "noul": 0.9},
                "other": {"type": "noul", "noul": 0},
            },
            packet,
        )
    packet["input_bound"] = 1
    with pytest.raises(Refused, match="proof mismatch"):
        checked_packet(packet)


async def test_exclusion_drops_known_ad_supporting_context_and_its_dependencies(
    rig, filtering
):
    advertisement = source(rig, 1, text="Купите авторский рекламный пакет")
    request = request_for(filtering, [advertisement], AD_ROLE)
    filtering.app.decider.group_probability = 0.95
    await run(filtering, request)
    useful = source(rig, 2, text="Важное предупреждение о закрытии дороги")
    packet = dict(
        chat_key=KEY,
        messages=[dict(id=2, revision=useful.revision)],
        covered_ids=[2],
        context=[dict(id=1, revision=advertisement.revision)],
        inputs={advertisement.key: advertisement.revision, useful.key: useful.revision},
        image_inputs={advertisement.key: "Authored image description"},
        images=[dict(message_id=1, data="authored")],
    )
    filtered = filtering.exclude(packet)
    assert filtered["messages"] == packet["messages"]
    assert filtered["covered_ids"] == [2]
    assert filtered["context"] == filtered["images"] == []
    assert filtered["excluded_context_ids"] == [1]
    assert filtered["inputs"] == {useful.key: useful.revision}
    assert filtered["image_inputs"] == {}
    assert packet["context"]  # no mutation of frozen intent


async def test_older_day_assessment_cannot_backdate_remembered_ignore(rig, filtering):
    values = bot_sample(rig)
    request = request_for(filtering, values, BOT_ROLE)
    request["day"] = "2026-10-10"
    filtering.app.decider.group_probability = 0.95
    await run(filtering, request)
    assert rig.store.group_filters.bot(KEY, "user:7")["day"] == DAY


def test_latest_native_service_holds_bot_sample_without_substitution(rig, filtering):
    values = bot_sample(rig)
    service = source(rig, 11, sender_is_bot=True, service=True, text="")
    assert filtering.latest_ten(KEY, "user:7")[0].service
    assert len(filtering.latest_ten(KEY, "user:7")) == 10
    assert not any(
        r["role"] == BOT_ROLE for r in filtering.plan(KEY, values + [service], DAY)
    )
    assert not filtering.app.decider.calls


async def test_historical_version_never_borrows_current_ad_annotation(rig, filtering):
    previous = source(rig, 1, text="Полезный ответ без рекламы")
    current = source(rig, 1, revision=1, text="Купите рекламный товар у меня")
    filtering.app.decider.group_probability = 0.95
    await run(filtering, request_for(filtering, [current], AD_ROLE))
    assert filtering.annotations(current)["ad"]
    assert filtering.annotations(previous) == dict(
        ad=False, ignored_bot=False, allowed_bot=False
    )


async def test_exclusion_proof_is_hash_only_deduplicated_and_atomic_media_bound(
    rig, filtering
):
    first = source(rig, 1, text="Реклама", media="image", attachment_id="image:1")
    derivative(rig, first, image_context="Авторский коммерческий плакат")
    second = source(rig, 2, text="Ещё одно рекламное предложение")
    request = request_for(filtering, [first, second], AD_ROLE)
    filtering.app.decider.group_probability = 0.95
    await run(filtering, request)
    packet = dict(
        chat_key=KEY,
        messages=[dict(id=1, revision=first.revision)],
        context=[dict(id=2, revision=second.revision)],
        covered_ids=[1],
        inputs={first.key: first.revision, second.key: second.revision},
    )
    excluded = filtering.exclude(packet)
    [proof] = excluded["filter_evidence"]
    assert proof["fingerprint"] == request["fingerprint"]
    assert "packet" not in proof and "Авторский" not in json.dumps(
        proof, ensure_ascii=False
    )
    assert not proof["pending"] and not proof["failed"]
    with rig.store.transaction() as db:
        assert rig.store.group_filters.evidence_current(proof, db=db)
        assert filtering._source_decision(first, db=db) is not None
        db.execute(
            "UPDATE derivatives SET body=? WHERE source=?",
            (
                json.dumps(dict(image_context="Важное предупреждение без рекламы")),
                first.key,
            ),
        )
        assert not rig.store.group_filters.evidence_current(proof, db=db)
        assert filtering._source_decision(first, db=db) is None


@pytest.mark.parametrize("role", [AD_ROLE, BOT_ROLE])
async def test_access_loss_during_http_retains_first_attempt_without_replay(
    rig, filtering, role
):
    values = bot_sample(rig) if role == BOT_ROLE else [source(rig, 1)]
    request = request_for(filtering, values, role)

    class LosingAccess(FakeDecider):
        async def generate(self, role, packet):
            # The marker must exist before the provider is called.
            assert all(
                row["pending"] and row["failed"]
                for row in filtering.storage.source_decisions(values[0].key)
            )
            response = await super().generate(role, packet)
            rig.store.groups.observe(KEY, access="lost", reason="authored access loss")
            return response

    engine = LosingAccess()
    engine.group_probability = 0.99
    filtering.app.decider = engine
    assert await run(filtering, request) is None
    marker = filtering.storage.decision(values[0].key, request["fingerprint"])
    assert marker["failed"] and marker["pending"] and marker["probabilities"] == {}
    assert filtering.storage.bot(KEY, "user:7") is None
    rig.store.groups.observe(KEY, access="available", reason="authored access restored")
    epoch = rig.store.groups.intake(KEY).epoch
    with rig.store.transaction() as db:
        for value in values:
            rig.store.groups.record_source(db, value, epoch)
    assert filtering.current(request)
    planned = filtering.plan(KEY, values, DAY)
    assert not any(candidate["role"] == role for candidate in planned)
    assert len(engine.calls) == 1


async def test_request_cap_failure_rolls_back_marker_with_reservation(rig, filtering):
    request = request_for(filtering, [source(rig, 1)], AD_ROLE)

    def cap(db, job):
        raise Refused("Authored request cap reached")

    with pytest.raises(Refused, match="request cap"):
        await run(filtering, request, group_accounting=cap)
    assert not rig.store.rows("SELECT 1 FROM reservations")
    assert not rig.store.rows("SELECT 1 FROM group_filter_decisions")
    assert not filtering.app.decider.calls


def test_frozen_bot_membership_never_expands_after_native_metadata_stage(
    rig, filtering
):
    values = bot_sample(rig)
    frozen = {
        "user:7": [message.key for message in filtering.latest_ten(KEY, "user:7")]
    }
    assert any(
        r["role"] == BOT_ROLE
        for r in filtering.plan(KEY, values, DAY, bot_samples=frozen)
    )
    assert not any(
        r["role"] == BOT_ROLE for r in filtering.plan(KEY, values, DAY, bot_samples={})
    )
    source(rig, 11, sender_is_bot=True)
    assert not any(
        r["role"] == BOT_ROLE
        for r in filtering.plan(KEY, values, DAY, bot_samples=frozen)
    )
    assert frozen["user:7"] == [f"{KEY}/{i}" for i in range(10, 0, -1)]


def test_direct_reply_precedes_twenty_recent_context_sources(rig, filtering):
    parent = source(rig, 1, text="Пожалуйста, порекомендуйте ремонтника и укажите цену")
    for identity in range(2, 26):
        source(rig, identity, text="Короткая отдельная реплика")
    target = source(
        rig, 50, reply_to=1, text="По вашему запросу: вот мастер и его цена"
    )
    request = request_for(filtering, [target], AD_ROLE)
    context = json.loads(request["packet"]["state"])["untrusted_sources"][
        "supporting_context"
    ]
    assert parent.message_id in [m["id"] for m in context]
    assert len(context) <= 21
    assert parent.key in request["evidence"]


def test_distinct_direct_parents_split_ad_packets_without_dropping_request(
    rig, filtering
):
    source(rig, 1, text="Порекомендуйте столяра")
    source(rig, 2, text="Порекомендуйте переводчика")
    first = source(rig, 100, reply_to=1, text="Вот контакт столяра по вашему запросу")
    second = source(
        rig, 101, reply_to=2, text="Вот контакт переводчика по вашему запросу"
    )
    requests = filtering.plan(KEY, [first, second], DAY)
    assert [r["packet"]["target_ids"] for r in requests] == [[100], [101]]
    assert f"{KEY}/1" in requests[0]["evidence"]
    assert f"{KEY}/2" in requests[1]["evidence"]


def test_unavailable_direct_reply_holds_classification_and_keeps_source_eligible(
    rig, filtering
):
    message = source(
        rig, 1, reply_to=999, text="По вашему запросу: цена и контакт продавца"
    )
    assert filtering.plan(KEY, [message], DAY) == []
    assert filtering.annotations(message) == dict(
        ad=False, ignored_bot=False, allowed_bot=False
    )


def test_hide_bots_hides_every_verified_bot_without_a_judgment(rig, filtering):
    rig.registry.set_hide_bots(True)
    useful = source(rig, 1, sender_is_bot=True, text="The authored bridge is closed.")
    human = source(rig, 2, author_peer="user:8", sender_is_bot=False)
    unknown = source(rig, 3, author_peer="user:9")
    assert filtering.annotations(useful) == dict(
        ad=False, ignored_bot=True, allowed_bot=False
    )
    assert not filtering.annotations(human)["ignored_bot"]
    # A name is no proof: unverified authors stay.
    assert not filtering.annotations(unknown)["ignored_bot"]
    requests = filtering.plan(KEY, [useful, human, unknown], DAY)
    assert all(request["role"] == AD_ROLE for request in requests)
    assert useful.key not in {key for r in requests for key in r["target_keys"]}
    assert not rig.store.rows("SELECT 1 FROM group_bot_decisions")


def test_hide_bots_plans_no_ten_message_judgment(rig, filtering):
    values = bot_sample(rig)
    assert any(r["role"] == BOT_ROLE for r in filtering.plan(KEY, values, DAY))
    rig.registry.set_hide_bots(True)
    assert not filtering.plan(KEY, values, DAY)


def test_owner_can_exempt_a_bot_that_was_never_judged(rig, filtering):
    rig.registry.set_hide_bots(True)
    notice = source(rig, 1, sender_is_bot=True, text="The authored bridge is closed.")
    person = source(rig, 2, author_peer="user:8", sender_is_bot=False)
    with pytest.raises(Refused, match="not a verified bot"):
        filtering.restore_bot(KEY, "user:8")
    assert filtering.restore_bot(KEY, "user:7")
    assert filtering.annotations(notice) == dict(
        ad=False, ignored_bot=False, allowed_bot=True
    )
    assert not filtering.restore_bot(KEY, "user:7")
    assert not filtering.annotations(person)["allowed_bot"]
    # The exemption is one bot in one group; it survives the preference.
    rig.registry.set_hide_bots(False)
    assert filtering.annotations(notice)["allowed_bot"]


async def test_one_pass_checks_each_decision_and_each_original_once(
    rig, filtering, monkeypatch
):
    values = [source(rig, identity, author_peer="user:8") for identity in range(1, 13)]
    request = request_for(filtering, values, AD_ROLE)
    await run(filtering, request)
    storage = filtering.storage
    checks, reads = [], []
    whole, one = storage._evidence_current, storage._evidence_source

    def counted(request, *args):
        checks.append(request["fingerprint"])
        return whole(request, *args)

    def read(db, source, admission, memo):
        if ("source", source, admission) not in memo:
            reads.append(source)
        return one(db, source, admission, memo)

    monkeypatch.setattr(storage, "_evidence_current", counted)
    monkeypatch.setattr(storage, "_evidence_source", read)
    # Twelve originals share one decision and its packet.
    assert not filtering.plan(KEY, values, DAY)
    assert len(checks) == 1 and len(reads) == len(set(reads)) == 12
    checks.clear()
    reads.clear()
    assert len(filtering.annotate(values)) == 12
    assert len(checks) == 1 and len(reads) == 12
