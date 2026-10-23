"""ADR 21 and ADR 55: selective publication and honest retained-source
provenance, now for what the person cares about."""

import json

import pytest

from backend.tests.test_memory import KEY, conversation, fold, update
from backend.tests.test_rebuild import fixture, message

# Memory as before ADR 56's Off default: these chats learn without switching
# on; test_memory_default covers the default as shipped.
pytestmark = pytest.mark.usefixtures("remember_on")


def retained(app, identity, text, citations, source="model"):
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO memory_entries (id,chat_key,text,citations,source,outdated,created) VALUES (?,?,?,?,?,NULL,?)",
            (identity, KEY, text, json.dumps(citations), source, app.store.clock()),
        )


async def run_update(app, output, ids=(1, 2)):
    async def generate(role, packet):
        app.engine.calls.append((role, packet))
        return update(output)

    app.engine.generate = generate
    app.memory.record(KEY, [f"{KEY}/{i}" for i in ids])
    assert app.memory.schedule(KEY)
    return await fold(app)


async def test_combines_messages_and_supersedes_duplicates_without_owner_changes(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    retained(app, "old", "Agreed to meet on 4 October.", [1])
    retained(app, "duplicate", "Meeting date is 4 October.", [2])
    retained(app, "mine", "Owner note.", [], "owner")
    await run_update(
        app,
        {
            "add": [
                {
                    "text": "Meeting agreed for 5 October, replacing 4 October.",
                    "citations": [1, 2],
                }
            ],
            "remove": ["old", "duplicate"],
            "outdated": [],
        },
    )
    entries = app.memory.entries(KEY)
    assert len(entries) == 2 and any(e["id"] == "mine" for e in entries)
    assert next(e for e in entries if e["source"] == "model")["citations"] == [1, 2]
    assert not app.memory.state(KEY)["error"]


async def test_expired_sources_can_be_conservatively_merged_with_known_provenance(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    retained(app, "legacy", "Prefers morning meetings.", [80])
    await run_update(
        app,
        {
            "add": [
                {
                    "text": "Meetings: prefers mornings.",
                    "citations": [80],
                    "based_on": ["legacy"],
                }
            ],
            "remove": ["legacy"],
            "outdated": [],
        },
    )
    packet = app.engine.calls[0][1]
    assert packet["person"]["memory"][0]["provenance"] == [
        {"id": 80, "availability": "not_cached"}
    ]
    assert 80 not in {m["id"] for m in packet["messages"]}
    assert app.memory.entries(KEY)[0]["citations"] == [80]


@pytest.mark.parametrize(
    "based_on,citations",
    [([], [80]), (["other-chat"], [80]), (["mine"], [80]), (["legacy"], [99])],
)
async def test_no_invented_or_owner_provenance(tmp_path, based_on, citations):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    retained(app, "legacy", "Morning meetings.", [80])
    retained(app, "mine", "Owner note.", [80], "owner")
    job = await run_update(
        app,
        {
            "add": [
                {"text": "Unsupported.", "citations": citations, "based_on": based_on}
            ],
            "remove": ["legacy"],
            "outdated": [],
        },
    )
    assert {e["id"] for e in app.memory.entries(KEY)} == {"legacy", "mine"}
    assert app.memory.state(KEY)["error"]
    assert (
        app.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))[0]["state"]
        == "failed"
    )


@pytest.mark.parametrize("change", ["edit", "delete", "transcript", "logout"])
async def test_changed_new_input_cannot_publish_even_without_a_previous_citation(
    tmp_path, change
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)

    async def generate(role, packet):
        if change == "edit":
            app.sync.ingest(message(app, 1, revision=2, text="Changed."))
        elif change == "delete":
            app.sync.deleted("user:42", [1])
        elif change == "logout":
            app.authorized = lambda: False
        else:
            with app.store.transaction() as db:
                db.execute(
                    "INSERT INTO derivatives VALUES (?,1,?)",
                    (
                        f"{KEY}/1",
                        json.dumps({"transcript": {"text": "Changed recognition."}}),
                    ),
                )
        return update({"add": [{"text": "Stale.", "citations": [1]}]})

    app.engine.generate = generate
    app.memory.record(KEY, [f"{KEY}/1"])
    app.memory.schedule(KEY)
    job = await fold(app)
    assert not app.memory.entries(KEY)
    assert (
        app.store.rows("SELECT state FROM jobs WHERE id=?", (job["id"],))[0]["state"]
        == "superseded"
    )


async def test_overflow_never_discards_an_old_important_fact(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    retained(app, "important", "An important old fact.", [80])
    await run_update(
        app,
        {
            "add": [{"text": str(i) + "x" * 299, "citations": [1]} for i in range(10)],
            "remove": [],
            "outdated": [],
        },
    )
    assert [e["id"] for e in app.memory.entries(KEY)] == ["important"]
    assert app.memory.state(KEY)["coverage"] == 0


async def test_empty_result_is_a_valid_consumed_batch(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    await run_update(app, {"add": [], "remove": [], "outdated": []})
    assert not app.memory.entries(KEY) and not app.memory.candidates(KEY)
    assert not app.memory.schedule(KEY)


def test_provenance_read_reports_current_cache_availability_without_work(tmp_path):
    from enloq.api.memory import MemoryView

    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    retained(app, "mine", "An edited retained note.", [1, 2, 80], "owner")
    app.sync.deleted("user:42", [2])
    before = app.store.rows("SELECT * FROM jobs")
    value = MemoryView.model_validate(app.memory.view(KEY))
    assert [s.availability for s in value.entries[0].sources] == [
        "cached",
        "deleted",
        "not_cached",
    ]
    assert app.store.rows("SELECT * FROM jobs") == before and not app.engine.calls
