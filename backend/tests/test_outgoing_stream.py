import asyncio

import pytest
from enloq.domain.models import Conflict, Refused
from enloq.inference.base import EngineResponse

from backend.tests.test_rebuild import attach, edit, fixture, message


class HeldEngine:
    name, model, provider = "muse", "synthetic", "meta"

    def __init__(self, *, empty=False, late=False, invalid=False):
        self.started, self.finish = asyncio.Event(), asyncio.Event()
        self.empty, self.late, self.invalid = empty, late, invalid
        self.calls = 0

    async def generate_stream(self, role, packet, on_text):
        self.calls += 1
        if not self.empty:
            on_text("Partial rewrite")
        self.started.set()
        try:
            await self.finish.wait()
        except asyncio.CancelledError:
            if not self.late:
                raise
            await self.finish.wait()
        on_text("Late rewrite")
        return EngineResponse(
            {"wrong": "shape"} if self.invalid else {"text": "Final rewrite"},
            actual_nanos=17,
        )


@pytest.mark.parametrize("empty", [True, False])
@pytest.mark.parametrize("undo", [True, False])
async def test_stop_undo_late_receipts_and_preserved_composition(tmp_path, empty, undo):
    app = fixture(tmp_path)
    app.sync.ingest(message(app), target=False)
    base = edit(app, "Original draft", reply_to=1)
    base = attach(app)
    app.engine = HeldEngine(empty=empty, late=True, invalid=True)
    outcomes = []
    app.rewriting.work.on_format = lambda role, ok: outcomes.append(ok)
    owner = app.rewriting.outgoing_work
    pending = owner.start("1/user:42", base["revision"])
    task = owner.tasks[pending["attempt"]]
    await app.engine.started.wait()
    shown = owner.view("1/user:42")
    assert owner.start("1/user:42", base["revision"])["attempt"] == pending["attempt"]
    reopened = fixture(tmp_path)
    assert reopened.rewriting.outgoing_work.view("1/user:42")["text"] == shown["text"]
    with pytest.raises(Refused):
        await app.delivery.send("1/user:42", shown["revision"])
    kept = owner.stop(
        "1/user:42",
        shown["revision"],
        shown["attempt"],
        undo=undo,
        visible=shown["text"] if shown.get("partial") else None,
    )
    assert kept["text"] == ("Original draft" if empty or undo else "Partial rewrite")
    assert kept["attachments"] == base["attachments"] and kept["reply_to"] == 1
    assert kept["state"] == ("idle" if undo else "stopped")
    await asyncio.sleep(0)
    app.engine.finish.set()
    await asyncio.gather(task, return_exceptions=True)
    assert app.store.draft("1/user:42") == kept
    assert app.store.rows("SELECT status,actual FROM reservations") == [
        {"status": "receipted", "actual": 17}
    ]
    assert (
        app.store.rows("SELECT state FROM jobs WHERE role='outgoing_rewrite'")[0][
            "state"
        ]
        == "cancelled"
    )
    assert outcomes == [] and not app.transport.deliveries
    if not undo:
        restored = app.rewriting.undo("1/user:42", kept["revision"])
        assert restored["text"] == "Original draft"


async def test_stopped_edit_removes_undo_and_restart_preserves_partial(tmp_path):
    app = fixture(tmp_path)
    base = edit(app, "Original")
    app.engine = HeldEngine()
    owner = app.rewriting.outgoing_work
    pending = owner.start("1/user:42", base["revision"])
    task = owner.tasks[pending["attempt"]]
    await app.engine.started.wait()
    owner.stop("1/user:42", pending["revision"], pending["attempt"], visible="Partial")
    await asyncio.gather(task, return_exceptions=True)
    app.store.recover()
    assert app.store.draft("1/user:42")["text"] == "Partial"
    updated = edit(app, "My subsequent edit")
    with pytest.raises(Refused):
        app.rewriting.undo("1/user:42", updated["revision"])


@pytest.mark.parametrize("mode", ["format", "restart", "newer"])
async def test_failure_or_restart_restores_without_overwriting_newer_edits(
    tmp_path, mode
):
    app = fixture(tmp_path)
    base = edit(app, "Keep complete original")
    app.engine = HeldEngine(invalid=mode == "format")
    owner = app.rewriting.outgoing_work
    pending = owner.start("1/user:42", base["revision"])
    task = owner.tasks[pending["attempt"]]
    await app.engine.started.wait()
    if mode == "newer":
        edit(app, "Newer owner text")
    if mode == "restart":
        app.store.recover()
    app.engine.finish.set()
    await asyncio.gather(task, return_exceptions=True)
    assert app.store.draft("1/user:42")["text"] == (
        "Newer owner text" if mode == "newer" else "Keep complete original"
    )
    assert not app.store.rows("SELECT * FROM rewrite_streams")
    assert app.engine.calls == 1


async def test_stop_wrong_chat_revision_and_before_any_dispatch(tmp_path):
    app = fixture(tmp_path)
    base = edit(app, "Keep")
    owner = app.rewriting.outgoing_work
    pending = owner.start("1/user:42", base["revision"])
    task = owner.tasks[pending["attempt"]]
    with pytest.raises(Conflict):
        owner.stop("1/user:99", pending["revision"], pending["attempt"])
    with pytest.raises(Conflict):
        owner.stop("1/user:42", base["revision"], pending["attempt"])
    kept = owner.stop("1/user:42", pending["revision"], pending["attempt"])
    await asyncio.gather(task, return_exceptions=True)
    assert kept["text"] == "Keep" and kept["undo"] == "Keep"
    assert not app.engine.calls and not app.store.rows("SELECT * FROM reservations")


@pytest.mark.parametrize("undo", [True, False])
async def test_completion_between_snapshot_and_stop_or_undo(tmp_path, undo):
    app = fixture(tmp_path)
    base = edit(app, "Complete original")
    owner = app.rewriting.outgoing_work
    pending = owner.start("1/user:42", base["revision"])
    await owner.tasks[pending["attempt"]]
    if undo:
        kept = app.rewriting.undo("1/user:42", pending["revision"], pending["attempt"])
    else:
        kept = owner.stop(
            "1/user:42", pending["revision"], pending["attempt"], visible="Complete"
        )
    assert kept["text"] == ("Complete original" if undo else "Complete")
    assert kept["state"] == ("idle" if undo else "stopped")
    edit(app, "Newer edit")
    with pytest.raises(Conflict):
        app.rewriting.undo("1/user:42", pending["revision"], pending["attempt"])


async def test_restart_before_dispatch_releases_draft_for_explicit_retry(tmp_path):
    app = fixture(tmp_path)
    base = edit(app, "Not dispatched")
    owner = app.rewriting.outgoing_work
    pending = owner.start("1/user:42", base["revision"])
    task = owner.tasks[pending["attempt"]]
    app.store.recover()
    await asyncio.gather(task, return_exceptions=True)
    assert not app.engine.calls
    restored = app.store.draft("1/user:42")
    assert restored["text"] == "Not dispatched" and restored["state"] == "failed"
    await app.rewriting.outgoing("1/user:42", restored["revision"])
    assert len(app.engine.calls) == 1
