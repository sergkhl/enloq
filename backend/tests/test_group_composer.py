"""Authored drafts and held engines prove group boundaries without provider calls."""

import asyncio
import base64

import pytest
from enloq.application.accounts import AccountManager
from enloq.config import Settings
from enloq.domain.models import Chat, ProcessingPaused, Refused
from enloq.inference.base import EngineResponse

from backend.tests.test_avatars import picture


class HeldEngine:
    name, model, provider = "muse", "authored-group-rewrite", "meta"

    def __init__(self, *, prepare=False, late=False):
        self.started, self.finish = asyncio.Event(), asyncio.Event()
        self.prepared, self.release = asyncio.Event(), asyncio.Event()
        self.calls = []
        self.late = late
        if prepare:
            self.prepare = self.wait_prepare

    async def wait_prepare(self, role):
        self.prepared.set()
        await self.release.wait()

    async def generate_stream(self, role, packet, on_text):
        self.calls.append((role, packet))
        on_text("Authored partial rewrite")
        self.started.set()
        try:
            await self.finish.wait()
        except asyncio.CancelledError:
            if not self.late:
                raise
            await self.finish.wait()
        on_text("Authored late rewrite")
        return EngineResponse({"text": "Authored final rewrite"}, actual_nanos=17)


@pytest.fixture
def rig(tmp_path):
    manager = AccountManager(Settings(data=tmp_path))
    app = manager.get(1)
    app.store.clock = lambda: 2_000_000_000
    app.store.put_chat(
        Chat(account_id=1, peer="channel:42", title="Authored group", kind="group"),
        activity=app.store.clock(),
    )
    key = "1/channel:42"
    app.store.groups.observe(key, access="available", reason="native_metadata")
    return manager, app, key


def draft(app, key, text="Authored short draft", **values):
    return app.store.mutate_draft(
        key,
        app.store.draft(key)["revision"],
        lambda value: value.update(text=text, **values),
    )


def begin(app, key):
    owner = app.rewriting.outgoing_work
    pending = owner.start(key, app.store.draft(key)["revision"])
    return owner, pending, owner.tasks[pending["attempt"]]


@pytest.mark.parametrize("tone", ["neutral", "professional", "friendly"])
async def test_group_off_allows_deliberate_rewrite_with_effective_tone(rig, tone):
    manager, app, key = rig
    remembered = manager.registry.set_tone(tone)
    base = draft(app, key, reply_to=9)
    base = app.attachments.add(
        key, base["revision"], "authored.png", base64.b64encode(picture()).decode()
    )
    app.engine = HeldEngine()
    assert not app.store.groups.get(key).processing_enabled
    assert manager.registry.processing_threshold() == 200
    owner, pending, task = begin(app, key)
    await app.engine.started.wait()
    assert owner.view(key)["text"] == "Authored partial rewrite"
    role, packet = app.engine.calls[0]
    assert role == "outgoing_rewrite"
    assert packet["tone"] == tone
    assert packet["inputs"] == {} and "recipient" not in packet
    assert "context" not in packet and "memory" not in packet
    assert packet["draft"] == {"text": base["text"]} and len(packet["images"]) == 1
    assert manager.registry.preferences() == remembered
    app.engine.finish.set()
    final = await task
    assert final["text"] == "Authored final rewrite" and final["undo"] == base["text"]
    assert final["reply_to"] == 9 and final["attachments"] == base["attachments"]
    assert not app.store.rows("SELECT * FROM emotion_state")
    assert not app.store.rows("SELECT * FROM suggestion_state")
    assert app.store.rows("SELECT actual FROM reservations") == [{"actual": 17}]
    assert not app.transport.deliveries and not app.store.rows("SELECT * FROM blocks")
    assert app.store.draft(key)["attempt"] == pending["attempt"]


@pytest.mark.parametrize(
    "access,supported", [("unknown", True), ("lost", True), ("available", False)]
)
def test_group_rewrite_requires_supported_available_native_access(
    rig, access, supported
):
    manager, app, key = rig
    base = draft(app, key)
    app.store.groups.observe(
        key, access=access, supported=supported, reason="authored_evidence"
    )
    with pytest.raises(Refused, match="Native group access"):
        begin(app, key)
    assert app.store.draft(key) == base
    assert not app.store.rows("SELECT * FROM jobs")
    assert not app.store.rows("SELECT * FROM reservations")


def test_account_master_off_refuses_group_rewrite_without_mutating_draft(rig):
    manager, app, key = rig
    base = draft(app, key)
    app.store.groups.set_processing_enabled(key, True)
    manager.set_processing(1, False)
    with pytest.raises(ProcessingPaused, match="Off for this account"):
        begin(app, key)
    assert app.store.draft(key) == base and not app.store.rows("SELECT * FROM jobs")


@pytest.mark.parametrize("change", ["master", "loss", "protected", "tone"])
async def test_group_preparation_rechecks_before_new_provider_dispatch(rig, change):
    manager, app, key = rig
    manager.registry.set_tone("friendly")
    base = draft(app, key)
    app.engine = HeldEngine(prepare=True)
    owner, pending, task = begin(app, key)
    await app.engine.prepared.wait()
    if change == "master":
        manager.set_processing(1, False)
    elif change == "tone":
        manager.registry.set_tone("neutral")
    else:
        app.store.groups.observe(
            key,
            access="lost" if change == "loss" else "available",
            supported=change != "protected",
            reason="authored_change",
        )
    app.engine.release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert not app.engine.calls and not app.store.rows("SELECT * FROM reservations")
    restored = app.store.draft(key)
    assert restored["text"] == base["text"] and restored["state"] == "failed"
    assert not app.store.rows("SELECT * FROM rewrite_streams")


@pytest.mark.parametrize(
    "change", ["tone", "loss", "restore", "protected", "eligibility"]
)
async def test_group_late_stream_cannot_publish_after_material_change(rig, change):
    manager, app, key = rig
    manager.registry.set_tone("friendly")
    base = draft(app, key)
    app.engine = HeldEngine(late=True)
    owner, pending, task = begin(app, key)
    await app.engine.started.wait()
    if change == "tone":
        manager.registry.set_tone("neutral")
    elif change == "eligibility":
        app.store.put_chat(
            app.store.chat(key).model_copy(update={"ai_eligible": False})
        )
    else:
        app.store.groups.observe(
            key,
            access="available" if change == "protected" else "lost",
            supported=change != "protected",
            reason="authored_change",
        )
        if change == "restore":
            app.store.groups.observe(
                key, access="available", reason="fresh_restoration"
            )
    app.engine.finish.set()
    await asyncio.gather(task, return_exceptions=True)
    restored = app.store.draft(key)
    assert restored["text"] == base["text"] and restored["state"] == "failed"
    assert app.store.rows("SELECT actual FROM reservations") == [{"actual": 17}]
    assert not app.store.rows("SELECT * FROM rewrite_streams")


async def test_group_revalidation_restores_original_and_rejects_cancel_ignoring_late_output(
    rig,
):
    manager, app, key = rig
    base = draft(app, key)
    app.engine = HeldEngine(late=True)
    owner, pending, task = begin(app, key)
    await app.engine.started.wait()
    app.store.groups.observe(key, access="lost", reason="confirmed_left")
    restored = app.store.draft(key)
    assert restored["text"] == base["text"] and restored["state"] == "failed"
    assert owner.view(key) == restored and not app.engine.finish.is_set()
    assert not app.store.rows("SELECT * FROM rewrite_streams")
    await asyncio.sleep(0)
    app.store.groups.observe(key, access="available", reason="fresh_restoration")
    newer = draft(app, key, "New authored draft after restoration")
    app.engine.finish.set()
    await asyncio.gather(task, return_exceptions=True)
    assert app.store.draft(key) == newer
    assert app.store.rows("SELECT actual FROM reservations") == [{"actual": 17}]


async def test_group_off_and_account_off_allow_valid_already_dispatched_rewrite_to_finish(
    rig,
):
    manager, app, key = rig
    app.store.groups.set_processing_enabled(key, True)
    draft(app, key)
    app.engine = HeldEngine()
    owner, pending, task = begin(app, key)
    await app.engine.started.wait()
    app.store.groups.set_processing_enabled(key, False)
    manager.set_processing(1, False)
    owner.revalidate()
    app.engine.finish.set()
    assert (await task)["text"] == "Authored final rewrite"
    assert not app.store.groups.get(key).processing_enabled
    assert not manager.registry.processing(1)["ai_enabled"]


async def test_duplicate_rewrite_click_cannot_reenter_after_confirmed_access_loss(rig):
    manager, app, key = rig
    base = draft(app, key)
    app.engine = HeldEngine()
    owner, pending, task = begin(app, key)
    await app.engine.started.wait()
    app.store.groups.observe(key, access="lost", reason="confirmed_left")
    restored = app.store.draft(key)
    assert restored["text"] == base["text"] and restored["state"] == "failed"
    with pytest.raises(Refused, match="Native group access"):
        owner.start(key, base["revision"])
    with pytest.raises(Refused, match="Native group access"):
        owner.start(key, restored["revision"])
    app.engine.finish.set()
    await asyncio.gather(task, return_exceptions=True)
    assert app.store.draft(key)["text"] == base["text"]
    assert len(app.engine.calls) == 1


@pytest.mark.parametrize("change", ["loss", "protected", "migration"])
async def test_native_group_observation_immediately_restores_draft_before_provider_finishes(
    rig, change
):
    manager, app, key = rig
    if change == "migration":
        key = "1/chat:42"
        app.store.put_chat(
            Chat(
                account_id=1, peer="chat:42", title="Authored basic group", kind="group"
            ),
            activity=app.store.clock(),
        )
        app.store.groups.observe(key, access="available", reason="native_metadata")
    base = draft(app, key, reply_to=9)
    base = app.attachments.add(
        key, base["revision"], "authored.png", base64.b64encode(picture()).decode()
    )
    app.engine = HeldEngine(late=True)
    owner, pending, task = begin(app, key)
    await app.engine.started.wait()
    app.store.groups.observe(
        key,
        access="available" if change == "protected" else "lost",
        supported=change != "protected",
        reason="confirmed_native_change",
        migrated_to="channel:43" if change == "migration" else None,
    )
    restored = app.store.draft(key)
    assert restored["state"] == "failed" and restored["text"] == base["text"]
    assert (
        restored["reply_to"] == base["reply_to"]
        and restored["attachments"] == base["attachments"]
    )
    assert owner.view(key) == restored and not app.engine.finish.is_set()
    assert not app.store.rows("SELECT * FROM rewrite_streams")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    app.engine.finish.set()
    await asyncio.gather(task, return_exceptions=True)
    assert app.store.draft(key) == restored
    assert app.store.rows("SELECT actual FROM reservations") == [{"actual": 17}]


async def test_nonmaterial_native_group_observation_preserves_active_authored_rewrite(
    rig,
):
    manager, app, key = rig
    draft(app, key)
    app.engine = HeldEngine()
    owner, pending, task = begin(app, key)
    await app.engine.started.wait()
    before = owner.view(key)
    revision = app.store.groups.get(key).access_revision
    app.store.groups.observe(key, access="available", reason="same_membership_refresh")
    assert app.store.groups.get(key).access_revision == revision
    assert owner.view(key) == before
    await asyncio.sleep(0)
    assert not task.done()
    app.engine.finish.set()
    assert (await task)["text"] == "Authored final rewrite"
