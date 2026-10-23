"""E7 and ADR 55: cited Person and Account Memory folded from messages
already sent to a model, or started once from the cache."""

import asyncio

import pytest
from enloq.domain.models import Conflict, ProcessingPaused, Refused
from enloq.inference.base import EngineResponse
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import RETENTION, WINDOW

from backend.tests.test_rebuild import fixture, message

# Memory as before ADR 56's Off default: these chats learn without switching
# on; test_memory_default covers the default as shipped.
pytestmark = pytest.mark.usefixtures("remember_on")

KEY = "1/user:42"
DAY = 86400
EMPTY = {"add": [], "remove": [], "outdated": []}


def update(person=None, account=None):
    """One memory update: the person's changes and the account's (ADR 55)."""
    return {
        "person": {**EMPTY, **(person or {})},
        "account": {**EMPTY, **(account or {})},
    }


def no_start(a):
    """Authored messages here count only once sent to a model: the store's
    cache start (ADR 55 Q20) predates them."""
    with a.store.transaction() as db:
        db.execute("UPDATE metadata SET value='0' WHERE key='memory_start'")


def conversation(a, now, count=4, age=10, start=False):
    """Alternating incoming/outgoing messages ``age`` days old, then a recent
    own message so the chat stays recent."""
    if not start:
        no_start(a)
    for identity in range(1, count + 1):
        a.sync.ingest(
            message(
                a,
                identity,
                date=now[0] - age * DAY + identity,
                text=f"Fact number {identity}.",
                outgoing=identity % 2 == 0,
            ),
            target=False,
        )
    a.sync.ingest(
        message(a, 100, date=now[0] - 1800, text="Latest own message.", outgoing=True)
    )


async def fold(a):
    job = a.store.rows("SELECT * FROM jobs WHERE role='chat_memory' AND state='queued'")
    assert job, "no fold queued"
    assert a.rewriting.claim(job[0])
    await a.rewriting.run(job[0])
    return job[0]


async def test_folds_take_only_analysed_departed_messages_without_history(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    conversation(a, now)
    reads = []
    a.transport.history = lambda *args: reads.append(args)
    a.transport.exact = lambda *args: reads.append(args)
    assert not a.memory.schedule(KEY)  # Nothing was sent to the model yet.
    a.memory.record(KEY, [f"{KEY}/1", f"{KEY}/2"])
    assert a.memory.schedule(KEY)
    await fold(a)
    folded = [m["id"] for m in a.engine.calls[-1][1]["messages"]]
    assert folded == [1, 2] and not reads
    view = a.memory.view(KEY)
    # The person's message notes what they care about; the owner's own, what
    # the owner does (ADR 55).
    assert [e["citations"] for e in view["entries"]] == [[1]]
    assert [e["citations"] for e in a.memory.account_view()["entries"]] == [[2]]
    assert a.memory.entries("account")[0]["citations"] == [[KEY, 2]]
    assert view["coverage"] == 2
    # Consumed versions do not fold again; coverage is no longer the cursor.
    a.memory.record(KEY, [f"{KEY}/1", f"{KEY}/3"])
    assert [r["message_id"] for r in a.memory.candidates(KEY)] == [3]


async def test_recent_messages_learn_after_30_quiet_minutes(tmp_path):
    # ADR 55 Q19 B: the latest 20 count too, once sent to a model.
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    no_start(a)
    for identity in range(1, 5):
        a.sync.ingest(
            message(a, identity, text=f"Recent {identity}.", outgoing=identity % 2 == 0)
        )
    assert not a.memory.candidates(KEY)  # Merely cached is not disclosed.
    a.memory.record(KEY, [f"{KEY}/{i}" for i in range(1, 5)])
    assert len(a.memory.candidates(KEY)) == 4
    assert not a.memory.due(KEY)
    now[0] += 1800
    assert a.memory.due(KEY)


async def test_folds_stop_while_off_or_out_of_budget(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    conversation(a, now)
    a.memory.record(KEY, [f"{KEY}/1"])
    a.memory.schedule(KEY)

    def paused():
        raise ProcessingPaused("Off")

    a.admit_ai = paused
    await fold(a)
    assert not a.engine.calls and not a.memory.view(KEY)["entries"]
    a.admit_ai = lambda: None
    now[0] += 60
    AccountRegistry(tmp_path).set_budget(0)

    class Muse:
        name, model, provider = "muse", "exact-test-route", "meta"

        async def generate(self, role, packet):
            raise AssertionError("no call without budget")

    a.engine = Muse()
    a.memory.schedule(KEY)
    await fold(a)
    assert not a.memory.view(KEY)["entries"] and not a.memory.view(KEY)["error"]


@pytest.mark.parametrize(
    "output",
    [
        update({"add": [{"text": "Invented.", "citations": [77]}]}),
        update({"add": [], "remove": ["unknown"], "outdated": []}),
        update(account={"add": [], "remove": ["unknown"], "outdated": []}),
        {"wrong": "format"},
    ],
)
async def test_malformed_or_invalid_updates_are_rejected_whole(tmp_path, output):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    conversation(a, now)
    a.memory.add(KEY, 0, "An owner fact.")

    async def generate(role, packet):
        return EngineResponse(output)

    a.engine.generate = generate
    a.memory.record(KEY, [f"{KEY}/1"])
    a.memory.schedule(KEY)
    await fold(a)
    view = a.memory.view(KEY)
    assert [e["text"] for e in view["entries"]] == ["An owner fact."]
    assert view["error"] and view["coverage"] == 0
    assert not a.memory.schedule(KEY)  # No automatic resend after a failure.
    a.engine.generate = lambda role, packet: asyncio.sleep(
        0,
        result=update({"add": [{"text": "Fact number 1.", "citations": [1]}]}),
    )
    a.memory.retry(KEY)
    await fold(a)
    assert len(a.memory.view(KEY)["entries"]) == 2


async def test_an_owner_edit_during_a_fold_discards_it(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    conversation(a, now)
    started, release = asyncio.Event(), asyncio.Event()
    original = a.engine.generate

    async def held(role, packet):
        started.set()
        await release.wait()
        return await original(role, packet)

    a.engine.generate = held
    a.memory.record(KEY, [f"{KEY}/1"])
    a.memory.schedule(KEY)
    task = asyncio.create_task(fold(a))
    await started.wait()
    view = a.memory.add(KEY, a.memory.view(KEY)["revision"], "Written meanwhile.")
    with pytest.raises(Conflict):
        a.memory.add(KEY, view["revision"] - 1, "Stale edit.")
    release.set()
    await task
    assert [e["text"] for e in a.memory.view(KEY)["entries"]] == ["Written meanwhile."]
    assert a.memory.candidates(KEY)  # Still waiting for the next fold.


async def test_deleting_a_cited_message_removes_its_entries_without_a_call(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    conversation(a, now)
    a.memory.record(KEY, [f"{KEY}/1", f"{KEY}/3"])
    a.memory.schedule(KEY)
    await fold(a)
    calls = len(a.engine.calls)
    a.sync.deleted("user:42", [1])
    assert [e["citations"] for e in a.memory.view(KEY)["entries"]] == [[3]]
    edited = a.store.message(KEY, 3).model_copy(
        update={"revision": 2, "text": "Changed."}
    )
    a.sync.ingest(edited)
    assert not a.memory.view(KEY)["entries"] and len(a.engine.calls) == calls


async def test_memory_reaches_only_newer_incoming_targets_never_cut_for_it(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    conversation(a, now)
    a.memory.record(KEY, [f"{KEY}/1"])
    a.memory.schedule(KEY)
    await fold(a)
    a.sync.ingest(message(a, 200, text="A new question."))
    now[0] += 30
    await a.process_due()
    packet = a.engine.calls[-1][1]
    await a.process_due()  # The Emotion Classification that follows it.
    assert packet["memory"]["note"].startswith("Unverified")
    assert packet["memory"]["entries"][0]["citations"] == [1]
    draft = a.store.mutate_draft(KEY, 0, lambda d: d.update(text="My own words."))
    await a.rewriting.outgoing(KEY, draft["revision"])
    assert "memory" not in a.engine.calls[-1][1]
    older = a.rewriting.block(f"{KEY}/3")
    assert older["state"] == "original"
    assert a.rewriting.process_older(KEY) == 2
    await a.process_due()
    assert "memory" not in a.engine.calls[-1][1]  # Older than seven days.
    # A near-limit request drops memory rather than cutting the target.
    for index in range(38):
        revision = a.memory.view(KEY)["revision"]
        a.memory.add(KEY, revision, f"Owner reference fact {index:02d} " + "r" * 50)
    for identity in range(300, 320):
        a.sync.ingest(message(a, identity, outgoing=True, text="c" * 250))
    a.sync.ingest(message(a, 320, text="x" * 23900))
    now[0] += 30
    await a.process_due()
    packet = a.engine.calls[-1][1]
    assert "memory" not in packet and len(packet["messages"][0]["text"]) == 23900


async def test_model_changes_never_touch_owner_entries_and_overflow_keeps_previous(
    tmp_path,
):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    conversation(a, now, count=44)
    owner = a.memory.add(KEY, 0, "Owner fact.")["entries"][0]["id"]

    async def flag(role, packet):
        return update(
            {
                "add": [
                    {"text": f"Fact {m['id']}.", "citations": [m["id"]]}
                    for m in packet["messages"][:10]
                ],
                "remove": [],
                "outdated": [{"id": owner, "citations": [packet["messages"][0]["id"]]}],
            }
        )

    a.engine.generate = flag
    for start in range(1, 41, 10):
        a.memory.record(KEY, [f"{KEY}/{i}" for i in range(start, start + 10)])
        a.memory.schedule(KEY)
        await fold(a)
    entries = a.memory.view(KEY)["entries"]
    assert len(entries) == 31  # Fourth batch rejected whole, no age eviction.
    assert a.memory.view(KEY)["error"]
    assert owner in [e["id"] for e in entries]
    kept = next(e for e in entries if e["id"] == owner)
    assert kept["text"] == "Owner fact." and kept["outdated"]
    assert all(
        set(e)
        == {
            "id",
            "chat_key",
            "text",
            "citations",
            "source",
            "outdated",
            "created",
            "guidance",
            "defaults",
            "sources",
        }
        for e in entries
    )

    async def remove_owner(role, packet):
        return update({"add": [], "remove": [owner], "outdated": []})

    a.engine.generate = remove_owner
    a.memory.record(KEY, [f"{KEY}/41"])
    a.memory.retry(KEY)
    await fold(a)
    view = a.memory.view(KEY)
    assert view["error"] and owner in [e["id"] for e in view["entries"]]


async def test_memory_outlives_content_expiry_and_goes_with_local_removal(tmp_path):
    now = [2_000_000_000.0]
    a = fixture(tmp_path, clock=lambda: now[0])
    conversation(a, now)
    a.memory.record(KEY, [f"{KEY}/1"])
    a.memory.schedule(KEY)
    await fold(a)
    now[0] += RETENTION + WINDOW
    a.store.expire()
    assert not a.store.message(KEY, 1)
    assert a.memory.view(KEY)["entries"][0]["citations"] == [1]
    with pytest.raises(Refused):
        a.memory.add(KEY, 0, "")


def test_memory_api_is_hidden_on_logout_and_removed_with_local_data(tmp_path):
    from enloq.api.server import create_app
    from enloq.config import Settings
    from fastapi.testclient import TestClient

    from backend.tests.auth_helpers import trust_browser

    app = create_app(Settings(data=tmp_path / "app"))
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        base = "/api/accounts/1/chats/user:101/memory"
        view = client.post(
            f"{base}/entries",
            json={"revision": 0, "text": "Prefers mornings."},
            headers=origin,
        ).json()
        assert [e["text"] for e in view["entries"]] == ["Prefers mornings."]
        stale = client.post(
            f"{base}/entries", json={"revision": 0, "text": "Stale."}, headers=origin
        )
        assert stale.status_code == 409
        assert (
            client.post(
                "/api/settings/memory", json={"enabled": False}, headers=origin
            ).json()["memory"]
            is False
        )
        store = app.state.accounts.get(1).store
        client.post("/api/accounts/1/logout", json={}, headers=origin)
        assert client.get(base).status_code == 400
        client.post(
            "/api/accounts/1/remove-local-data", json={"confirm": True}, headers=origin
        )
        assert not store.path.exists()
