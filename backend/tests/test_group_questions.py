"""ADR 59's Ask this group with authored engines, offline only."""

import json

import httpx
import pytest
from enloq.application.group_questions import GroupQuestions, Reading
from enloq.domain.citation_check import ANSWER_CHECK_ROLE
from enloq.domain.group_questions import (
    AROUND,
    EMBED_ROLE,
    LINKED,
    MAX_CALLS,
    MAX_SEARCHES,
    MAX_TURNS,
    QUESTION_ROLE,
    RERANK_ROLE,
    SEARCH,
    SEARCHED,
    AnswerError,
    ToolCall,
    fuse,
    match_expression,
    question_text,
    validate_answer,
    validate_turn,
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
    assert match_expression("Где квартиру снимали?") == '"где" OR "кварти" OR "снима"'
    assert match_expression("a, b?") is None
    assert fuse([3, 1, 2], [1, 4]) == [1, 3, 4, 2]
    assert question_text("  When   is it?  ") == "When is it?"
    for bad in ("", " ", "x" * 501, "bad\x00"):
        with pytest.raises(Refused):
            question_text(bad)


def test_an_answer_cites_only_retrieved_messages():
    answer = {"statements": [{"text": "On Sunday.", "citations": [3]}]}
    assert validate_answer(answer, [3, 5]).statements[0].citations == [3]
    for broken, code in (
        (
            {**answer, "statements": [{"text": "x", "citations": [4]}]},
            "answer_citations",
        ),
        ({"statements": [{"text": "x", "citations": [3]}] * 5}, "answer_schema"),
        ({"outcome": "not_found", "statements": []}, "answer_schema"),
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
        retrieval.Route(
            "qwen/qwen3-embedding-8b", "nebius", "nebius", "0.00000001", dimensions=1024
        ),
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
    rig.app.engine.answer = {"statements": []}
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


async def test_a_quota_refusal_of_an_embedding_is_tried_again(rig, monkeypatch):
    source(rig, 1, text="The shed key is with Eli now.")
    embedder = rig.app.questions.embedder
    answer = embedder.generate
    refusals = [ProviderRefused("Model provider HTTP 429")] * 2

    async def quota(role, packet):
        if refusals:
            raise refusals.pop()
        return await answer(role, packet)

    waits = []

    async def wait(seconds):
        waits.append(seconds)

    embedder.generate = quota
    monkeypatch.setattr("enloq.application.group_questions.asyncio.sleep", wait)
    result = await rig.app.questions.ask(KEY, "Who has the key?")
    # The authored engines sleep briefly too; the quota waits are whole seconds.
    assert result["status"] == "answer" and [w for w in waits if w >= 1] == [1, 1]
    refusals.extend([ProviderRefused("Model provider HTTP 429")] * 3)
    with pytest.raises(ProviderRefused):
        await rig.app.questions.ask(KEY, "Who has the key now?")


async def test_the_subscription_writer_asks_for_json_and_spends_nothing(tmp_path):
    from enloq.inference.subscription import MODEL, SubscriptionEngine

    seen, status = [], [200]

    async def handler(request):
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"model_name": MODEL}]})
        seen.append(json.loads(request.content))
        if status[0] != 200:
            return httpx.Response(status[0])
        reply = 'Here: {"statements": [{"text": "On Sunday.", "citations": [3]}]}'
        return httpx.Response(
            200,
            json={
                "id": "authored",
                "choices": [{"message": {"content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 9, "completion_tokens": 4},
            },
        )

    engine = SubscriptionEngine(
        settings_at(tmp_path),
        user="u" * 32,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await engine.prepare({})
    packet = {"question": "When?", "messages": [], "retrieved_ids": [3]}
    response = await engine.generate(QUESTION_ROLE, packet)
    assert json.loads(response.content)["statements"][0]["citations"] == [3]
    assert response.actual_nanos == 0 and response.usage["cost"] == 0
    assert (
        seen[0]["model"] == MODEL
        and "retrieved_ids" not in seen[0]["messages"][1]["content"]
    )
    status[0] = 429
    with pytest.raises(ProviderRefused):
        await engine.generate(QUESTION_ROLE, packet)
    await engine.client.aclose()


async def test_a_failed_rerank_keeps_the_fused_order(rig):
    source(rig, 1, text="The shed key is with Eli now.")
    source(rig, 2, text="Watering moved to Sunday.")

    class Overloaded:
        name, model, provider = "offline", "authored-rerank", "local"

        async def generate(self, role, packet):
            raise ProviderRefused("Model provider HTTP 503")

    rig.app.questions.reranker = Overloaded()
    result = await rig.app.questions.ask(KEY, "Who has the key?")
    assert result["status"] == "answer"


def call(name, identity="c1", **arguments):
    """One tool call in the chat route's shape."""
    return {
        "id": identity,
        "type": "function",
        "function": {"name": name, "arguments": json.dumps(arguments)},
    }


def test_a_turn_is_tool_calls_only_while_tools_are_offered():
    offered = {"tools": True, "final": False, "retrieved_ids": [3]}
    [search] = validate_turn({"tool_calls": [call(SEARCH, query="key")]}, offered)
    assert (search.name, search.arguments) == (SEARCH, {"query": "key"})
    [around] = validate_turn({"tool_calls": [call(AROUND, message_id=3)]}, offered)
    assert around.arguments == {"message_id": 3}
    for broken, packet in (
        ([call(SEARCH, query="key")], {"retrieved_ids": [3]}),
        ([call(SEARCH, query="key")], {**offered, "final": True}),
        ([call("read_web", query="key")], offered),
        ([call(SEARCH, query=" ")], offered),
        ([call(AROUND, message_id="3")], offered),
        ([call(LINKED, message_id=3, extra=1)], offered),
        ([call(SEARCH, query="a"), call(SEARCH, query="b")], offered),
        ([call(SEARCH, f"c{n}", query="key") for n in range(13)], offered),
        ([{"id": "c1", "function": {"name": SEARCH, "arguments": "{"}}], offered),
        ([], offered),
    ):
        with pytest.raises(AnswerError) as failure:
            validate_turn({"tool_calls": broken}, packet)
        assert failure.value.code == "tool_calls"
    # The answer still cites only what the writer has read.
    answer = {"statements": [{"text": "On Sunday.", "citations": [4]}]}
    with pytest.raises(AnswerError):
        validate_turn(answer, offered)


def tool_call(name, **arguments):
    return ToolCall(id="c1", name=name, arguments=arguments)


async def test_tools_read_only_the_searchable_originals_within_their_bounds(rig):
    for identity in range(1, 15):
        source(rig, identity, text=f"Authored chore note {identity}.")
    source(rig, 15, text="Who has the shed key?")
    source(rig, 16, text="Eli has it.", reply_to=15)
    source(rig, 17, text="Since Monday.", reply_to=15)
    source(rig, 18, author_peer="user:9", sender_is_bot=True, text="Buy keys!")
    questions = rig.app.questions
    entries = questions.pool(KEY, 0)
    assert 18 not in {m.message_id for m, _, _ in entries}
    reading = Reading()

    async def run(name, **arguments):
        return await questions.tool(KEY, entries, reading, tool_call(name, **arguments))

    around = await run(AROUND, message_id=3)
    assert [m["id"] for m in around["messages"]] == list(range(1, 9))
    linked = await run(LINKED, message_id=16)
    assert linked["message"]["id"] == 16 and linked["message"]["reply_to"] == 15
    assert linked["replies_to"]["id"] == 15 and linked["replies"] == []
    replied = await run(LINKED, message_id=15)
    assert replied["replies_to"] is None
    assert [m["id"] for m in replied["replies"]] == [16, 17]
    assert "error" in await run(AROUND, message_id=18)
    found = await run(SEARCH, query="shed key")
    assert 1 <= len(found["messages"]) <= SEARCHED
    assert reading.seen >= {1, 8, 15, 16, 17} and 18 not in reading.seen
    for _ in range(MAX_SEARCHES - 1):
        assert "messages" in await run(SEARCH, query="chore")
    assert "error" in await run(SEARCH, query="chore")
    while reading.calls < MAX_CALLS:
        assert "messages" in await run(AROUND, message_id=1)
    assert "error" in await run(AROUND, message_id=1)


async def test_with_tools_alone_the_writer_searches_then_answers(rig):
    source(rig, 1, text="The shed key is with Eli now.")
    source(rig, 2, text="Watering moved to Sunday.")
    rig.app.questions.retrieval = "tools"
    result = await rig.app.questions.ask(KEY, "Who has the shed key?")
    assert result["status"] == "answer"
    written = [p for role, p in rig.calls if role == QUESTION_ROLE]
    assert len(written) == 2
    first, last = written
    assert first["messages"] == [] and first["tools"] and not first["rounds"]
    [exchange] = last["rounds"]
    [made] = exchange["calls"]
    assert made["name"] == SEARCH
    read = [m["id"] for m in exchange["results"][made["id"]]["messages"]]
    assert sorted(read) == last["retrieved_ids"] == [1, 2]
    assert result["statements"][0]["citations"] == [read[0]]
    # One embedding request for the vectors and one for the search's query.
    assert [len(p["texts"]) for _, p in rig.app.questions.embedder.calls] == [2, 1]


async def test_both_reads_the_fixed_pass_first_and_may_answer_at_once(rig):
    source(rig, 1, text="The shed key is with Eli now.")
    rig.app.questions.retrieval = "both"
    result = await rig.app.questions.ask(KEY, "Who has the shed key?")
    assert result["status"] == "answer"
    [written] = [p for role, p in rig.calls if role == QUESTION_ROLE]
    assert [m["id"] for m in written["messages"]] == [1] and written["tools"]


async def test_the_last_allowed_reply_must_answer(rig):
    source(rig, 1, text="The shed key is with Eli now.")
    questions = rig.app.questions
    questions.retrieval = "both"
    rig.app.engine.answer = [
        {"tool_calls": [call(AROUND, f"c{n}", message_id=1)]} for n in range(MAX_TURNS)
    ]
    with pytest.raises(Refused):
        await questions.ask(KEY, "Who has the shed key?")
    written = [p for role, p in rig.calls if role == QUESTION_ROLE]
    assert len(written) == MAX_TURNS and written[-1]["final"]
    assert not any(p["final"] for p in written[:-1])


async def test_a_writer_that_calls_no_tools_reads_the_fixed_pass(rig):
    source(rig, 1, text="The shed key is with Eli now.")
    questions = rig.app.questions
    questions.retrieval = "tools"
    rig.app.engine.tools = False
    result = await questions.ask(KEY, "Who has the shed key?")
    assert result["status"] == "answer"
    [written] = [p for role, p in rig.calls if role == QUESTION_ROLE]
    assert [m["id"] for m in written["messages"]] == [1] and "tools" not in written


async def test_the_subscription_writer_offers_tools_and_returns_their_calls(tmp_path):
    from enloq.inference.subscription import TOOLS, SubscriptionEngine

    seen = []
    replies = [
        {
            "message": {"content": None, "tool_calls": [call(SEARCH, query="key")]},
            "finish_reason": "tool_calls",
        },
        {
            "message": {"content": '{"statements": []}'},
            "finish_reason": "stop",
        },
    ]

    async def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"id": "authored", "choices": [replies.pop(0)]})

    engine = SubscriptionEngine(
        settings_at(tmp_path),
        user="u" * 32,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    packet = {
        "question": "Who has the key?",
        "today": "2026-10-11",
        "messages": [],
        "retrieved_ids": [],
        "tools": True,
        "rounds": [],
        "final": False,
    }
    response = await engine.generate(QUESTION_ROLE, packet)
    assert response.usage["finish_reason"] == "tool_calls"
    [made] = validate_turn(response.content, packet)
    assert seen[0]["tools"] == TOOLS and seen[0]["tool_choice"] == "auto"
    result = {"messages": [{"id": 3, "speaker": "Eli", "text": "Eli has it."}]}
    packet.update(
        rounds=[{"calls": [made.model_dump()], "results": {made.id: result}}],
        retrieved_ids=[3],
        final=True,
    )
    await engine.generate(QUESTION_ROLE, packet)
    assert seen[1]["tool_choice"] == "none"
    assistant, tool = seen[1]["messages"][2:]
    assert assistant["tool_calls"][0]["function"]["name"] == SEARCH
    assert tool == {
        "role": "tool",
        "tool_call_id": made.id,
        "content": json.dumps(result, ensure_ascii=False),
    }
    await engine.client.aclose()
