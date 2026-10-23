"""ADR 29 T1: the exact MiMo/Xiaomi Model Route, offline with mocked transport."""

import asyncio
import json
from decimal import Decimal

import httpx
import pytest
from enloq.application.accounts import AccountManager
from enloq.config import Settings, private_write
from enloq.domain.models import Refused, Unavailable
from enloq.inference.base import EngineResponse, ProviderRefused
from enloq.inference.openrouter import (
    LISTING,
    LOGICAL_MODEL,
    MODEL,
    MUSE,
    MUSE_LISTED,
    PROVIDER,
    MuseEngine,
    muse_rates,
    route_rates,
)
from enloq.inference.streaming import read_stream
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.accounts import AccountRegistry

from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_rebuild import fixture, message
from backend.tests.test_route_refusals import LOOKALIKE, exact_route, model_info

KEY = "sk-authored-placeholder"


def listing(*routes, status=200, headers=None):
    async def handler(request):
        assert request.method == "GET"
        assert str(request.url) == f"{ENDPOINT}/model/info"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        return httpx.Response(status, json=model_info(*routes), headers=headers)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_route_accepts_only_the_single_exact_proxy_route():
    other = exact_route(LOOKALIKE, input_cost_per_token=1e-09)
    async with listing(other, exact_route()) as client:
        rates = await route_rates(client, ENDPOINT, KEY)
    assert rates["model"] == LOGICAL_MODEL and rates["provider"] == PROVIDER
    assert rates["endpoint"] == ENDPOINT
    assert Decimal(rates["prompt"]) == Decimal("0.00000014")
    assert Decimal(rates["completion"]) == Decimal("0.00000028")
    assert rates["context_length"] == 1048576


@pytest.mark.parametrize(
    "routes",
    [
        [],
        [exact_route(LOOKALIKE)],
        [exact_route(), exact_route()],
        [exact_route(MODEL + "-lite")],
    ],
    ids=["missing", "other-provider-only", "ambiguous", "prefix-lookalike"],
)
async def test_missing_or_ambiguous_xiaomi_endpoint_is_a_no_work_refusal(routes):
    async with listing(*routes) as client:
        with pytest.raises(ProviderRefused, match="MiMo / Xiaomi"):
            await route_rates(client, ENDPOINT, KEY)


async def test_proxy_refusal_is_a_no_work_refusal_with_its_retry_after():
    async with listing(status=503, headers={"Retry-After": "7.5"}) as client:
        with pytest.raises(ProviderRefused, match="Model proxy HTTP 503") as raised:
            await route_rates(client, ENDPOINT, KEY)
    assert raised.value.retry_after == 7.5


async def test_missing_structured_outputs_is_a_changed_route_contract():
    route = exact_route(supports_response_schema=False)
    async with listing(route) as client:
        with pytest.raises(Unavailable) as raised:
            await route_rates(client, ENDPOINT, KEY)
    assert not isinstance(raised.value, ProviderRefused)


def stream(*events):
    frames = [": OPENROUTER PROCESSING\n\n"]
    for event in events:
        value = {"id": "authored-stream", "model": MODEL, "provider": "Xiaomi"}
        frames.append("data: " + json.dumps({**value, **event}) + "\n\n")
    frames.append("data: [DONE]\n\n")
    return httpx.Response(200, content="".join(frames).encode())


async def test_stream_shows_only_text_and_keeps_reasoning_counts_not_reasoning():
    shown = []
    response = await read_stream(
        stream(
            {"choices": [{"delta": {"reasoning": "AUTHORED-HIDDEN-REASONING"}}]},
            {"choices": [{"delta": {"content": '{"text":"Visible'}}]},
            {"choices": [{"delta": {"content": ' words"}'}, "finish_reason": "stop"}]},
            {
                "choices": [],
                "usage": {
                    "cost": 0.000002,
                    "completion_tokens": 40,
                    "completion_tokens_details": {"reasoning_tokens": 31},
                },
            },
        ),
        MODEL,
        shown.append,
    )
    assert shown == ["Visible", "Visible words"]
    assert "AUTHORED-HIDDEN-REASONING" not in json.dumps(
        [response.content, response.usage]
    )
    assert response.usage["completion_tokens_details"] == {"reasoning_tokens": 31}


def test_daily_budget_counts_earlier_muse_spending_across_the_switch(tmp_path):
    app = fixture(tmp_path)
    registry = AccountRegistry(tmp_path)
    registry.set_budget(25_000_000)
    day = registry.budget(clock=app.store.clock)["day"]
    with registry.transaction() as db:
        for identity, engine, actual, status in (
            ("muse-receipt", "muse", 6_000_000, "receipted"),
            ("muse-unknown", "muse", None, "unknown"),
            ("fixture", "offline", None, "unknown"),
        ):
            db.execute(
                "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created) VALUES (1,?,'s','r',?,?,?,0,?,?,0)",
                (identity, identity, "day:" + day, engine, actual, status),
            )
    state = registry.budget(clock=app.store.clock)
    # Offline authored fixtures stay exempt; both routes count.
    assert state["spent"] == 6_000_000 + FLAT_UNKNOWN
    assert state["calls"] == 2 and state["unknown"] == 1

    def job(identity):
        value = dict(
            id=identity,
            scope="test",
            role="incoming_rewrite",
            input_revision=identity,
            state="queued",
            due=0,
            payload="{}",
            error=None,
        )
        with app.store.transaction() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", tuple(value.values())
            )
        return value

    app.rewriting.work.reserve(job("first"), "mimo")
    with pytest.raises(Refused, match="Daily budget"):
        app.rewriting.work.reserve(job("second"), "mimo")
    app.store.close()


def test_format_counts_restart_once_per_route_change_not_on_restart(tmp_path):
    registry = AccountRegistry(tmp_path)
    for _ in range(3):
        registry.record_format("chat_memory", False)
    registry.record_format("incoming_rewrite", False)
    assert registry.paused_roles() == {"chat_memory"}
    settings = Settings(mode="personal", data=tmp_path)
    AccountManager(settings)
    assert registry.format_failures() == {} and not registry.paused_roles()
    for _ in range(3):
        registry.record_format("chat_memory", False)
    AccountManager(settings)  # A restart on the same route keeps the counts.
    assert registry.paused_roles() == {"chat_memory"}
    moved = {"chat_memory": "another/model@provider"}
    assert registry.bind_routes(moved) == {"chat_memory"}
    assert not registry.paused_roles()
    assert not registry.bind_routes(moved)


def test_provider_user_is_one_stable_private_random_pseudonym(tmp_path):
    registry = AccountRegistry(tmp_path)
    registry.register(5_001, "accounts/5001/personal.session")
    first = registry.provider_user()
    assert first == AccountRegistry(tmp_path).provider_user()
    assert len(first) == 32 and int(first, 16) >= 0
    assert "5001" not in first
    assert first != AccountRegistry(tmp_path / "other").provider_user()
    assert (tmp_path / "accounts.sqlite3").stat().st_mode & 0o777 == 0o600


async def test_every_account_engine_sends_the_same_pseudonym(tmp_path, monkeypatch):
    from enloq.application import accounts
    from enloq.inference.fake import FakeEngine
    from enloq.transport.fake import FakeTransport

    seen = []

    def engine(settings, *, user):
        seen.append(user)
        return FakeEngine()

    monkeypatch.setattr(accounts, "OpenRouterEngine", engine)
    # The decision route and calmed rewrites' Muse route send the same
    # pseudonym (ADR 30, ADR 53).
    monkeypatch.setattr(accounts, "DeciderEngine", engine)
    monkeypatch.setattr(accounts, "MuseEngine", engine)
    monkeypatch.setattr(accounts, "PersonalTransport", lambda *a, **k: FakeTransport())
    registry = AccountRegistry(tmp_path)
    for identity in (5_001, 5_002):
        registry.register(identity, f"accounts/{identity}/personal.session")
    manager = AccountManager(Settings(mode="personal", data=tmp_path))
    assert seen == [registry.provider_user()] * 6
    await manager.close()


@pytest.mark.parametrize("role", ["incoming_rewrite", "outgoing_rewrite"])
async def test_each_attempt_uses_its_roles_deadline(tmp_path, role):
    app = fixture(tmp_path)
    asked = []

    class Engine:
        name, model, provider = "mimo", LOGICAL_MODEL, PROVIDER

        def attempt_seconds(self, value):
            asked.append(value)
            return 0.05

        async def generate(self, value, packet):
            await asyncio.sleep(1)
            return EngineResponse({"text": "Too late."}, actual_nanos=1)

    job = dict(
        id="deadline",
        scope="1/user:42",
        role=role,
        input_revision="r",
        state="queued",
        due=0,
        payload="{}",
        error=None,
    )
    with app.store.transaction() as db:
        db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", tuple(job.values()))
    with pytest.raises(Refused, match="Provider unavailable"):
        await app.rewriting.work.run(Engine(), job, {}, lambda: True)
    assert asked == [role]
    assert app.store.rows("SELECT status FROM reservations") == [{"status": "unknown"}]
    app.sync.ingest(message(app))  # The store stays usable after the timeout.
    app.store.close()


@pytest.mark.parametrize(
    "usage,expected",
    [
        ({"cost": 0, "cost_details": {"upstream_inference_cost": 0.00022204}}, 222040),
        (
            {"cost": 0.0004734, "cost_details": {"upstream_inference_cost": 0.0004734}},
            473400,
        ),
        ({"cost": 0.0003, "cost_details": {"upstream_inference_cost": 0.0001}}, 300000),
        ({"cost": 0.0000012}, 1200),
        ({"cost": "NaN", "cost_details": {"upstream_inference_cost": -1}}, None),
        ({}, None),
    ],
    ids=["zero-charge", "equal", "charge-larger", "no-details", "invalid", "missing"],
)
def test_receipts_count_the_larger_of_charged_and_upstream_cost(usage, expected):
    from enloq.inference.streaming import cost_nanos

    assert cost_nanos(usage) == expected


async def test_zero_charge_receipt_still_counts_its_upstream_cost(tmp_path):
    seen = []

    async def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "authored-receipt",
                "model": MODEL,
                "provider": "Xiaomi",
                "usage": {
                    "cost": 0,
                    "is_byok": False,
                    "cost_details": {"upstream_inference_cost": 0.00008036},
                },
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"text":"Authored."}'},
                    }
                ],
            },
        )

    from enloq.config import private_write
    from enloq.inference.openrouter import OpenRouterEngine

    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-authored-placeholder")
    engine = OpenRouterEngine(
        Settings(
            mode="personal",
            data=tmp_path,
            refs={
                "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
                "MUSE_API_KEY_REF": str(tmp_path / "key"),
            },
        ),
        user="authored-provider-pseudonym",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    response = await engine.structured(
        "Authored instructions.",
        {"draft": {"text": "Own words."}},
        {"type": "object"},
        role="outgoing_rewrite",
    )
    assert response.actual_nanos == 80360
    assert response.usage["is_byok"] is False
    await engine.client.aclose()


@pytest.mark.parametrize(
    "raw",
    ['{"text":"Two points:\\n• one\\n• two"}', '{"text":"Two points:\n• one\n• two"}'],
)
def test_structured_line_breaks_validate_escaped_or_raw(raw):
    from enloq.inference.work import validate_output

    result = validate_output("outgoing_rewrite", EngineResponse(raw), {})
    assert result.text == "Two points:\n• one\n• two"


@pytest.mark.parametrize("raw", ['{"text":"Bad\x01control"}', '{"text":"a\x00"}'])
def test_other_raw_control_characters_stay_invalid_json(raw):
    from enloq.inference.work import InvalidOutput, validate_output

    with pytest.raises(InvalidOutput, match="invalid_json"):
        validate_output("outgoing_rewrite", EngineResponse(raw), {})


def test_stream_preview_shows_raw_line_breaks_and_rejects_other_controls():
    from enloq.inference.streaming import TextPreview

    preview = TextPreview()
    assert preview.feed('{"text":"First topic.\n\nSecond') == ("First topic.\n\nSecond")
    with pytest.raises(ValueError):
        TextPreview().feed('{"text":"bad\x07"')


# ADR 53: calmed rewrites on Muse Spark 1.3.
def muse_info(**changes):
    return exact_route(MUSE, **{"input_cost_per_token": None, **changes})


def muse_listing(*endpoints, data_id=MUSE_LISTED):
    return {"data": {"id": data_id, "endpoints": list(endpoints)}}


def muse_endpoint(**changes):
    return {
        "name": "Meta | meta/muse-spark-1.3-contributor-20260902",
        "tag": "meta",
        "status": 0,
        "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
        "context_length": 1048576,
        **changes,
    }


def muse_client(routes, listed, requests=None):
    async def handler(request):
        if requests is not None:
            requests.append(request)
        if str(request.url) == f"{ENDPOINT}/model/info":
            assert request.headers["authorization"] == f"Bearer {KEY}"
            return httpx.Response(200, json=model_info(*routes))
        # OpenRouter's public listing: no proxy key goes to it.
        assert str(request.url) == f"{LISTING}/models/{MUSE_LISTED}/endpoints"
        assert "authorization" not in request.headers
        return httpx.Response(200, json=listed)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_muse_rates_come_from_the_public_listing_of_its_one_meta_endpoint():
    listed = muse_listing(muse_endpoint(), muse_endpoint(tag="other", status=0))
    async with muse_client([exact_route(), muse_info()], listed) as client:
        rates = await muse_rates(client, ENDPOINT, KEY)
    assert rates["model"] == MUSE and rates["provider"] == "meta"
    assert Decimal(rates["prompt"]) == Decimal("0.0000001")
    assert rates["context_length"] == 1048576


@pytest.mark.parametrize(
    "routes,listed,error",
    [
        ([exact_route()], muse_listing(muse_endpoint()), "Muse / Meta"),
        ([muse_info()], muse_listing(), "Muse / Meta"),
        ([muse_info()], muse_listing(muse_endpoint(status=1)), "Muse / Meta"),
        (
            [muse_info()],
            muse_listing(
                muse_endpoint(pricing={"prompt": "0.0000002", "completion": "0"})
            ),
            "exceed the adopted rates",
        ),
    ],
    ids=["no-proxy-route", "no-endpoint", "endpoint-down", "dearer"],
)
async def test_an_unqualified_muse_route_is_a_no_work_refusal(routes, listed, error):
    async with muse_client(routes, listed) as client:
        with pytest.raises(ProviderRefused, match=error):
            await muse_rates(client, ENDPOINT, KEY)


async def test_muse_sends_no_extra_body_and_checks_its_echo(tmp_path):
    sent = []

    async def handler(request):
        if request.method == "GET":
            if str(request.url) == f"{ENDPOINT}/model/info":
                return httpx.Response(200, json=model_info(muse_info()))
            return httpx.Response(200, json=muse_listing(muse_endpoint()))
        sent.append(json.loads(request.content))
        event = {
            "model": MUSE,
            "choices": [
                {
                    "delta": {
                        "content": json.dumps(
                            {
                                "parts": [{"kind": "paragraph", "text": "Calm."}],
                                "image_context": [],
                            }
                        )
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.00001},
        }
        return httpx.Response(
            200, content="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
        )

    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", KEY)
    settings = Settings(
        mode="personal",
        data=tmp_path / "accounts",
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )
    engine = MuseEngine(
        settings,
        user="x" * 32,
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    packet = {"messages": [], "context": [], "calm": True}
    await engine.prepare(packet)
    shown = []
    response = await engine.generate_stream("incoming_rewrite", packet, shown.append)
    assert response.usage["cost"] == 0.00001
    (body,) = sent
    assert body["model"] == MUSE and "extra_body" not in body
    assert "calm" not in body["messages"][1]["content"][0]["text"]
    await engine.client.aclose()
