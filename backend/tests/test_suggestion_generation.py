"""S2: independent automatic suggestion generation under shared accounting.

Fake providers only. One processed request per admitted source revision; the
incoming result never waits for suggestions, and late output never revives a
withdrawn set.
"""

import asyncio
import json

import pytest
from enloq.application.blocks import enqueue
from enloq.application.capacity import RANK, Capacity
from enloq.application.content_readiness import content_kind
from enloq.domain.models import ProcessingPaused, Refused, Unavailable
from enloq.domain.suggestions import QUALIFYING
from enloq.inference.base import EngineResponse, ProviderRefused
from enloq.inference.fake import choice, label_choice
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.accounts import AccountRegistry

from backend.tests.test_emotion import KEY
from backend.tests.test_rebuild import fixture, message

# Suggestions following ADR 30's decision; calming (ADR 53), which decides
# first, is covered by test_calming.
pytestmark = pytest.mark.usefixtures("calming_off", "text_rewriting")

OPTIONS = {
    "options": [
        {"purpose": "acknowledge", "text": "That sounds exhausting."},
        {"purpose": "clarify", "text": "Do you want to talk about it?"},
    ]
}


class Engine:
    """Authored responses; not ``offline``, so the shared Daily Budget applies."""

    name, model, provider = "mimo", "authored-model", "xiaomi"

    def __init__(self):
        self.calls = []
        self.emotion = "sad"
        # The Decider's usefulness probability for incoming blocks (ADR 49).
        self.useful = 0.9
        self.options = OPTIONS
        self.failure = None
        self.refusals = 0
        self.contract = False
        self.gate = None
        self.started = asyncio.Event()

    async def prepare(self, packet):
        if self.contract:
            raise Unavailable("Exact route lacks required output parameters")
        if self.refusals:
            self.refusals -= 1
            raise ProviderRefused("Exact MiMo / Xiaomi route unavailable")

    async def generate(self, role, packet):
        self.calls.append((role, json.loads(json.dumps(packet, default=str))))
        if role == "incoming_rewrite":
            return EngineResponse(
                {"text": "Authored combined rewrite.", "image_context": []},
                actual_nanos=1000,
            )
        assert role == "response_suggestions"
        self.started.set()
        if self.gate:
            await self.gate.wait()
        if self.failure == "format":
            return EngineResponse(
                {"options": [{"purpose": "advice", "text": "Do this."}]},
                actual_nanos=1000,
            )
        if self.failure == "one_sided":
            # ADR 49 Q4: an answer with no contrasting stance.
            return EngineResponse(
                {"options": [{"purpose": "accept", "text": "Yes, I'll be there."}]},
                actual_nanos=1000,
            )
        if self.failure == "incomplete":
            return EngineResponse(
                self.options, usage={"finish_reason": "length"}, actual_nanos=1000
            )
        if self.failure == "unknown":
            raise RuntimeError("Authored provider loss after dispatch")
        if self.failure == "busy":
            raise ProviderRefused("Model provider HTTP 429")
        return EngineResponse(self.options, actual_nanos=1000)

    def count(self, role="response_suggestions"):
        return [r for r, _ in self.calls].count(role)


class Decider:
    """Authored Emotion Classification of the engine's authored emotion (ADR
    30); free, so the suggestion budget arithmetic stays the engine's."""

    name, model, provider = "offline", "authored-decisions", "local"

    def __init__(self, source):
        self.source, self.calls = source, []
        # ADR 49 Q19: the labels it gives (None keeps the authored ones), a
        # refusal of the label request and a gate holding it.
        self.labels = None
        self.refuse_labels = False
        self.gate = None

    async def generate(self, role, packet):
        self.calls.append((role, packet))
        if role == "suggestion_labels":
            if self.gate:
                await self.gate.wait()
            if self.refuse_labels:
                raise ProviderRefused("Model provider HTTP 403")
            labels = self.labels or [
                o["purpose"] for o in self.source.options["options"]
            ]
            return EngineResponse(
                {
                    f"purpose_{index}": label_choice(label)
                    for index, label in enumerate(labels[: packet["replies"]], 1)
                },
                actual_nanos=0,
            )
        code = self.source.emotion
        answers = {
            "emotion": choice(code, 0.9),
            # ADR 49 Q2: the authored usefulness answer.
            "suggestions_useful": {"type": "noul", "noul": self.source.useful},
        }
        return EngineResponse(answers if code else {}, actual_nanos=0)


def rig(tmp_path, *, account=1, clock=None):
    app = fixture(tmp_path, account=account, clock=clock)
    registry = AccountRegistry(tmp_path)
    app.engine = Engine()
    app.decider = Decider(app.engine)
    # As the account manager wires the shared registry for every account.
    app.rewriting.work.guidance = registry.guidance
    app.rewriting.work.on_format = registry.record_format
    app.rewriting.paused_roles = registry.paused_roles
    return app, registry


async def drain(app, limit=20):
    """Run every due job; incoming quiet waits are skipped for authored tests."""
    for _ in range(limit):
        with app.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET due=0 WHERE role='incoming_rewrite' AND state='queued'"
            )
        job = app.rewriting.next_job()
        if not job:
            return
        if app.rewriting.claim(job):
            await app.rewriting.run(job)
    raise AssertionError("Jobs kept arriving")


def jobs(app, role="response_suggestions", state=None):
    return app.store.rows(
        "SELECT * FROM jobs WHERE role=?" + (" AND state=?" if state else ""),
        (role, state) if state else (role,),
    )


async def incoming_only(app):
    """The incoming rewrite and the Emotion Classification that follows it,
    stopping before any suggestion request."""
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET due=0 WHERE role='incoming_rewrite'")
    job = app.rewriting.next_job()
    assert job["role"] == "incoming_rewrite" and app.rewriting.claim(job)
    await app.rewriting.run(job)
    job = app.rewriting.next_job()
    assert job["role"] == "emotion_classification" and app.rewriting.claim(job)
    await app.rewriting.run(job)


async def test_one_processed_request_per_admitted_source_revision(tmp_path):
    app, registry = rig(tmp_path)
    app.sync.ingest(message(app, 1, text="I had an awful day."))
    await incoming_only(app)
    block = app.conversation(KEY)["blocks"][0]
    # Readiness, unseen state and notifications never wait for suggestions.
    assert block["state"] == "ready" and content_kind([block]) == "rewrite"
    assert app.suggestions.view(KEY).state == "preparing"
    assert len(jobs(app, state="queued")) == 1
    await drain(app)
    view = app.suggestions.view(KEY)
    assert view.state == "ready" and [o.text for o in view.options] == [
        o["text"] for o in OPTIONS["options"]
    ]
    assert app.engine.count() == 1
    receipt = app.store.rows(
        "SELECT * FROM receipts WHERE role='response_suggestions'"
    )[0]
    assert json.loads(receipt["usage"])["guidance_version"] == 0

    # Reads, restart, tone, guidance and an unchanged normal re-run reuse it.
    for _ in range(3):
        app.conversation(KEY)
        app.suggestions.view(KEY)
    registry.set_tone("friendly")
    registry.set_guidance({"suggestions": "Owner-edited suggestion guidance."})
    restarted, _ = rig(tmp_path)
    restarted.store.recover()
    await drain(restarted)
    with restarted.store.transaction() as db:
        enqueue(db, f"{KEY}/1", block["revision"], 0, klass="catchup")
    await drain(restarted)
    assert restarted.engine.count("incoming_rewrite") == 1
    # A failed status cannot turn completed output into a regeneration control.
    with restarted.store.transaction() as db:
        db.execute("UPDATE blocks SET state='failed',error='Authored failure'")
    with pytest.raises(Refused, match="already rewritten"):
        restarted.rewriting.retry(f"{KEY}/1")
    await drain(restarted)
    assert restarted.engine.count("incoming_rewrite") == 1
    assert restarted.engine.count() == 0
    assert len(jobs(restarted)) == 1
    assert restarted.suggestions.view(KEY).attempt == view.attempt

    # A genuinely changed source (a new message in the block) is admitted once.
    restarted.sync.ingest(message(restarted, 2, text="It keeps getting worse."))
    await drain(restarted)
    assert restarted.engine.count() == 1 and len(jobs(restarted)) == 2
    changed = restarted.suggestions.view(KEY)
    assert changed.state == "ready" and changed.attempt != view.attempt
    # The guidance version used is the one current at dispatch.
    receipts = restarted.store.rows(
        "SELECT usage FROM receipts WHERE role='response_suggestions'"
    )
    assert {json.loads(r["usage"])["guidance_version"] for r in receipts} == {0, 1}


@pytest.mark.parametrize("code", ["neutral", "unknown", None, *sorted(QUALIFYING)])
async def test_without_a_yes_only_usable_non_neutral_emotion_schedules(tmp_path, code):
    app, _ = rig(tmp_path)
    app.engine.emotion, app.engine.useful = code, 0.1
    app.sync.ingest(message(app))
    await drain(app)
    assert bool(jobs(app)) == (code in QUALIFYING)
    assert app.engine.count() == (code in QUALIFYING)


@pytest.mark.parametrize("useful", [0.4999, 0.5])
async def test_a_qualifying_emotion_suggests_whatever_the_usefulness(tmp_path, useful):
    # ADR 56: either signal, where ADR 49 Q2 needed both.
    app, _ = rig(tmp_path)
    app.engine.useful = useful
    app.sync.ingest(message(app, text="I had an awful day."))
    await drain(app)
    record = app.emotion.record(KEY)
    assert record["code"] == "sad" and record["usefulness"] == useful
    assert record["useful"] is (useful >= 0.5)
    assert len(jobs(app)) == 1 and app.engine.count() == 1
    assert app.suggestions.view(KEY).state == "ready"


@pytest.mark.parametrize("explicit", ["retry", "older"])
async def test_explicit_processing_never_starts_suggestions(tmp_path, explicit):
    now = [2_000_000_000.0]
    app, _ = rig(tmp_path, clock=lambda: now[0])
    app.engine.emotion, app.engine.useful = "neutral", 0.1
    app.sync.ingest(message(app))
    await drain(app)
    app.engine.emotion = "angry"
    if explicit == "retry":
        generate = app.engine.generate

        async def failed(role, packet):
            response = await generate(role, packet)
            return (
                EngineResponse({"wrong": "shape"})
                if role == "incoming_rewrite"
                else response
            )

        app.engine.generate = failed
        app.sync.ingest(message(app, revision=2, text="Changed authored wording"))
        await drain(app)
        app.engine.generate = generate
        app.rewriting.retry(f"{KEY}/1")
    else:
        app.sync.ingest(message(app, 2, outgoing=True))
        app.sync.ingest(message(app, 3), target=False)
        assert app.rewriting.process_older(KEY) == 1
    await drain(app)
    assert app.engine.count("incoming_rewrite") == (3 if explicit == "retry" else 2)
    assert app.engine.emotion == "angry" and app.emotion.view(KEY).code == "angry"
    assert not jobs(app) and app.engine.count() == 0
    assert app.suggestions.view(KEY).reason == "unavailable"


@pytest.mark.usefixtures("remember_on")
async def test_provider_packet_carries_both_memories_at_dispatch(tmp_path):
    # ADR 55 Q1 B, Q15, Q16: what the person and the owner care about.
    import httpx
    from enloq.config import Settings, private_write
    from enloq.inference.guidance import SUGGESTIONS_CORE
    from enloq.inference.openrouter import (
        BACKGROUND_OUTPUT_TOKENS,
        MODEL,
        OpenRouterEngine,
    )

    from backend.tests.proxy_helpers import ENDPOINT
    from backend.tests.test_route_refusals import exact_route, model_info

    app, _ = rig(tmp_path)
    requests = []

    async def handler(request):
        if request.method == "GET":
            assert str(request.url) == f"{ENDPOINT}/model/info"
            return httpx.Response(200, json=model_info(exact_route()))
        payload = json.loads(request.content)
        requests.append(payload)
        properties = payload["response_format"]["json_schema"]["schema"]["properties"]
        content = (
            OPTIONS
            if "options" in properties
            else {"text": "Authored.", "image_context": []}
        )
        body = {
            "id": "authored-receipt",
            "model": MODEL,
            "provider": "Xiaomi",
            "usage": {"cost": 0.000001},
            "choices": [{"finish_reason": "stop"}],
        }
        if payload.get("stream"):
            body["choices"][0]["delta"] = {"content": json.dumps(content)}
            return httpx.Response(
                200, content=f"data: {json.dumps(body)}\n\ndata: [DONE]\n\n"
            )
        body["choices"][0]["message"] = {"content": json.dumps(content)}
        return httpx.Response(200, json=body)

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
    app.decider.source.emotion = "worried"
    app.sync.ingest(message(app, 1, outgoing=True, text="Owner earlier words"))
    app.memory.add(KEY, 0, "Protected memory fact")
    app.memory.add("account", 0, "An owner priority")
    app.sync.ingest(message(app, 2, text="I am worried about tomorrow."))
    await drain(app)
    incoming, suggestion = requests
    assert "Protected memory fact" in json.dumps(incoming, ensure_ascii=False)
    # Media and calmed rewrites read only the person's memory.
    assert "An owner priority" not in json.dumps(incoming, ensure_ascii=False)
    system, user = suggestion["messages"]
    assert system["content"].startswith(SUGGESTIONS_CORE)
    assert "may_say" in system["content"]
    assert "[Owner guidance: response suggestions]" in system["content"]
    assert "Faithfulness" not in system["content"]
    data = json.loads(user["content"][0]["text"])
    assert set(data) == {
        "messages",
        "context",
        "direct_reply",
        "recipient_emotion",
        "memory",
    }
    assert data["memory"]["person"] == [{"text": "Protected memory fact"}]
    # The owner wrote it: it came from no chat, so it only shapes.
    assert data["memory"]["owner"] == [{"text": "An owner priority", "may_say": False}]
    assert len(data["memory"]["today"]) == 10
    assert data["recipient_emotion"] == "worried"
    assert [m["id"] for m in data["context"]] == [1]
    assert data["context"][0]["outgoing"] is True
    assert len(user["content"]) == 1  # No image bytes.
    schema = suggestion["response_format"]["json_schema"]
    assert schema["strict"] is True
    assert schema["schema"]["properties"]["options"]["maxItems"] == 3
    option = schema["schema"]["$defs"]["SuggestionOption"]
    assert option["properties"]["purpose"]["enum"] == [
        "acknowledge",
        "support",
        "clarify",
        "celebrate",
        # ADR 49 Q4, Q8: answer stances, never offered alone.
        "accept",
        "decline",
        "later",
    ]
    assert option["properties"]["text"]["maxLength"] == 280
    assert set(option["required"]) == {"purpose", "text"}
    # The proxy route names its provider; no OpenRouter routing is sent.
    assert suggestion["model"] == MODEL and "provider" not in suggestion
    assert suggestion["max_tokens"] == BACKGROUND_OUTPUT_TOKENS
    assert "stream" not in suggestion and "tool_choice" not in suggestion
    assert "reasoning" not in suggestion
    assert suggestion["extra_body"] == {"thinking": {"type": "enabled"}}
    assert app.suggestions.view(KEY).state == "ready"
    await app.engine.client.aclose()


async def test_shared_budget_across_accounts_waits_without_dispatch(tmp_path):
    one, registry = rig(tmp_path)
    two, _ = rig(tmp_path, account=2)
    registry.set_budget(1500)
    two.sync.ingest(message(two))
    await incoming_only(two)
    one.sync.ingest(message(one))
    await incoming_only(one)
    # Both suggestion jobs exist; shared counted spending is now 2,000.
    for app in (one, two):
        await drain(app)
        assert app.engine.count() == 0
        assert (
            app.suggestions.view(
                app.store.chat(f"{app.store.account_id}/user:42").key
            ).reason
            == "budget"
        )
    queued = jobs(one, state="queued")[0]
    assert "budget" in queued["error"].lower()
    assert not one.store.rows("SELECT * FROM reservations WHERE id=?", (queued["id"],))
    registry.set_budget(1_000_000_000)
    with one.store.transaction() as db:
        db.execute("UPDATE jobs SET due=0")
    await drain(one)
    assert one.engine.count() == 1 and one.suggestions.view(KEY).state == "ready"
    assert jobs(one)[0]["id"] == queued["id"]  # The same undispatched attempt.
    assert registry.budget()["spent"] == 3000


async def test_zero_budget_refuses_new_suggestion_calls(tmp_path):
    app, registry = rig(tmp_path)
    app.sync.ingest(message(app))
    await incoming_only(app)
    registry.set_budget(0)
    await drain(app)
    assert app.engine.count() == 0
    assert app.suggestions.view(KEY).reason == "budget"
    # The incoming rewrite and its classification only.
    assert len(app.store.rows("SELECT * FROM reservations")) == 2


async def test_unknown_cost_counts_and_fails_without_retry(tmp_path):
    app, registry = rig(tmp_path)
    app.engine.failure = "unknown"
    app.sync.ingest(message(app))
    await drain(app)
    view = app.suggestions.view(KEY)
    assert (view.state, view.reason) == ("failed", "provider")
    budget = registry.budget()
    assert budget["unknown"] == 1 and budget["spent"] == 1000 + FLAT_UNKNOWN
    # No automatic resampling of a processed attempt, including after restart.
    restarted, _ = rig(tmp_path)
    restarted.store.recover()
    await drain(restarted)
    assert restarted.engine.count() == 0
    assert restarted.suggestions.view(KEY).state == "failed"
    assert app.conversation(KEY)["blocks"][0]["state"] == "ready"


@pytest.mark.parametrize("failure", ["format", "incomplete"])
async def test_format_failures_never_pause_or_poison_other_roles(tmp_path, failure):
    app, registry = rig(tmp_path)
    app.engine.failure = failure
    for identity in range(1, 5):
        app.sync.ingest(message(app, identity, text=f"Message {identity}"))
        await drain(app)
        view = app.suggestions.view(KEY)
        assert (view.state, view.reason) == ("failed", "format")
        assert app.conversation(KEY)["blocks"][0]["state"] == "ready"
    # Four changed sources, four attempts: no role pause, no incoming count.
    assert app.engine.count() == 4
    assert registry.paused_roles() == set()
    assert "response_suggestions" not in registry.format_failures()
    assert registry.format_failures().get("incoming_rewrite", 0) == 0
    codes = {
        json.loads(r["usage"]).get("format_failure")
        for r in app.store.rows(
            "SELECT usage FROM receipts WHERE role='response_suggestions'"
        )
    }
    assert codes == {"incomplete_response" if failure == "incomplete" else "schema"}


LABELLED = {
    "options": [
        {"purpose": "clarify", "text": "Yes, I can pick her up. What time?"},
        {"purpose": "clarify", "text": "Sorry, I can't tomorrow."},
    ]
}


async def test_the_decision_model_labels_every_option(tmp_path):
    """ADR 49 Q19: MiMo's labels are replaced by the decision model's, which
    reads only the conversation and the replies' text."""
    app, _ = rig(tmp_path)
    app.engine.options = LABELLED
    app.decider.labels = ["accept", "decline"]
    app.sync.ingest(message(app))
    await drain(app)
    view = app.suggestions.view(KEY)
    assert view.state == "ready"
    assert [(o.purpose, o.text) for o in view.options] == [
        ("accept", "Yes, I can pick her up. What time?"),
        ("decline", "Sorry, I can't tomorrow."),
    ]
    requests = [p for role, p in app.decider.calls if role == "suggestion_labels"]
    assert len(requests) == 1 and requests[0]["replies"] == 2
    state = requests[0]["state"]
    assert state["replies"] == [
        {"id": 1, "text": "Yes, I can pick her up. What time?"},
        {"id": 2, "text": "Sorry, I can't tomorrow."},
    ]
    assert any(entry.get("latest_block") for entry in state["conversation"])
    assert "clarify" not in json.dumps(state)
    labels = app.store.rows("SELECT state FROM jobs WHERE role='suggestion_labels'")
    assert [row["state"] for row in labels] == ["done"]


@pytest.mark.parametrize(
    "authored,labels,ready",
    [
        # MiMo's own labels are one-sided, the decision model's are not.
        ("one_sided", ["accept", "decline"], True),
        # MiMo's labels contrast, but the replies say one thing.
        ("contrasting", ["accept", "accept"], False),
        # Q20: two deferrals commit to nothing.
        ("contrasting", ["later", "later"], True),
    ],
)
async def test_only_the_decision_models_labels_are_judged_one_sided(
    tmp_path, authored, labels, ready
):
    app, _ = rig(tmp_path)
    purposes = (
        ["accept", "accept"] if authored == "one_sided" else ["accept", "decline"]
    )
    app.engine.options = {
        "options": [
            {"purpose": purposes[0], "text": "Yes, of course."},
            {"purpose": purposes[1], "text": "Sure, see you then."},
        ]
    }
    app.decider.labels = labels
    app.sync.ingest(message(app))
    await drain(app)
    view = app.suggestions.view(KEY)
    if ready:
        assert view.state == "ready"
        assert [o.purpose for o in view.options] == labels
    else:
        assert (view.state, view.reason) == ("failed", "format")
    # The written set is never resampled.
    assert app.engine.count() == 1


async def test_a_refused_label_request_fails_the_set_without_resampling(tmp_path):
    app, _ = rig(tmp_path)
    app.engine.options = LABELLED
    app.decider.refuse_labels = True
    app.sync.ingest(message(app))
    await drain(app)
    view = app.suggestions.view(KEY)
    assert (view.state, view.reason) == ("failed", "refused")
    assert app.engine.count() == 1
    receipts = app.store.rows(
        "SELECT error FROM receipts WHERE role='suggestion_labels'"
    )
    assert [row["error"] for row in receipts] == ["Provider refused before processing"]
    refused = app.store.rows(
        "SELECT status FROM reservations WHERE id IN (SELECT id FROM jobs WHERE role='suggestion_labels')"
    )
    assert [row["status"] for row in refused] == ["refused"]


async def test_a_set_being_labelled_shows_as_preparing(tmp_path):
    app, _ = rig(tmp_path)
    app.engine.options = LABELLED
    app.decider.labels = ["accept", "decline"]
    app.decider.gate = asyncio.Event()
    app.sync.ingest(message(app))
    running = asyncio.create_task(drain(app))
    for _ in range(200):
        if any(role == "suggestion_labels" for role, _ in app.decider.calls):
            break
        await asyncio.sleep(0.01)
    view = app.suggestions.view(KEY)
    assert (view.state, view.reason) == ("preparing", "running")
    app.decider.gate.set()
    await running
    assert app.suggestions.view(KEY).state == "ready"


async def test_no_work_refusals_retry_three_times_then_fail_visibly(tmp_path):
    now = [2_000_000_000.0]
    app, registry = rig(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app))
    await incoming_only(app)
    app.engine.refusals = 4
    attempts = []
    for delay in (30, 60, 120):
        await drain(app)
        queued = jobs(app, state="queued")[0]
        attempts.append(queued["id"])
        assert queued["due"] == now[0] + delay
        assert app.suggestions.view(KEY).reason == "retrying"
        now[0] = queued["due"]
    await drain(app)
    view = app.suggestions.view(KEY)
    assert (view.state, view.reason) == ("failed", "refused")
    assert len(set(attempts)) == 3
    assert app.engine.count() == 0
    # The incoming rewrite and its classification only.
    assert len(app.store.rows("SELECT * FROM reservations")) == 2
    assert registry.budget(clock=lambda: now[0])["spent"] == 1000


async def test_refusal_then_recovery_and_http_refusal_backoff(tmp_path):
    now = [2_000_000_000.0]
    app, _ = rig(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app))
    await incoming_only(app)
    app.engine.refusals = 1
    await drain(app)
    retry = jobs(app, state="queued")[0]
    now[0] = retry["due"]
    app.engine.failure = "busy"
    await drain(app)
    # HTTP 429 also did no work: a refused reservation counts nothing.
    busy = jobs(app, state="queued")[0]
    assert busy["id"] != retry["id"] and busy["due"] == now[0] + 60
    assert app.store.rows(
        "SELECT status FROM reservations WHERE id=?", (retry["id"],)
    ) == [{"status": "refused"}]
    app.engine.failure = None
    now[0] = busy["due"]
    await drain(app)
    assert app.suggestions.view(KEY).state == "ready"
    assert app.suggestions.record(KEY)["attempt"] == busy["id"]


async def test_route_contract_failure_is_visible_without_dispatch(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app))
    await incoming_only(app)
    app.engine.contract = True
    await drain(app)
    view = app.suggestions.view(KEY)
    assert (view.state, view.reason) == ("failed", "provider")
    assert len(app.store.rows("SELECT * FROM reservations")) == 2
    assert not jobs(app, state="queued")


def test_rewriting_precedes_suggestions_which_precede_idle_memory(tmp_path):
    app, _ = rig(tmp_path)
    with app.store.transaction() as db:
        for identity, role, klass in [
            ("memory", "chat_memory", "memory"),
            ("suggestion", "response_suggestions", "suggestions"),
            ("older", "incoming_rewrite", "older"),
            ("new", "incoming_rewrite", "new"),
        ]:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,'1','queued',0,?,NULL)",
                (identity, KEY, role, json.dumps({"class": klass})),
            )
    order = []
    for _ in range(4):
        job = app.rewriting.next_job()
        if not job:
            break
        order.append(job["id"])
        with app.store.transaction() as db:
            db.execute("UPDATE jobs SET state='done' WHERE id=?", (job["id"],))
    assert order[:3] == ["new", "older", "suggestion"]
    assert RANK["older"] < RANK["suggestions"] < RANK["memory"]
    # A queued suggestion keeps idle memory waiting, across accounts too.
    with app.store.transaction() as db:
        db.execute("UPDATE jobs SET state='queued' WHERE id='suggestion'")
    assert not app.memory.local_idle()
    # With a limit of two or more, suggestions never take the last free slot.
    capacity = Capacity(limit=lambda: 2)
    assert capacity.admits("suggestions") and not capacity.admits(
        "suggestions", running=1
    )
    assert capacity.admits("new", running=1)
    assert Capacity(limit=lambda: 1).admits("suggestions")


async def test_account_ai_off_holds_and_suggestions_off_cancels(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app))
    await incoming_only(app)
    job = jobs(app, state="queued")[0]

    def off():
        raise ProcessingPaused("AI processing is Off for this account")

    app.admit_ai = off
    assert app.rewriting.claim(job)
    await app.suggestions.run(job)
    assert jobs(app, state="queued")[0]["id"] == job["id"]
    assert app.suggestions.view(KEY).reason == "paused"
    app.admit_ai = lambda: None
    app.suggestions.enabled = lambda: False
    assert app.suggestions.view(KEY).reason == "off"
    await drain(app)
    assert app.engine.count() == 0 and not app.store.rows(
        "SELECT * FROM reservations WHERE id=?", (job["id"],)
    )
    assert jobs(app)[0]["state"] == "cancelled"
    # On reuses nothing absent and does not backfill unchanged sources.
    app.suggestions.enabled = lambda: True
    assert app.suggestions.view(KEY).reason == "cancelled"
    await drain(app)
    assert app.engine.count() == 0 and len(jobs(app)) == 1
    app.sync.ingest(message(app, 2, text="A changed block"))
    await drain(app)
    assert app.engine.count() == 1 and app.suggestions.view(KEY).state == "ready"


async def test_switching_off_cancels_queued_work_but_started_work_finishes(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app))
    await incoming_only(app)
    app.engine.gate = asyncio.Event()
    task = asyncio.create_task(drain(app))
    await asyncio.wait_for(app.engine.started.wait(), 5)
    app.suggestions.enabled = lambda: False
    app.suggestions.cancel_queued()
    app.engine.gate.set()
    await task
    assert app.suggestions.record(KEY)["state"] == "ready"
    assert app.suggestions.view(KEY).reason == "off"
    app.suggestions.enabled = lambda: True
    assert app.suggestions.view(KEY).state == "ready"
    # Queued, undispatched work is cancelled by Off.
    app.engine.gate = None
    app.sync.ingest(message(app, 2, text="Another message"))
    await incoming_only(app)
    assert jobs(app, state="queued")
    app.suggestions.cancel_queued()
    assert not jobs(app, state="queued")
    assert app.suggestions.record(KEY)["state"] == "cancelled"


@pytest.mark.parametrize(
    "change",
    [
        "new_incoming",
        "external_outgoing",
        "app_outgoing",
        "context_edit",
        "target_delete",
        "transcript",
        "expiry",
        "authorization",
    ],
)
async def test_late_output_after_invalidation_keeps_cost_and_never_publishes(
    tmp_path, change
):
    now = [2_000_000_000.0]
    app, registry = rig(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app, 1, outgoing=True, text="Owner context"))
    app.sync.ingest(message(app, 2, text="Please listen."))
    await incoming_only(app)
    app.engine.gate = asyncio.Event()
    job = jobs(app, state="queued")[0]
    assert app.rewriting.claim(job)
    task = asyncio.create_task(app.rewriting.run(job))
    await asyncio.wait_for(app.engine.started.wait(), 5)
    if change == "new_incoming":
        app.sync.ingest(message(app, 3, text="One more thing"))
    elif change == "external_outgoing":
        app.sync.ingest(message(app, 3, outgoing=True, text="Answered elsewhere"))
    elif change == "app_outgoing":
        app.store.apply_message(message(app, 3, outgoing=True, text="Sent here"))
    elif change == "context_edit":
        app.sync.ingest(message(app, 1, outgoing=True, revision=2, text="Edited"))
    elif change == "target_delete":
        app.sync.ingest(message(app, 2, revision=2, deleted=True))
    elif change == "transcript":
        with app.store.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
                (
                    f"{KEY}/1",
                    1,
                    json.dumps({"transcript": {"text": "Changed", "language": "en"}}),
                ),
            )
    elif change == "expiry":
        now[0] += 12 * 3600
    elif change == "authorization":
        app.authorized = lambda: False
    app.engine.gate.set()
    await task
    record = app.suggestions.record(KEY)
    assert record["attempt"] == job["id"] and "packet" not in record
    assert record["state"] == ("failed" if change == "authorization" else "cancelled")
    assert app.suggestions.view(KEY).state != "ready"
    # The provider's receipted cost is preserved although publication failed.
    assert app.store.rows(
        "SELECT status,actual FROM reservations WHERE id=?", (job["id"],)
    ) == [{"status": "receipted", "actual": 1000}]
    assert app.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],)) == [
        {"state": "superseded"}
    ]
    # Ordinary processing of a genuinely changed source may follow; the late
    # attempt's options are never what becomes visible.
    app.authorized = lambda: True
    await drain(app)
    view = app.suggestions.view(KEY)
    assert view.attempt != job["id"]
    if change in ("new_incoming", "context_edit"):
        assert view.state == "ready"
    elif change == "authorization":
        # Processed but unpublished output is an interrupted outcome.
        assert (view.state, view.reason) == ("failed", "interrupted")
    else:
        assert view.state == "none"


async def test_schedule_failure_never_undoes_the_incoming_result(tmp_path, caplog):
    app, _ = rig(tmp_path)

    def broken(*args, **kwargs):
        raise KeyError("authored capture failure")

    app.suggestions.capture = broken
    app.sync.ingest(message(app))
    with caplog.at_level("WARNING", logger="enloq.suggestions"):
        await drain(app)
    block = app.conversation(KEY)["blocks"][0]
    assert block["state"] == "ready" and block["rendered"]
    assert app.emotion.view(KEY).code == "sad"
    assert not jobs(app) and not app.store.rows("SELECT * FROM suggestion_state")
    assert [r.msg for r in caplog.records] == ["suggestion_schedule_unavailable"]


async def test_registry_backed_switches_never_wait_on_the_store_lock(tmp_path, caplog):
    """The account manager's switch and authorization read the shared
    registry. Reading either inside the store's write transaction once waited
    out the 10-second lock timeout and silently skipped scheduling."""
    import time

    from enloq.application.accounts import AccountManager
    from enloq.config import Settings
    from enloq.domain.models import Chat

    manager = AccountManager(Settings(data=tmp_path))
    manager.set_processing_threshold(0)
    app = manager.get(1)
    app.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored", kind="private")
    )
    app.engine = Engine()
    app.decider = Decider(app.engine)
    app.sync.ingest(message(app))
    started = time.monotonic()
    with caplog.at_level("WARNING", logger="enloq"):
        await drain(app, limit=200)
    assert time.monotonic() - started < 5
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    assert app.suggestions.view(KEY).state == "ready"
    manager.set_suggestions(False)
    assert app.suggestions.view(KEY).reason == "off"
    manager.set_suggestions(True)
    assert app.suggestions.view(KEY).state == "ready"
    await manager.close()
