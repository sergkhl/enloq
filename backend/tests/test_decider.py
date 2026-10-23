"""ADR 30 D1: the exact Decider / Perplexity route, offline with mocked transport."""

import json
import math

import httpx
import pytest
from enloq.application.accounts import AccountManager
from enloq.config import Settings, private_write
from enloq.domain.emotion import CODES
from enloq.domain.models import Refused, Unavailable
from enloq.inference import decider
from enloq.inference.base import (
    EngineResponse,
    InvalidOutput,
    ProviderRefused,
)
from enloq.inference.decider import (
    LISTING,
    LOGICAL_MODEL,
    MODEL,
    PROVIDER,
    DeciderEngine,
    route_rates,
)
from enloq.inference.fake import choice
from enloq.inference.guidance import EMOTION_CRITERIA, snapshot
from enloq.inference.openrouter import LOGICAL_MODEL as MIMO
from enloq.inference.openrouter import PROVIDER as MIMO_PROVIDER
from enloq.inference.work import ROUTES, validate_output
from enloq.storage.accounts import AccountRegistry

from backend.tests.proxy_helpers import DECISIONS, ENDPOINT

USER = "authored-provider-pseudonym"


def route(**changes):
    return {
        "name": f"Perplexity | {MODEL}-20261016",
        "tag": "perplexity",
        "model_id": MODEL,
        "status": 0,
        "supported_parameters": [],
        "pricing": {"prompt": "0.00000002", "completion": "0"},
        "context_length": 262144,
        **changes,
    }


def listing(*endpoints, model=MODEL):
    async def handler(request):
        # OpenRouter's public listing: no proxy key goes to it.
        assert str(request.url) == f"{LISTING}/models/{MODEL}/endpoints"
        assert "authorization" not in request.headers
        return httpx.Response(
            200, json={"data": {"id": model, "endpoints": list(endpoints)}}
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def settings(tmp_path, key="sk-authored-placeholder"):
    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", key)
    return Settings(
        mode="personal",
        data=tmp_path,
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )


def engine(tmp_path, handler, **kwargs):
    return DeciderEngine(
        settings(tmp_path),
        user=USER,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        **kwargs,
    )


def answers(code="worried", probability=0.8, **extra):
    return {"emotion": choice(code, probability), **extra}


def incoming_answers(code="worried", probability=0.8, useful=0.7):
    """An incoming request's two answers (ADR 49 Q2)."""
    return answers(
        code, probability, suggestions_useful={"type": "noul", "noul": useful}
    )


def body(content=None, *, model=MODEL, provider="Perplexity", usage=None):
    return {
        "id": "gen-dec-authored",
        "model": model,
        "provider": provider,
        "answers": answers() if content is None else content,
        "usage": usage or {"input_tokens": 800, "output_tokens": 2, "cost": 1.6e-05},
    }


async def test_route_accepts_only_the_single_perplexity_endpoint():
    other = route(tag="deepinfra", name="DeepInfra")
    async with listing(other, route()) as client:
        rates = await route_rates(client)
    assert rates["model"] == LOGICAL_MODEL and rates["provider"] == PROVIDER
    assert rates["endpoint"] == "perplexity"
    assert rates["checkpoint"] == f"{MODEL}-20261016"


@pytest.mark.parametrize(
    "endpoints,model",
    [
        ([route(tag="deepinfra")], MODEL),
        ([route(status=-5)], MODEL),
        ([route(), route(tag="perplexity/fp8")], MODEL),
        ([route(tag="perplexityfast")], MODEL),
        ([route(model_id="perplexity/pplx-decider-v1.1-27b")], MODEL),
        ([route()], "perplexity/pplx-decider-v1.1-27b"),
    ],
    ids=["other", "not-ok", "ambiguous", "lookalike", "earlier-model", "listing-id"],
)
async def test_missing_ambiguous_or_other_checkpoint_is_a_no_work_refusal(
    endpoints, model
):
    async with listing(*endpoints, model=model) as client:
        with pytest.raises(ProviderRefused, match="Decider / Perplexity"):
            await route_rates(client)


@pytest.mark.parametrize(
    "pricing",
    [
        {"prompt": "0.00000003", "completion": "0"},
        {"prompt": "0.00000002", "completion": "0.000001"},
    ],
)
async def test_rates_above_the_adopted_rates_send_no_work(pricing):
    async with listing(route(pricing=pricing)) as client:
        with pytest.raises(ProviderRefused, match="adopted rates"):
            await route_rates(client)


def test_construction_keeps_the_endpoint_key_and_pseudonym_binding(tmp_path):
    with pytest.raises(Refused, match="Offline"):
        DeciderEngine(Settings(), user=USER)
    with pytest.raises(Refused, match="pseudonym"):
        DeciderEngine(settings(tmp_path), user="short")
    with pytest.raises(Refused, match="not the LiteLLM proxy"):
        DeciderEngine(settings(tmp_path, "authored-non-proxy-key"), user=USER)
    # An OpenRouter key is not a proxy caller key.
    with pytest.raises(Refused, match="not the LiteLLM proxy"):
        DeciderEngine(settings(tmp_path, "sk-or-authored-placeholder"), user=USER)


async def test_one_exact_decision_request_and_its_receipt(tmp_path):
    requests = []

    async def handler(request):
        if request.method == "GET":
            assert str(request.url) == f"{LISTING}/models/{MODEL}/endpoints"
            assert "authorization" not in request.headers
            return httpx.Response(
                200, json={"data": {"id": MODEL, "endpoints": [route()]}}
            )
        requests.append(request)
        return httpx.Response(
            200,
            json=body(
                usage={
                    "input_tokens": 800,
                    "output_tokens": 2,
                    "cost": 1.6e-05,
                    "cost_details": {"upstream_inference_cost": 2e-05},
                }
            ),
        )

    live = engine(tmp_path, handler)
    await live.prepare({})
    state = {"conversation": [{"id": 1, "from": "recipient", "text": "Authored"}]}
    guidance = snapshot({"emotion": "Owner-edited emotion guidance."}, 3)
    response = await live.generate(
        "emotion_classification", {"state": state, "guidance": guidance}
    )
    [request] = requests
    # The proxy's pass-through forwards the body unchanged to OpenRouter.
    assert str(request.url) == DECISIONS
    assert request.headers["authorization"] == "Bearer sk-authored-placeholder"
    assert request.headers["x-litellm-enable-message-redaction"] == "true"
    payload = json.loads(request.content)
    assert set(payload) == {"model", "provider", "user", "state", "questions"}
    assert payload["model"] == MODEL and payload["user"] == USER
    assert payload["provider"] == {"only": ["perplexity"], "allow_fallbacks": False}
    assert payload["state"] == state
    # ADR 49 Q2: the emotion choice and the usefulness yes/no, one request;
    # ADR 53: whether the block is sarcastic, a fixed question.
    assert set(payload["questions"]) == {"emotion", "suggestions_useful", "sarcasm"}
    question = payload["questions"]["emotion"]
    assert question["type"] == "choice"
    assert list(question["criteria"]) == list(CODES) == list(EMOTION_CRITERIA)
    assert "[Owner guidance: emotion extraction]" in question["instructions"]
    assert "Owner-edited emotion guidance." in question["instructions"]
    useful = payload["questions"]["suggestions_useful"]
    assert useful["type"] == "noul" and "criteria" not in useful
    assert "[Owner guidance: when to suggest]" in useful["instructions"]
    sarcasm = payload["questions"]["sarcasm"]
    assert (
        sarcasm["type"] == "noul" and "[Owner guidance" not in sarcasm["instructions"]
    )
    # The larger of the charged and the upstream cost counts.
    assert response.actual_nanos == 20_000
    assert response.usage["receipt_id"] == "gen-dec-authored"
    assert response.usage["input_tokens"] == 800
    assert response.content == answers()
    await live.client.aclose()


@pytest.mark.parametrize(
    "model,provider",
    [("perplexity/pplx-decider-v1.1-27b", "Perplexity"), (MODEL, "DeepInfra")],
)
async def test_an_unqualified_echo_fails_but_keeps_its_receipt(
    tmp_path, model, provider
):
    async def handler(request):
        return httpx.Response(200, json=body(model=model, provider=provider))

    live = engine(tmp_path, handler)
    with pytest.raises(Unavailable, match="unqualified") as failure:
        await live.generate("emotion_classification", {"state": {}})
    assert failure.value.receipt.actual_nanos == 16_000
    await live.client.aclose()


async def test_only_the_adopted_dated_checkpoint_echo_is_exact(tmp_path):
    async def handler(request):
        return httpx.Response(200, json=body(model=f"{MODEL}-20261016"))

    live = engine(tmp_path, handler)
    response = await live.generate("emotion_classification", {"state": {}})
    assert response.usage["checkpoint"] == f"{MODEL}-20261016"
    await live.client.aclose()

    async def later(request):
        return httpx.Response(200, json=body(model=f"{MODEL}-20261201"))

    live = engine(tmp_path, later)
    with pytest.raises(Unavailable, match="unqualified"):
        await live.generate("emotion_classification", {"state": {}})
    await live.client.aclose()


async def test_a_listing_that_serves_another_checkpoint_sends_no_work():
    later = route(name=f"Perplexity | {MODEL}-20261201")
    async with listing(later) as client:
        with pytest.raises(ProviderRefused, match="another checkpoint"):
            await route_rates(client)
    # A name without a checkpoint does not decide; the echo still must.
    async with listing(route(name="Perplexity")) as client:
        assert (await route_rates(client))["checkpoint"] is None


@pytest.mark.parametrize(
    "status,kind", [(429, ProviderRefused), (402, ProviderRefused), (500, Unavailable)]
)
async def test_http_refusals_do_no_work_and_failures_are_unavailable(
    tmp_path, status, kind
):
    async def handler(request):
        return httpx.Response(status, headers={"retry-after": "12"})

    live = engine(tmp_path, handler)
    with pytest.raises(kind) as failure:
        await live.generate("emotion_classification", {"state": {}})
    if kind is ProviderRefused:
        assert failure.value.retry_after == 12
    await live.client.aclose()


@pytest.mark.parametrize(
    "status,kind,reported",
    [
        # The proxy refusing a model its key lacks does no work (ADR 49 Q14).
        (401, ProviderRefused, (False, "refused")),
        (403, ProviderRefused, (False, "refused")),
        (402, ProviderRefused, (False, "refused")),
        (429, ProviderRefused, None),  # Throttling is not an outage.
        (404, Unavailable, (False, "http")),
        (500, Unavailable, (False, "http")),
        (200, None, (True, None)),
    ],
)
async def test_each_answer_reports_the_routes_state(tmp_path, status, kind, reported):
    async def handler(request):
        if status == 200:
            return httpx.Response(200, json=body())
        return httpx.Response(status)

    live = engine(tmp_path, handler)
    reports = []
    live.on_route = lambda available, reason=None: reports.append((available, reason))
    if kind is None:
        await live.generate("emotion_classification", {"state": {}})
    else:
        with pytest.raises(kind):
            await live.generate("emotion_classification", {"state": {}})
    assert reports == ([reported] if reported else [])
    await live.client.aclose()


async def test_listing_connection_and_echo_failures_report_an_outage(tmp_path):
    async def unlisted(request):
        return httpx.Response(200, json={"data": {"id": MODEL, "endpoints": []}})

    live = engine(tmp_path, unlisted)
    reports = []
    live.on_route = lambda available, reason=None: reports.append((available, reason))
    with pytest.raises(ProviderRefused):
        await live.prepare({})
    await live.client.aclose()

    async def unreachable(request):
        raise httpx.ConnectError("Authored refusal")

    live = engine(tmp_path, unreachable)
    live.on_route = lambda available, reason=None: reports.append((available, reason))
    with pytest.raises(ProviderRefused, match="connection"):
        await live.generate("emotion_classification", {"state": {}})
    await live.client.aclose()

    async def echo(request):
        return httpx.Response(200, json=body(model="perplexity/pplx-decider-v1.1-27b"))

    live = engine(tmp_path, echo)
    live.on_route = lambda available, reason=None: reports.append((available, reason))
    with pytest.raises(Unavailable):
        await live.generate("emotion_classification", {"state": {}})
    await live.client.aclose()
    assert reports == [(False, "listing"), (False, "connection"), (False, "route")]

    # A failing state store never changes the call's own outcome.
    def broken(available, reason=None):
        raise RuntimeError("Authored registry failure")

    live = engine(tmp_path, lambda request: httpx.Response(200, json=body()))
    live.on_route = broken
    assert (await live.generate("emotion_classification", {"state": {}})).content
    await live.client.aclose()


async def test_generation_roles_are_not_decisions(tmp_path):
    live = engine(tmp_path, lambda request: None)
    with pytest.raises(Refused, match="decision role"):
        await live.generate("incoming_rewrite", {"state": {}})
    await live.client.aclose()


def classify(content):
    return validate_output(
        "emotion_classification", EngineResponse(content), {"state": {}}
    )


@pytest.mark.parametrize("code", CODES)
def test_valid_answers_keep_probabilities_for_diagnosis(code):
    result = classify(incoming_answers(code, 0.8))
    decision = result.decision
    assert (result.code, decision.choice, decision.probability) == (code, code, 0.8)
    assert decision.confidence == 0.8 and set(decision.probabilities) == set(CODES)
    assert result.suggestions_useful and result.usefulness == 0.7


def test_a_selected_probability_below_the_floor_is_unknown():
    assert classify(incoming_answers("sad", 0.3499)).code == "unknown"
    low = classify(incoming_answers("sad", 0.3499)).decision
    assert low.choice == "sad" and low.probability == 0.3499
    assert classify(incoming_answers("sad", 0.35)).code == "sad"


def test_incoming_usefulness_is_the_same_half_threshold_and_required():
    # ADR 49 Q2: the answer only gates suggestions, at the reactions' cutoff.
    assert classify(incoming_answers(useful=0.5)).suggestions_useful
    low = classify(incoming_answers(useful=0.4999))
    assert not low.suggestions_useful and low.usefulness == 0.4999
    with pytest.raises(InvalidOutput, match="missing_answer"):
        classify(answers())
    with pytest.raises(InvalidOutput, match="invalid_answer"):
        classify(answers(suggestions_useful={"type": "noul", "noul": 1.5}))


def nan_answer():
    value = choice("sad", 0.8)
    value["probabilities"]["angry"] = math.nan
    return {"emotion": value}


@pytest.mark.parametrize(
    "content,diagnostic",
    [
        ({}, "missing_answer"),
        (None, "missing_answer"),
        ([], "missing_answer"),
        ({"emotion": "sad"}, "missing_answer"),
        ({"emotion": {**choice("sad", 0.8), "type": "noul"}}, "invalid_answer"),
        ({"emotion": {**choice("sad", 0.8), "choice": "elated"}}, "unknown_option"),
        (
            {"emotion": {**choice("sad", 0.8), "probabilities": {}}},
            "invalid_probabilities",
        ),
        (
            {"emotion": {**choice("sad", 0.8), "probabilities": {"sad": 1.2}}},
            "invalid_probabilities",
        ),
        (
            {"emotion": {**choice("sad", 0.8), "probabilities": {"sad": True}}},
            "invalid_probabilities",
        ),
        (
            {
                "emotion": {
                    **choice("sad", 0.8),
                    "probabilities": {"sad": 0.8, "elated": 0.2},
                }
            },
            "invalid_probabilities",
        ),
        (nan_answer(), "invalid_probabilities"),
        ({"emotion": {**choice("sad", 0.8), "confidence": 2}}, "invalid_answer"),
        ({**answers(), "dominant": choice("sad", 0.8)}, "invalid_answer"),
    ],
)
def test_malformed_answers_are_fixed_content_free_format_failures(content, diagnostic):
    with pytest.raises(InvalidOutput) as failure:
        classify(content)
    assert str(failure.value) == diagnostic


def test_reaction_usefulness_is_a_fixed_half_threshold():
    def assess(useful):
        return decider.validate(
            "reaction_assessment",
            answers(
                "positive",
                0.85,
                suggestions_useful={"type": "noul", "noul": useful},
            ),
        )

    assert assess(0.5).suggestions_useful and not assess(0.4999).suggestions_useful
    result = assess(0.7)
    assert (result.emotion, result.usefulness) == ("positive", 0.7)
    assert result.decision.probability == 0.85
    for bad in (
        {"type": "noul"},
        {"type": "choice", "noul": 0.5},
        {"type": "noul", "noul": -1},
    ):
        with pytest.raises(InvalidOutput, match="invalid_answer"):
            decider.validate("reaction_assessment", answers(suggestions_useful=bad))
    with pytest.raises(InvalidOutput, match="missing_answer"):
        decider.validate("reaction_assessment", answers())


def test_format_counts_restart_per_role_when_only_that_route_changes(tmp_path):
    registry = AccountRegistry(tmp_path)
    # Before ADR 30 one route string served every role.
    with registry.transaction() as db:
        db.execute(
            "INSERT INTO metadata VALUES ('format_route',?)",
            (f"{MIMO}@{MIMO_PROVIDER}",),
        )
    for _ in range(3):
        registry.record_format("chat_memory", False)
    registry.record_format("emotion_classification", False)
    AccountManager(Settings(mode="personal", data=tmp_path))
    # MiMo roles keep their counts; only the decided role restarts.
    assert registry.format_failures() == {"chat_memory": 3}
    assert registry.paused_roles() == {"chat_memory"}
    assert ROUTES["emotion_classification"] == f"{LOGICAL_MODEL}@{PROVIDER}"
    assert ROUTES["chat_memory"] == "xiaomi_mimo/mimo-v2.6-flash@xiaomi"
    assert not registry.bind_routes(ROUTES)
    assert registry.bind_routes(
        {**ROUTES, "chat_memory": "another/model@provider"}
    ) == {"chat_memory"}
    assert not registry.paused_roles()


def test_each_question_carries_only_its_own_owner_guidance():
    from enloq.inference.guidance import decision_questions

    guidance = snapshot(
        {
            "emotion": "Owner-edited emotion guidance.",
            "usefulness": "Owner-edited usefulness guidance.",
        },
        2,
    )
    incoming = decision_questions("emotion_classification", guidance)
    reaction = decision_questions("reaction_assessment", guidance)
    # ADR 49: both requests ask both questions; ADR 53: only the incoming one
    # asks whether the block is sarcastic.
    assert set(reaction) == {"emotion", "suggestions_useful"}
    assert set(incoming) == {"emotion", "suggestions_useful", "sarcasm"}
    for question in (incoming["emotion"], reaction["emotion"]):
        assert "Owner-edited emotion guidance." in question["instructions"]
        assert "usefulness guidance" not in question["instructions"]
    for question in (incoming["suggestions_useful"], reaction["suggestions_useful"]):
        assert question["type"] == "noul" and "criteria" not in question
        assert "[Owner guidance: when to suggest]" in question["instructions"]
        assert "Owner-edited usefulness guidance." in question["instructions"]
        assert "emotion guidance" not in question["instructions"]
    assert incoming["emotion"]["instructions"] != reaction["emotion"]["instructions"]
    assert (
        incoming["suggestions_useful"]["instructions"]
        != reaction["suggestions_useful"]["instructions"]
    )
    # An emptied field leaves only the fixed core.
    emptied = decision_questions("emotion_classification", snapshot({"usefulness": ""}))
    assert "[Owner guidance" not in emptied["suggestions_useful"]["instructions"]
    with pytest.raises(ValueError):
        decision_questions("incoming_rewrite", guidance)


def test_the_sarcasm_answer_is_read_when_given_and_checked_when_malformed():
    given = incoming_answers()
    given["sarcasm"] = {"type": "noul", "noul": 0.8}
    assert classify(given).sarcasm == 0.8 and classify(given).sarcastic
    given["sarcasm"] = {"type": "noul", "noul": 0.4999}
    assert not classify(given).sarcastic
    # Missing, it is unknown and no hint follows.
    missing = classify(incoming_answers())
    assert missing.sarcasm is None and not missing.sarcastic
    given["sarcasm"] = {"type": "noul", "noul": 1.5}
    with pytest.raises(InvalidOutput, match="invalid_answer"):
        classify(given)
