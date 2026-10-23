"""E4: change notifications, Processing Stages, run progress, Stop and budget."""

import asyncio
import json

import pytest
from enloq.api.server import create_app
from enloq.config import Settings
from enloq.domain.models import MediaInfo, ProcessingPaused, Refused
from enloq.inference.base import EngineResponse
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.accounts import AccountRegistry
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture, image_bytes, message


def stage(app, index=0):
    return app.conversation("1/user:42")["blocks"][index]["stage"]


async def test_every_processing_stage_appears_in_its_state(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    seen = []

    class Speech:
        async def transcribe(self, path):
            seen.append(stage(a))
            return {"text": "Authored transcript", "language": "en"}

    original = a.engine.generate

    async def generate(role, packet):
        seen.append(stage(a))
        return await original(role, packet)

    a.media.transcriber, a.engine.generate = Speech(), generate
    a.transport.media["user:42", 1] = b"OggS" + bytes(32)
    a.transport.media["user:42", 2] = image_bytes()
    a.sync.ingest(message(a, 1, text="", media="audio"))
    a.sync.ingest(message(a, 2, text="", media="image"))
    assert stage(a) == "quiet"
    original_image = a.media.image

    async def image(source):
        seen.append(stage(a))
        return await original_image(source)

    a.media.image = image
    now[0] += 30
    assert stage(a) == "queued"
    a.admit_ai = lambda: (_ for _ in ()).throw(ProcessingPaused("Off"))
    assert stage(a) == "paused"
    a.admit_ai = lambda: None
    await a.process_due()
    assert seen == ["transcribing", "reading_images", "rewriting"]
    assert stage(a) == "done"
    await a.process_due()  # The Emotion Classification that follows it.
    a.sync.ingest(message(a, 3, outgoing=True))
    a.sync.ingest(
        message(a, 4, text="", media="sticker", media_info=MediaInfo(sticker="static"))
    )
    assert stage(a, 1) == "nothing"
    a.sync.ingest(message(a, 5, outgoing=True))
    a.sync.ingest(message(a, 6, text="", media="image"))  # No bytes available.
    now[0] += 30
    await a.process_due()
    assert stage(a, 2) == "failed"
    AccountRegistry(tmp_path).set_budget(0)

    class Muse:
        name, model, provider = "muse", "exact-test-route", "meta"

        async def generate(self, role, packet):
            raise AssertionError("no call after the budget is reached")

    a.engine = Muse()
    a.sync.ingest(message(a, 7, outgoing=True))
    a.sync.ingest(message(a, 8, text="Budget-limited request."))
    now[0] += 30
    await a.process_due()
    assert stage(a, 3) == "budget"


async def test_run_progress_and_stop_cancels_only_undispatched_work(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    for identity in (1, 2, 3):
        a.sync.ingest(
            message(a, identity * 2 - 1, text=f"Separate request {identity}.")
        )
        a.sync.ingest(message(a, identity * 2, outgoing=True))
        now[0] += 200
    assert a.conversation("1/user:42")["progress"] == {
        "done": 0,
        "total": 3,
        "explicit": False,
    }
    await a.process_due()
    assert a.conversation("1/user:42")["progress"] == {
        "done": 1,
        "total": 3,
        "explicit": False,
    }
    started, release = asyncio.Event(), asyncio.Event()
    original = a.engine.generate

    async def held(role, packet):
        started.set()
        await release.wait()
        return await original(role, packet)

    a.engine.generate = held
    running = asyncio.create_task(a.process_due())
    await started.wait()
    assert a.rewriting.stop("1/user:42") == 1
    release.set()
    await running
    states = [b["state"] for b in a.conversation("1/user:42")["blocks"]]
    assert states == ["ready", "ready", "original"]
    assert a.conversation("1/user:42")["progress"] is None
    assert a.store.recent_chats()[0]["processing"] == 0
    await a.process_due()
    assert len(a.engine.calls) == 2  # Stopped work never resumes by itself.


def test_change_scopes_cover_every_write_path(tmp_path):
    a = fixture(tmp_path)
    registry = AccountRegistry(tmp_path)
    _, start = a.store.changes()
    a.sync.ingest(message(a))
    scopes, seq = a.store.changes(start)
    assert scopes == ["1/user:42"] and seq > start
    a.store.mutate_draft("1/user:42", 0, lambda d: d.update(text="Draft"))
    assert a.store.changes(seq)[0] == ["1/user:42"]
    registry.register(1, "synthetic.session")
    _, latest = registry.changes()
    registry.set_budget(500_000_000)
    registry.set_processing(1, False)
    registry.set_reading_view("originals")
    assert set(registry.changes(latest)[0]) == {
        "daily_budget",
        "accounts",
        "reading_view",
    }


async def test_event_stream_carries_scopes_not_content(tmp_path):
    from types import SimpleNamespace

    from enloq.api.accounts import account_routes

    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        browser = client.get("/api/auth/browsers").json()[0]["id"]
        router = account_routes(
            app.state.accounts, app.state.logins, app.state.browser_auth
        )
        endpoint = next(r.endpoint for r in router.routes if r.path == "/api/events")
        response = await endpoint(
            SimpleNamespace(state=SimpleNamespace(local_session=browser))
        )
        events = response.body_iterator
        assert "refresh" in await anext(events)
        conversation = app.state.accounts.get(1)
        conversation.sync.ingest(
            message(conversation, 9, text="Private words").model_copy(
                update={"peer": "user:101"}
            )
        )
        change = await asyncio.wait_for(anext(events), 5)
        assert change.startswith("event: change")
        data = json.loads(change.split("data: ", 1)[1])
        assert data["chats"] == {"1": ["1/user:101"]}
        assert "Private words" not in change
        app.state.accounts.registry.set_reading_view("originals")
        shared = json.loads(
            (await asyncio.wait_for(anext(events), 5)).split("data: ")[1]
        )
        assert shared["shared"] == ["reading_view"]
        await events.aclose()


def test_budget_edits_apply_to_the_next_call_log_and_validate(tmp_path):
    a = fixture(tmp_path)
    registry = AccountRegistry(tmp_path)
    work = a.rewriting.work

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
        with a.store.transaction() as db:
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", tuple(value.values())
            )
        return value

    registry.set_budget(0)
    with pytest.raises(Refused, match="budget"):
        work.reserve(job("zero"), "muse")
    registry.set_budget(1_000_000_000)
    work.reserve(job("next"), "muse")
    assert [(c["old"], c["new"]) for c in registry.budget()["changes"]] == [
        (0, 1_000_000_000),
        (1_000_000_000, 0),
    ]
    with pytest.raises(Refused):
        registry.set_budget(10_000_000_001)
    app = create_app(Settings(data=tmp_path / "app"))
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        origin = {"Origin": "http://127.0.0.1:8765"}
        assert client.get("/api/settings").json()["budget"]["limit"] == 1_000_000_000
        for bad in ("10.01", "-1", "0.001", "abc"):
            response = client.post(
                "/api/settings/budget", json={"limit_usd": bad}, headers=origin
            )
            assert response.status_code == 422, bad
        saved = client.post(
            "/api/settings/budget", json={"limit_usd": "2.50"}, headers=origin
        ).json()
        assert saved["budget"]["limit"] == 2_500_000_000


def test_counted_spending_is_receipts_plus_flat_unknowns(tmp_path):
    fixture(tmp_path)
    registry = AccountRegistry(tmp_path)
    day = registry.budget()["day"]
    with registry.transaction() as db:
        for identity, actual, status in (
            ("a", 1_234_000, "receipted"),
            ("b", 2_000_000, "receipted"),
            ("c", None, "unknown"),
            ("d", None, "unknown"),
            ("e", None, "refused"),
            ("f", 99, "receipted"),
        ):
            db.execute(
                "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created) VALUES (1,?,?,?,?,?,?,?,?,?,0)",
                (identity, "s", "r", identity, "day:" + day, "muse", 0, actual, status),
            )
        db.execute(
            "INSERT INTO model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created) VALUES (1,'old','s','r','old','day:2000-01-01','muse',105676800,NULL,'unknown',0)"
        )
    state = registry.budget()
    assert state["spent"] == 1_234_000 + 2_000_000 + 99 + 2 * FLAT_UNKNOWN
    assert state["calls"] == 6 and state["unknown"] == 2


async def test_running_calls_may_finish_but_overshoot_stays_bounded(tmp_path):
    a = fixture(tmp_path)
    registry = AccountRegistry(tmp_path)
    registry.set_budget(15_000_000)  # $0.015: one flat estimate fits under it.
    release = asyncio.Event()
    calls = []

    class Muse:
        name, model, provider = "muse", "exact-test-route", "meta"

        async def generate(self, role, packet):
            calls.append(role)
            await release.wait()
            return EngineResponse({"text": "Done."}, actual_nanos=12_000_000)

    a.engine = Muse()
    for identity in (1, 3, 5):
        a.sync.ingest(message(a, identity, text="Request."))
        a.sync.ingest(message(a, identity + 1, outgoing=True))
    jobs = a.store.rows("SELECT * FROM jobs WHERE state='queued' ORDER BY scope")
    assert len(jobs) == 3
    tasks = [asyncio.create_task(a.rewriting.incoming(job)) for job in jobs]
    await asyncio.sleep(0.05)
    assert len(calls) == 2  # The third waits: $0.02 counted >= $0.015.
    release.set()
    await asyncio.gather(*tasks)
    state = registry.budget()
    assert state["spent"] == 24_000_000
    assert state["spent"] - state["limit"] <= 2 * 12_000_000
