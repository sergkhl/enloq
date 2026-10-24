"""H3: authored replay, dependency invalidation and retained-result recovery."""

import asyncio
import json
from types import SimpleNamespace

import pytest
from enloq.application.blocks import (
    enqueue,
    reconcile_unread,
    regroup_cached,
)
from enloq.application.runtime import sync_cycle
from enloq.domain.models import GapTooLong, MediaInfo, Refused
from enloq.inference.base import EngineResponse, ProviderRefused

from backend.tests.test_rebuild import fixture, message

KEY = "1/user:42"
DAY = 86400


def jobs(app):
    return app.store.rows(
        "SELECT * FROM jobs WHERE role='incoming_rewrite' ORDER BY rowid"
    )


def block(app, identity):
    return next(b for b in app.conversation(KEY)["blocks"] if b["id"] == identity)


async def complete(app, now):
    now[0] += 30
    pending = app.store.rows(
        "SELECT id FROM jobs WHERE role='incoming_rewrite' AND state='queued' AND due<=?",
        (now[0],),
    )
    # Arrival screens are independently queued before the block work.
    for _ in range(len(pending) + 20):
        await app.process_due()
        if not app.store.rows(
            "SELECT id FROM jobs WHERE role IN ('incoming_rewrite','hostility_screen') "
            "AND state='queued' AND due<=?",
            (now[0],),
        ):
            break
    assert all(b["state"] == "ready" for b in app.conversation(KEY)["blocks"])
    await classified(app)


async def classified(app):
    """Run the Emotion Classification that follows a published rewrite."""
    while app.store.rows(
        "SELECT id FROM jobs WHERE role='emotion_classification' AND state='queued'"
    ):
        await app.process_due()


async def replay(app, tmp_path, now, sources, mode, *, target=True):
    """Use the ordinary sync cycle; the fake recovery never contacts Telegram."""
    if mode == "restart":
        app = fixture(tmp_path, clock=lambda: now[0])
        app.store.recover()
        regroup_cached(app.store)
        reconcile_unread(app.store)
    app.engine.calls.clear()
    transport = app.transport
    transport.messages = []
    transport.identity = SimpleNamespace(id=1)
    transport.requires_login = False
    calls = []
    gap_pending = mode == "gap"

    async def discover():
        calls.append("discover")

    async def catch_up():
        nonlocal gap_pending
        calls.append("catchup")
        if gap_pending:
            gap_pending = False
            raise GapTooLong("Authored gap beyond the update buffer")
        for source in sources:
            app.sync.ingest(source, target=target, cursor=("pts", "authored-replay"))

    async def restart_after_gap():
        calls.append("gap_restart")
        with app.store.transaction() as db:
            db.execute("DELETE FROM cursors WHERE key LIKE 'recent:%'")

    transport.discover = discover
    transport.catch_up = catch_up
    transport.restart_after_gap = restart_after_gap
    if mode == "gap":
        assert not await sync_cycle(app)
        assert calls == ["discover", "catchup", "gap_restart"]
    assert await sync_cycle(app)
    return app


@pytest.mark.parametrize("mode", ["restart", "reconnect", "gap"])
async def test_completed_sources_replayed_unchanged_create_no_job_call_or_alert(
    tmp_path, mode
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    sources = [
        message(app, 1, text="Authored first request."),
        message(app, 2, outgoing=True, text="Authored owner reply."),
        message(app, 3, text="Authored second request."),
    ]
    for source in sources:
        app.sync.ingest(source, live=True)
    await complete(app, now)
    app.notifications.advance()
    for notice in app.notifications.rows():
        assert app.notifications.finish(notice)
    app.unseen.advance()
    before_jobs = jobs(app)
    before_notices = app.notifications.rows()
    before_views = app.store.rows("SELECT * FROM source_views ORDER BY source")
    before_results = [(b["id"], b["rendered"]) for b in app.conversation(KEY)["blocks"]]

    app = await replay(app, tmp_path, now, sources, mode)
    for _ in range(2):
        now[0] += 60
        app.conversation(KEY)
        await app.process_due()
        app.notifications.advance()
        app.unseen.advance()
    assert jobs(app) == before_jobs
    assert not app.engine.calls
    assert app.notifications.rows() == before_notices
    assert app.store.rows("SELECT * FROM source_views ORDER BY source") == before_views
    assert [
        (b["id"], b["rendered"]) for b in app.conversation(KEY)["blocks"]
    ] == before_results
    assert not app.transport.deliveries


@pytest.mark.parametrize("mode", ["restart", "reconnect", "gap"])
async def test_unchanged_transient_reply_later_cached_does_not_regenerate_dependents(
    tmp_path, mode
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    parent = message(
        app,
        1,
        date=now[0] - 31 * DAY,
        outgoing=True,
        text="Authored exact older reference.",
    )
    app.transport.messages = [parent]
    for identity in (20, 22):
        app.sync.ingest(message(app, identity, reply_to=1))
        app.sync.ingest(message(app, identity + 1, outgoing=True))
    await complete(app, now)
    before = jobs(app)
    assert len(before) == 2 and all(job["state"] == "done" for job in before)
    assert all(parent.key in json.loads(job["payload"])["transient"] for job in before)
    assert app.store.message(KEY, 1) is None
    before_notices = app.notifications.rows()
    before_results = [(b["id"], b["rendered"]) for b in app.conversation(KEY)["blocks"]]

    # The exact reply was valid transient context. A later update merely adds
    # its identical native revision to the cache; no dependency has changed.
    app = await replay(app, tmp_path, now, [parent], mode, target=False)
    now[0] += 60
    await app.process_due()
    app.notifications.advance()
    assert jobs(app) == before
    assert not app.engine.calls
    assert app.notifications.rows() == before_notices
    assert [
        (b["id"], b["rendered"]) for b in app.conversation(KEY)["blocks"]
    ] == before_results
    assert not app.transport.deliveries


@pytest.mark.parametrize("change", ["same_revision", "new_revision", "legacy"])
async def test_transient_cache_admission_requires_proven_unchanged_content(
    tmp_path, change
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    parent = message(
        app,
        1,
        date=now[0] - 31 * DAY,
        outgoing=True,
        text="Authored original exact reference.",
    )
    app.transport.messages = [parent]
    target = message(app, 20, reply_to=1)
    app.sync.ingest(target)
    await complete(app, now)
    original_job = jobs(app)[0]
    if change == "legacy":
        # Older job metadata proves only a native revision, not equal content.
        payload = json.loads(original_job["payload"])
        payload.pop("transient_inputs", None)
        with app.store.transaction() as db:
            db.execute(
                "UPDATE jobs SET payload=? WHERE id=?",
                (json.dumps(payload), original_job["id"]),
            )
        arriving = parent
    else:
        arriving = parent.model_copy(
            update={
                "text": "Authored changed exact reference.",
                "revision": 2 if change == "new_revision" else parent.revision,
            }
        )

    app.sync.ingest(arriving, target=False)
    queued = [job for job in jobs(app) if job["state"] == "queued"]
    assert len(queued) == 1
    assert queued[0]["id"] != original_job["id"]
    assert queued[0]["input_revision"] == original_job["input_revision"]
    assert block(app, target.key)["rendered"] is None
    now[0] += 30
    await app.process_due()
    assert block(app, target.key)["state"] == "ready"
    assert len(app.engine.calls) == 2
    received = app.engine.calls[-1][1]["context"]
    assert (
        next(source["text"] for source in received if source["id"] == 1)
        == arriving.text
    )
    assert not app.notifications.rows()
    assert not app.transport.deliveries


@pytest.mark.parametrize("changed", [False, True])
async def test_cache_arrival_before_publication_checks_the_transient_content(
    tmp_path, changed
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    parent = message(
        app,
        1,
        date=now[0] - 31 * DAY,
        outgoing=True,
        text="Authored exact reference before generation.",
    )
    app.transport.messages = [parent]
    target = message(app, 20, reply_to=1)
    started, finish = asyncio.Event(), asyncio.Event()

    class Engine:
        name, model, provider = "offline", "authored-cache-race", "local"

        async def generate_stream(self, role, packet, on_text):
            on_text("Authored provisional result")
            started.set()
            await finish.wait()
            return EngineResponse(
                {"text": "Authored validated result", "image_context": []},
                actual_nanos=0,
            )

    app.engine = Engine()
    app.sync.ingest(target)
    now[0] += 30
    work = asyncio.create_task(app.process_due())
    try:
        await asyncio.wait_for(started.wait(), 2)
        arriving = (
            parent.model_copy(update={"text": "Authored changed exact reference."})
            if changed
            else parent
        )
        # Intake commits a cache row before rebuilding dependents. Simulate
        # publication in that gap so its own input guard must reject stale text.
        assert app.store.apply_message(arriving, intake="history")
        during = block(app, target.key)["stream"]
    finally:
        finish.set()
        await work
    result = block(app, target.key)
    if changed:
        assert during is None
        assert result["rendered"] is None
        assert jobs(app)[0]["state"] == "superseded"
        app.changed(app.store.message(KEY, 1), target=False)
        assert len([job for job in jobs(app) if job["state"] == "queued"]) == 1
    else:
        assert during and during["text"] == "Authored provisional result"
        assert result["rendered"] == "Authored validated result"
        assert jobs(app)[0]["state"] == "done"
        app.changed(app.store.message(KEY, 1), target=False)
        assert len(jobs(app)) == 1
    assert result["stream"] is None
    assert not app.notifications.rows()
    assert not app.transport.deliveries


async def test_transient_attachment_metadata_change_invalidates_its_dependents(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    parent = message(
        app,
        1,
        date=now[0] - 31 * DAY,
        outgoing=True,
        text="Authored reference to an attached proposal.",
        media="document",
        attachment_id="authored-document-1",
        media_info=MediaInfo(file_name="proposal-draft.txt"),
    )
    app.transport.messages = [parent]
    target = message(app, 20, reply_to=1)
    app.sync.ingest(target)
    await complete(app, now)
    previous = jobs(app)[0]
    assert app.store.message(KEY, parent.message_id) is None
    original_context = app.engine.calls[-1][1]["context"][0]
    assert original_context["placeholder"]["file_name"] == "proposal-draft.txt"

    arriving = parent.model_copy(
        update={"media_info": MediaInfo(file_name="proposal-final.txt")}
    )
    assert arriving.revision == parent.revision
    assert arriving.attachment_id == parent.attachment_id
    app.sync.ingest(arriving, target=False)
    queued = [job for job in jobs(app) if job["state"] == "queued"]
    assert len(queued) == 1
    assert queued[0]["input_revision"] == previous["input_revision"]
    assert block(app, target.key)["rendered"] is None
    now[0] += 30
    await app.process_due()
    assert block(app, target.key)["state"] == "ready"
    assert len(app.engine.calls) == 2
    received = app.engine.calls[-1][1]["context"][0]
    assert received["placeholder"]["file_name"] == "proposal-final.txt"
    assert not app.notifications.rows()
    assert not app.transport.deliveries


async def test_unprovable_attachment_cache_arrival_rejects_late_publication(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    parent = message(
        app,
        1,
        date=now[0] - 31 * DAY,
        outgoing=True,
        text="Authored reference to a legacy attachment.",
        media="document",
        media_info=MediaInfo(file_name="authored-legacy.txt"),
    )
    app.transport.messages = [parent]
    target = message(app, 20, reply_to=1)
    started, finish = asyncio.Event(), asyncio.Event()

    class Engine:
        name, model, provider = "offline", "authored-unknown-attachment", "local"

        async def generate_stream(self, role, packet, on_text):
            on_text("Authored provisional attachment result")
            started.set()
            await finish.wait()
            return EngineResponse(
                {"text": "Authored late attachment result", "image_context": []},
                actual_nanos=0,
            )

    app.engine = Engine()
    app.sync.ingest(target)
    now[0] += 30
    work = asyncio.create_task(app.process_due())
    try:
        await asyncio.wait_for(started.wait(), 2)
        payload = json.loads(jobs(app)[0]["payload"])
        assert parent.key in payload["transient"]
        assert parent.key not in payload["transient_inputs"]
        assert block(app, target.key)["stream"]
        # Equal display metadata cannot establish equal attachment content.
        # Publication must fail closed before the rebuild callback can run.
        assert app.store.apply_message(parent, intake="history")
        during = block(app, target.key)["stream"]
    finally:
        finish.set()
        await work
    assert during is None
    assert block(app, target.key)["rendered"] is None
    assert jobs(app)[0]["state"] == "superseded"
    app.changed(app.store.message(KEY, 1), target=False)
    assert len([job for job in jobs(app) if job["state"] == "queued"]) == 1
    assert not app.notifications.rows()
    assert not app.transport.deliveries


@pytest.mark.parametrize("change", ["edit", "delete"])
async def test_changed_supporting_source_requeues_all_dependent_unchanged_targets(
    tmp_path, change
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    context = message(app, 1, text="Authored supporting context.")
    app.sync.ingest(context)
    app.sync.ingest(message(app, 2, outgoing=True))
    for identity in (3, 5, 7, 9, 11):
        app.sync.ingest(message(app, identity, text=f"Authored target {identity}."))
        app.sync.ingest(message(app, identity + 1, outgoing=True))
    await complete(app, now)
    dependent = {f"{KEY}/{identity}" for identity in (3, 5, 7, 9, 11)}
    before = {job["scope"]: job for job in jobs(app)}
    assert all(
        json.loads(before[identity]["payload"])["inputs"][context.key]
        == context.revision
        for identity in dependent
    )

    app = fixture(tmp_path, clock=lambda: now[0])
    app.store.recover()
    update = context.model_copy(
        update={"text": "", "deleted": True}
        if change == "delete"
        else {"text": "Authored corrected context.", "revision": 2}
    )
    app.sync.ingest(update)
    new_jobs = [
        job for job in jobs(app) if job["id"] not in {j["id"] for j in before.values()}
    ]
    affected = [job for job in new_jobs if job["scope"] in dependent]
    assert {job["scope"] for job in affected} == dependent
    assert all(job["state"] == "queued" for job in affected)
    assert all(
        job["input_revision"] == before[job["scope"]]["input_revision"]
        for job in affected
    )
    assert all(not json.loads(job["payload"]).get("explicit_retry") for job in affected)
    assert all(block(app, identity)["rendered"] is None for identity in dependent)
    assert not app.notifications.rows()


@pytest.mark.parametrize("outcome", ["prepare", "format", "refused", "interrupted"])
async def test_same_revision_redundant_failure_keeps_current_valid_rendering(
    tmp_path, outcome
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app))
    await complete(app, now)
    identity = f"{KEY}/1"
    previous = block(app, identity)
    before_notices = app.notifications.rows()
    with app.store.transaction() as db:
        enqueue(db, identity, previous["revision"], now[0])
        db.execute("UPDATE blocks SET state='queued' WHERE id=?", (identity,))

    class Engine:
        name, model, provider = "offline", "authored-failure", "local"

        async def prepare(self, packet):
            if outcome == "prepare":
                raise Refused("Authored preparation unavailable")
            if outcome == "refused":
                raise ProviderRefused("Authored no-work refusal")

        async def generate_stream(self, role, packet, on_text):
            on_text("Authored incomplete replacement")
            if outcome == "interrupted":
                raise asyncio.CancelledError
            return EngineResponse({"wrong": "shape"}, actual_nanos=0)

    app.engine = Engine()
    if outcome == "interrupted":
        with pytest.raises(asyncio.CancelledError):
            await app.process_due()
        app.store.recover()
    else:
        await app.process_due()
        if outcome == "refused":
            for delay in (30, 60, 120):
                now[0] += delay
                await app.process_due()
    result = block(app, identity)
    assert result["state"] in {"failed", "unknown"}
    assert result["rendered"] == previous["rendered"]
    assert result["rendered_revision"] == result["revision"] == previous["revision"]
    assert result["stream"] is None
    assert not any(
        job["state"] in {"queued", "claimed", "running"} for job in jobs(app)
    )
    app.notifications.advance()
    assert app.notifications.rows() == before_notices
    assert not app.transport.deliveries


@pytest.mark.parametrize("change", ["edit", "delete"])
async def test_failed_changed_dependency_attempt_never_restores_the_old_result(
    tmp_path, change
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    context = message(app, 1, text="Authored supporting context.")
    app.sync.ingest(context)
    app.sync.ingest(message(app, 2, outgoing=True))
    app.sync.ingest(message(app, 3, text="Authored dependent target."))
    await complete(app, now)
    identity = f"{KEY}/3"
    revision = block(app, identity)["revision"]
    assert block(app, identity)["rendered"]

    if change == "delete":
        app.sync.deleted("user:42", [1])
    else:
        app.sync.ingest(
            context.model_copy(update={"text": "Authored corrected context."})
        )
    assert block(app, identity)["revision"] == revision
    assert block(app, identity)["rendered"] is None

    async def invalid(role, packet):
        return {"wrong": "shape"}

    app.engine.generate = invalid
    now[0] += 30
    for _ in [job for job in jobs(app) if job["state"] == "queued"]:
        await app.process_due()
    assert block(app, identity)["state"] == "failed"
    assert block(app, identity)["rendered"] is None
    assert not app.notifications.rows()
