"""H2: exact-route checks are bounded no-work retries for background roles."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from enloq.application.accounts import AccountManager
from enloq.config import Settings, private_write
from enloq.domain.models import Chat
from enloq.inference.openrouter import MODEL, OpenRouterEngine

from backend.tests.proxy_helpers import ENDPOINT
from backend.tests.test_memory import KEY, conversation
from backend.tests.test_rebuild import message

# The incoming route's refusals and receipts; calming (ADR 53), which
# decides first, is covered by test_calming.
# Memory roles learn without switching on, as before ADR 56.
pytestmark = pytest.mark.usefixtures("calming_off", "text_rewriting", "remember_on")

ROLES = ("incoming_rewrite", "chat_memory")
# The same model through another provider: never a fallback for the exact route.
LOOKALIKE = "openrouter/xiaomi/mimo-v2.6-flash"


def exact_route(name=MODEL, **changes):
    """One entry of the proxy's GET /model/info, as it lists the MiMo route."""
    return {
        "model_name": name,
        "model_info": {
            "mode": "chat",
            "supports_response_schema": True,
            "supports_vision": True,
            "input_cost_per_token": 1.4e-07,
            "output_cost_per_token": 2.8e-07,
            "max_input_tokens": 1048576,
            **changes,
        },
    }


def priced(rates):
    """Listing changes for per-token rates named as the engine names them."""
    if not rates:
        return {}
    return {
        "input_cost_per_token": rates["prompt"],
        "output_cost_per_token": rates["completion"],
    }


def model_info(*routes):
    return {"data": list(routes)}


@pytest.fixture
async def rig(tmp_path):
    manager = AccountManager(Settings(data=tmp_path))
    manager.set_processing_threshold(0)
    app = manager.get(1)
    now = [2_000_000_000.0]
    app.store.clock = lambda: now[0]
    app.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored", kind="private")
    )
    state = SimpleNamespace(
        manager=manager,
        app=app,
        now=now,
        endpoints=[],
        status=200,
        headers={},
        checks=[],
        calls=[],
        before_listing=None,
    )

    async def handler(request):
        assert request.headers["authorization"] == "Bearer sk-authored-placeholder"
        if request.method == "GET":
            assert str(request.url) == f"{ENDPOINT}/model/info"
            state.checks.append(now[0])
            if state.before_listing:
                await state.before_listing()
            return httpx.Response(
                state.status,
                json=model_info(*state.endpoints),
                headers=state.headers,
            )
        assert request.method == "POST"
        assert str(request.url) == f"{ENDPOINT}/chat/completions"
        payload = json.loads(request.content)
        state.calls.append(payload)
        properties = payload["response_format"]["json_schema"]["schema"]["properties"]
        content = (
            {
                "person": {"add": [], "remove": [], "outdated": []},
                "account": {"add": [], "remove": [], "outdated": []},
            }
            if "person" in properties
            else {"text": "Authored rewrite.", "image_context": []}
            if "image_context" in properties
            else {"text": "Authored outgoing rewrite."}
        )
        choice = {"finish_reason": "stop"}
        result = {
            "id": "authored-route-recovery-receipt",
            "model": MODEL,
            "provider": "Xiaomi",
            "usage": {"cost": 0.000001},
            "choices": [choice],
        }
        if payload.get("stream"):
            choice["delta"] = {"content": json.dumps(content)}
            return httpx.Response(
                200, content=f"data: {json.dumps(result)}\n\ndata: [DONE]\n\n"
            )
        choice["message"] = {"content": json.dumps(content)}
        return httpx.Response(200, json=result)

    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-authored-placeholder")
    app.engine = OpenRouterEngine(
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
    yield state
    await manager.close()


def queue(rig, role):
    app = rig.app
    if role == "incoming_rewrite":
        app.sync.ingest(message(app))
        rig.now[0] += 30
    else:
        conversation(app, rig.now)
        app.memory.add(KEY, 0, "Keep this owner-authored fact.")
        app.memory.record(KEY, [f"{KEY}/1"])
        assert app.memory.schedule(KEY)
    return queued(rig, role)


def queued(rig, role):
    jobs = rig.app.store.rows(
        "SELECT * FROM jobs WHERE role=? AND state='queued'", (role,)
    )
    assert len(jobs) == 1
    return jobs[0]


def no_dispatch(rig):
    assert not rig.calls
    assert not rig.app.store.rows("SELECT * FROM reservations")
    assert not rig.app.store.rows("SELECT * FROM receipts")
    assert not rig.manager.registry.rows("SELECT * FROM model_reservations")
    assert not rig.manager.registry.rows("SELECT * FROM model_receipts")
    assert rig.manager.registry.budget(clock=rig.app.store.clock)["spent"] == 0
    assert rig.manager.registry.budget(clock=rig.app.store.clock)["unknown"] == 0


def finished(rig, role):
    jobs = rig.app.store.rows("SELECT * FROM jobs WHERE role=?", (role,))
    assert [job["state"] for job in jobs].count("done") == 1
    assert not any(job["state"] in {"queued", "claimed", "running"} for job in jobs)
    if role == "incoming_rewrite":
        block = rig.app.conversation(KEY)["blocks"][0]
        assert block["state"] == "ready" and block["rendered"] == "Authored rewrite."
    else:
        view = rig.app.memory.view(KEY)
        assert not view["error"] and not view["waiting"]
        assert [entry["text"] for entry in view["entries"]] == [
            "Keep this owner-authored fact."
        ]
    assert len(rig.calls) == 1
    assert rig.calls[0]["model"] == MODEL
    # The proxy route names its provider; no OpenRouter routing is sent.
    assert "provider" not in rig.calls[0]
    assert rig.calls[0]["extra_body"] == {"thinking": {"type": "enabled"}}
    assert rig.manager.registry.rows(
        "SELECT status,actual FROM model_reservations"
    ) == [{"status": "receipted", "actual": 1000}]
    budget = rig.manager.registry.budget(clock=rig.app.store.clock)
    assert budget["spent"] == 1000 and budget["unknown"] == 0


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize("unavailable", ["missing", "proxy_refusal"])
async def test_exact_route_retries_three_times_without_dispatch_then_needs_retry(
    rig, role, unavailable
):
    # A different provider being healthy never enables a fallback.
    rig.endpoints = [exact_route(LOOKALIKE)]
    if unavailable == "proxy_refusal":
        rig.endpoints.append(exact_route())
        rig.status = 503
    job = queue(rig, role)
    attempts = []
    for delay in (30, 60, 120):
        attempts.append(job["id"])
        await rig.app.process_due()
        no_dispatch(rig)
        next_job = queued(rig, role)
        assert next_job["id"] != job["id"]
        assert next_job["due"] == rig.now[0] + delay
        assert json.loads(next_job["payload"])["refusals"] == len(attempts)
        assert rig.app.store.rows(
            "SELECT state FROM jobs WHERE id=?", (job["id"],)
        ) == [{"state": "refused"}]
        checks = len(rig.checks)
        rig.now[0] = next_job["due"] - 0.001
        await rig.app.process_due()
        assert len(rig.checks) == checks
        rig.now[0] = next_job["due"]
        job = next_job
    attempts.append(job["id"])
    await rig.app.process_due()
    no_dispatch(rig)
    assert len(rig.checks) == 4 and len(set(attempts)) == 4
    assert not rig.app.store.rows(
        "SELECT id FROM jobs WHERE role=? AND state IN ('queued','claimed','running')",
        (role,),
    )
    if role == "incoming_rewrite":
        block = rig.app.conversation(KEY)["blocks"][0]
        assert block["state"] == "failed" and "Retry" in block["error"]
    else:
        assert "Retry" in rig.app.memory.view(KEY)["error"]
        assert not rig.app.memory.schedule(KEY)
    rig.now[0] += 3600
    await rig.app.process_due()
    assert len(rig.checks) == 4

    # A deliberate Retry starts fresh, including while the role is format-paused.
    for _ in range(3):
        rig.manager.registry.record_format(role, False)
    if role == "incoming_rewrite":
        rig.app.rewriting.retry(f"{KEY}/1")
    else:
        rig.app.memory.retry(KEY)
    explicit = queued(rig, role)
    assert explicit["id"] not in attempts
    assert json.loads(explicit["payload"])["explicit_retry"]
    await rig.app.process_due()
    retry = queued(rig, role)
    assert retry["due"] == rig.now[0] + 30
    assert json.loads(retry["payload"])["refusals"] == 1
    assert json.loads(retry["payload"])["explicit_retry"]
    no_dispatch(rig)
    rig.endpoints, rig.status = [exact_route()], 200
    rig.now[0] = retry["due"]
    await rig.app.process_due()
    finished(rig, role)


@pytest.mark.parametrize("role", ROLES)
async def test_route_recovery_uses_one_new_attempt_and_only_that_receipt(rig, role):
    original = queue(rig, role)
    await rig.app.process_due()
    no_dispatch(rig)
    retry = queued(rig, role)
    assert retry["id"] != original["id"]
    rig.endpoints = [exact_route()]
    rig.now[0] = retry["due"]
    await rig.app.process_due()
    finished(rig, role)
    assert rig.app.store.rows("SELECT id FROM reservations") == [{"id": retry["id"]}]
    assert rig.app.store.rows("SELECT id FROM receipts") == [{"id": retry["id"]}]
    assert len(rig.checks) == 2


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "header,delay", [("7.5", 7.5), ("0", 0), ("-1", 30), ("3601", 30), ("NaN", 30)]
)
async def test_route_listing_backoff_is_bounded_and_zero_is_honored(
    rig, role, header, delay
):
    # The proxy's own refusal carries the Retry-After.
    rig.status, rig.headers = 429, {"Retry-After": header}
    queue(rig, role)
    await rig.app.process_due()
    assert queued(rig, role)["due"] == rig.now[0] + delay
    no_dispatch(rig)


@pytest.mark.parametrize("role", ROLES)
async def test_off_and_budget_waits_keep_pending_route_retry_without_spending(
    rig, role
):
    queue(rig, role)
    await rig.app.process_due()
    pending = queued(rig, role)
    rig.manager.set_processing(1, False)
    rig.now[0] = pending["due"] + 3600
    await rig.app.process_due()
    held = queued(rig, role)
    assert held["id"] == pending["id"]
    assert json.loads(held["payload"])["refusals"] == 1
    assert len(rig.checks) == 1
    no_dispatch(rig)

    rig.endpoints = [exact_route()]
    rig.manager.registry.set_budget(0, clock=rig.app.store.clock)
    rig.manager.set_processing(1, True)
    await rig.app.process_due()
    waiting = queued(rig, role)
    assert waiting["id"] == pending["id"]
    assert waiting["due"] == rig.now[0] + 60
    assert json.loads(waiting["payload"])["refusals"] == 1
    no_dispatch(rig)
    if role == "incoming_rewrite":
        assert rig.app.conversation(KEY)["blocks"][0]["stage"] == "budget"
    else:
        assert not rig.app.memory.view(KEY)["error"]
    rig.manager.registry.set_budget(1_000_000_000, clock=rig.app.store.clock)
    rig.now[0] = waiting["due"]
    await rig.app.process_due()
    finished(rig, role)


@pytest.mark.parametrize("role", ROLES)
@pytest.mark.parametrize(
    "route",
    [
        exact_route(supports_vision=False),
        exact_route(supports_response_schema=False),
        exact_route(mode="completion"),
        exact_route(input_cost_per_token="NaN"),
        exact_route(output_cost_per_token="Infinity"),
        exact_route(input_cost_per_token=-1e-07),
    ],
    ids=[
        "missing-vision",
        "missing-response-schema",
        "not-chat",
        "nan",
        "infinite",
        "negative",
    ],
)
async def test_changed_route_contract_fails_immediately_without_retry(rig, role, route):
    rig.endpoints = [route]
    queue(rig, role)
    await rig.app.process_due()
    no_dispatch(rig)
    jobs = rig.app.store.rows("SELECT state FROM jobs WHERE role=?", (role,))
    assert len(jobs) == 1 and jobs[0]["state"] in {"failed", "cancelled"}
    if role == "incoming_rewrite":
        assert rig.app.conversation(KEY)["blocks"][0]["state"] == "failed"
    else:
        assert rig.app.memory.view(KEY)["error"]
    rig.now[0] += 3600
    await rig.app.process_due()
    assert len(rig.checks) == 1


@pytest.mark.parametrize("change", ["stop", "edit"])
async def test_late_route_refusal_never_requeues_a_stopped_or_superseded_attempt(
    rig, change
):
    original = queue(rig, "incoming_rewrite")
    started, release = asyncio.Event(), asyncio.Event()

    async def held():
        started.set()
        await release.wait()

    rig.before_listing = held
    running = asyncio.create_task(rig.app.process_due())
    try:
        await asyncio.wait_for(started.wait(), 2)
        if change == "stop":
            assert rig.app.rewriting.stop(KEY) == 1
        else:
            rig.app.sync.ingest(
                message(rig.app, revision=2, text="Changed authored source.")
            )
    finally:
        release.set()
        await running
    no_dispatch(rig)
    assert rig.app.store.rows(
        "SELECT state FROM jobs WHERE id=?", (original["id"],)
    ) == [{"state": "cancelled" if change == "stop" else "superseded"}]
    jobs = rig.app.store.rows(
        "SELECT * FROM jobs WHERE role='incoming_rewrite' AND state='queued'"
    )
    if change == "stop":
        assert not jobs
        assert rig.app.conversation(KEY)["blocks"][0]["state"] == "original"
    else:
        assert len(jobs) == 1
        assert jobs[0]["input_revision"] != original["input_revision"]
        assert not json.loads(jobs[0]["payload"]).get("refusals")


@pytest.mark.parametrize("role", ROLES)
async def test_turning_off_during_route_check_defers_the_same_attempt(rig, role):
    original = queue(rig, role)
    started, release = asyncio.Event(), asyncio.Event()

    async def held():
        started.set()
        await release.wait()

    rig.before_listing = held
    running = asyncio.create_task(rig.app.process_due())
    try:
        await asyncio.wait_for(started.wait(), 2)
        rig.manager.set_processing(1, False)
    finally:
        release.set()
        await running
    no_dispatch(rig)
    waiting = queued(rig, role)
    assert waiting["id"] == original["id"]
    assert not json.loads(waiting["payload"]).get("refusals")
    assert len(rig.checks) == 1
    rig.before_listing = None
    rig.endpoints = [exact_route()]
    rig.manager.set_processing(1, True)
    rig.now[0] = max(rig.now[0], waiting["due"])
    await rig.app.process_due()
    finished(rig, role)


async def test_owner_memory_edit_during_route_check_never_requeues_stale_revision(rig):
    original = queue(rig, "chat_memory")
    view = rig.app.memory.view(KEY)
    started, release = asyncio.Event(), asyncio.Event()

    async def held():
        started.set()
        await release.wait()

    rig.before_listing = held
    running = asyncio.create_task(rig.app.process_due())
    try:
        await asyncio.wait_for(started.wait(), 2)
        rig.app.memory.edit(
            KEY, view["revision"], view["entries"][0]["id"], "Owner correction."
        )
    finally:
        release.set()
        await running
    no_dispatch(rig)
    assert rig.app.store.rows(
        "SELECT state FROM jobs WHERE id=?", (original["id"],)
    ) == [{"state": "superseded"}]
    assert not rig.app.memory.job(KEY)
    revised = rig.app.memory.view(KEY)
    assert not revised["error"]
    assert [entry["text"] for entry in revised["entries"]] == ["Owner correction."]
