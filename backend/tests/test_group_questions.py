"""ADR 59's Ask this group with authored engines, offline only."""

import json

import httpx
import pytest
from enloq.application.group_questions import GroupQuestions
from enloq.domain.citation_check import ANSWER_CHECK_ROLE
from enloq.domain.group_questions import (
    EMBED_ROLE,
    QUESTION_ROLE,
    RERANK_ROLE,
    AnswerError,
    fuse,
    match_expression,
    question_text,
    validate_answer,
)
from enloq.domain.models import ProcessingPaused, Refused
from enloq.inference import retrieval
from enloq.inference.base import EngineResponse, InvalidOutput, ProviderRefused

from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_group_briefing_check import settings_at
from backend.tests.test_group_pipeline_reuse import KEY, create, source


def test_keyword_expression_fusion_and_question_bounds():
    assert match_expression("Who has the shed key? Ок, ключ у кого") == (
        '"who" OR "has" OR "the" OR "shed" OR "key" OR "ключ" OR "кого"'
    )
    assert match_expression("a, b?") is None
    assert fuse([3, 1, 2], [1, 4]) == [1, 3, 4, 2]
    assert question_text("  When   is it?  ") == "When is it?"
    for bad in ("", " ", "x" * 501, "bad\x00"):
        with pytest.raises(Refused):
            question_text(bad)


def test_an_answer_cites_only_retrieved_messages():
    answer = {
        "outcome": "answer",
        "statements": [{"text": "On Sunday.", "citations": [3]}],
    }
    assert validate_answer(answer, [3, 5]).statements[0].citations == [3]
    for broken, code in (
        (
            {**answer, "statements": [{"text": "x", "citations": [4]}]},
            "answer_citations",
        ),
        ({"outcome": "answer", "statements": []}, "answer_schema"),
        ({"outcome": "not_found", "statements": answer["statements"]}, "answer_schema"),
    ):
        with pytest.raises(AnswerError) as failure:
            validate_answer(broken, [3, 5])
        assert failure.value.code == code


def test_vectors_and_scores_are_validated():
    texts = {"texts": ["a", "b"]}
    rows = {
        "data": [{"index": 1, "embedding": [0, 2]}, {"index": 0, "embedding": [3, 4]}]
    }
    assert retrieval.validate(EMBED_ROLE, EngineResponse(rows), texts) == [
        [0.6, 0.8],
        [0.0, 1.0],
    ]
    for broken in (
        {"data": [{"index": 0, "embedding": [1.0]}]},
        {"data": [{"index": 0, "embedding": [1.0]}, {"index": 1, "embedding": [1, 2]}]},
        {"data": [{"index": 0, "embedding": [0]}, {"index": 1, "embedding": [1]}]},
    ):
        with pytest.raises(InvalidOutput):
            retrieval.validate(EMBED_ROLE, EngineResponse(broken), texts)
    documents = {"documents": ["a", "b"]}
    scores = {
        "results": [
            {"index": 1, "relevance_score": 0.9},
            {"index": 0, "relevance_score": 0.1},
        ]
    }
    assert retrieval.validate(RERANK_ROLE, EngineResponse(scores), documents) == [
        0.1,
        0.9,
    ]
    with pytest.raises(InvalidOutput):
        retrieval.validate(
            RERANK_ROLE,
            EngineResponse({"results": [{"index": 0, "relevance_score": 1}]}),
            documents,
        )


async def test_the_pass_through_pins_one_zero_retention_provider(tmp_path):
    seen, status = [], [200]

    async def handler(request):
        seen.append(json.loads(request.content))
        assert (
            str(request.url)
            == ENDPOINT.removesuffix("/v1") + "/openrouter/api/v1/embeddings"
        )
        if status[0] != 200:
            return httpx.Response(status[0])
        return httpx.Response(
            200,
            json={
                "data": [{"index": 0, "embedding": [0.1, 0.2]}],
                "provider": "Nebius",
                "usage": {"prompt_tokens": 3, "cost": 0.00000003},
            },
        )

    engine = retrieval.EmbeddingEngine(
        settings_at(tmp_path),
        retrieval.EMBEDDING,
        user="u" * 32,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    response = await engine.generate(EMBED_ROLE, {"texts": ["Authored."]})
    assert response.actual_nanos == 30
    assert seen[0]["provider"] == {
        "only": ["nebius"],
        "allow_fallbacks": False,
        "zdr": True,
    }
    assert (
        seen[0]["dimensions"] == 1024 and seen[0]["model"] == "qwen/qwen3-embedding-8b"
    )
    status[0] = 403
    with pytest.raises(ProviderRefused):
        await engine.generate(EMBED_ROLE, {"texts": ["Authored."]})
    with pytest.raises(Refused):
        await engine.generate(EMBED_ROLE, {"texts": []})
    await engine.client.aclose()


@pytest.fixture
async def rig(tmp_path):
    value = create(tmp_path)
    value.store = value.app.store
    try:
        yield value
    finally:
        await value.manager.close()


def roles(rig):
    return [role for role, _ in rig.app.engine.calls] + [
        role for role, _ in rig.app.decider.calls
    ]


async def test_an_answer_keeps_supported_citations_and_names_the_range(rig):
    source(rig, 1, text="The shed key is with Eli now.")
    source(rig, 2, text="Watering moved from Saturday to Sunday morning.")
    source(rig, 3, text="Lovely weather today!")
    rig.app.engine.answer = {
        "outcome": "answer",
        "statements": [{"text": "Watering is on Sunday morning.", "citations": [2, 3]}],
    }
    rig.app.decider.citation_support = {3: 0.1}
    result = await rig.app.questions.ask(KEY, "When is the watering now?")
    assert result["status"] == "answer"
    assert result["searched"] == {"start": "2026-10-11", "end": "2026-10-11"}
    [statement] = result["statements"]
    assert statement["citations"] == [2] and statement["unsupported"] == [3]
    assert statement["supported"] is True
    written = next(p for role, p in rig.app.engine.calls if role == QUESTION_ROLE)
    assert {m["id"] for m in written["messages"]} == {1, 2, 3}
    assert "peer" not in json.dumps(written["messages"])
    assert [r for r in roles(rig) if r == ANSWER_CHECK_ROLE] == [ANSWER_CHECK_ROLE]
    # Nothing about the question or answer is stored (Q9).
    assert not rig.store.rows("SELECT 1 FROM messages WHERE body LIKE '%watering now%'")


async def test_vectors_are_reused_and_dropped_with_an_edit(rig):
    source(rig, 1, text="The shed key is with Eli now.")
    source(rig, 2, text="Watering moved to Sunday.")
    embedder = rig.app.questions.embedder
    await rig.app.questions.ask(KEY, "Who has the key?")
    assert [len(p["texts"]) for _, p in embedder.calls] == [2, 1]
    await rig.app.questions.ask(KEY, "Who has the key?")
    assert [len(p["texts"]) for _, p in embedder.calls] == [2, 1, 1]
    source(rig, 2, revision=2, text="Watering moved to Monday.")
    await rig.app.questions.ask(KEY, "When is watering?")
    assert [len(p["texts"]) for _, p in embedder.calls][-2:] == [1, 1]


async def test_ignored_bots_and_ads_stay_out_and_nothing_found_calls_no_model(rig):
    source(rig, 1, author_peer="user:9", sender_is_bot=True, text="Buy seeds today!")
    result = await rig.app.questions.ask(KEY, "Where can I buy seeds?")
    assert result == {
        "status": "not_found",
        "searched": {"start": "2026-10-11", "end": "2026-10-11"},
        "statements": [],
    }
    assert not roles(rig) and not rig.app.questions.embedder.calls


async def test_a_check_that_cannot_run_shows_no_answer(rig):
    source(rig, 1, text="The shed key is with Eli now.")

    async def refuse(role, packet):
        raise ProviderRefused("Model provider HTTP 429")

    rig.app.decider.generate = refuse
    result = await rig.app.questions.ask(KEY, "Who has the key?")
    assert result["status"] == "unchecked" and result["statements"] == []


async def test_a_not_found_answer_and_group_ai_off(rig):
    source(rig, 1, text="The shed key is with Eli now.")
    rig.app.engine.answer = {"outcome": "not_found", "statements": []}
    result = await rig.app.questions.ask(KEY, "What did the plumber charge?")
    assert result["status"] == "not_found"
    assert ANSWER_CHECK_ROLE not in roles(rig)
    rig.app.set_group_processing(KEY, False)
    with pytest.raises(ProcessingPaused):
        await rig.app.questions.ask(KEY, "Who has the key?")


async def test_one_question_at_a_time_per_account(rig):
    source(rig, 1, text="The shed key is with Eli now.")
    questions = rig.app.questions
    assert isinstance(questions, GroupQuestions)
    async with questions._busy:
        with pytest.raises(Refused):
            await questions.ask(KEY, "Who has the key?")
