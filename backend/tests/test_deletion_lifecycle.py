"""Authored account shutdown/removal and retained native evidence, without sockets."""

import asyncio
import base64
import uuid
from types import SimpleNamespace

import pytest
from enloq.application.accounts import AccountManager
from enloq.config import Settings
from enloq.domain.models import Refused
from enloq.storage.store import RETENTION

from backend.tests.test_rebuild import image_bytes


def user(identity):
    return SimpleNamespace(
        id=identity, bot=False, first_name="Authored owner", last_name="", username=""
    )


async def setup(tmp_path):
    manager = AccountManager(Settings(data=tmp_path / "offline"))
    await manager.activate(user(2), "synthetic.session")
    app = manager.get(2)
    key = "2/user:101"
    source = app.store.message(key, 1)
    app.transport.messages = [source]
    operation = await app.deletion.submit(
        key,
        uuid.uuid4().hex,
        [{"message_id": 1, "revision": source.revision}],
        browser="authored-browser",
    )
    return manager, app, key, operation


async def test_logout_keeps_uncertain_receipt_and_exact_absence_cannot_allow_removal(
    tmp_path,
):
    manager, app, key, operation = await setup(tmp_path)
    try:
        app.transport.lose_delete_receipt = True
        result = await app.deletion.dispatch(operation["id"])
        assert result["state"] == "uncertain"
        checked = await app.deletion.check(key, operation["id"])
        assert checked["counts"]["absent"] == 1
        assert checked["counts"]["acknowledged"] == 0
        await manager.logout(2)
        with pytest.raises(Refused, match="pending deletions"):
            await manager.remove(2)
        kept = manager._store(manager.registry.get(2))
        assert kept.deletion_operations.get(operation["id"])["state"] == "uncertain"
        assert kept.deletion_operations.reply_hold(key, 1) == "deleted"
        assert kept.deletion_operations.blocks_removal()
        kept.close()
        assert [a["account_id"] for a in manager.registry.accounts()] == [1, 2]
    finally:
        await manager.close()


async def test_account_drain_settles_exposed_mutation_and_preserves_newer_composer(
    tmp_path,
):
    manager, app, key, operation = await setup(tmp_path)
    exposed = asyncio.Event()

    async def delete(description, evidence):
        evidence.calling()
        evidence.watching()
        evidence.handing_off()
        exposed.set()
        await asyncio.Event().wait()

    app.transport.delete = delete
    draft = app.store.mutate_draft(
        key, 0, lambda value: value.update(text="Newer authored text", reply_to=1)
    )
    # A private authored PNG belongs to the draft, not the incoming-media cache.
    draft = app.attachments.add(
        key, draft["revision"], "authored.png", base64.b64encode(image_bytes()).decode()
    )
    image = draft["attachments"][0]
    _, path = app.attachments.get(key, image)
    expected = path.read_bytes()
    task = asyncio.create_task(app.deletion.dispatch(operation["id"]))
    manager.runtimes[2].workers.append(task)
    try:
        async with asyncio.timeout(5):
            await exposed.wait()
        await manager.logout(2)
        assert task.cancelled()
        kept = manager._store(manager.registry.get(2))
        saved = kept.draft(key)
        assert saved["text"] == "Newer authored text" and saved["reply_to"] == 1
        assert saved["attachments"] == [image] and path.read_bytes() == expected
        deletion = kept.deletion_operations.get(operation["id"])
        assert deletion["state"] == "uncertain"
        assert kept.deletion_operations.reply_hold(key, 1) == "unresolved"
        kept.close()
        with pytest.raises(Refused, match="pending deletions"):
            await manager.remove(2)
        assert not app.transport.deletion_calls
    finally:
        await manager.close()


async def test_expiry_retains_uncertain_operational_ids_without_original_content(
    tmp_path,
):
    manager, app, key, operation = await setup(tmp_path)
    try:
        app.transport.deletion_failure = TimeoutError()
        await app.deletion.dispatch(operation["id"])
        now = app.store.clock()
        app.store.clock = lambda: now + RETENTION + 1
        app.store.expire()
        assert app.store.message(key, 1) is None
        saved = app.store.deletion_operations.get(operation["id"])
        assert saved["state"] == "uncertain" and saved["targets"][0]["preview"] == ""
        assert app.store.deletion_operations.reply_hold(key, 1) == "unresolved"
        await manager.logout(2)
        with pytest.raises(Refused, match="pending deletions"):
            await manager.remove(2)
    finally:
        await manager.close()


async def test_confirmed_deletion_does_not_block_other_account_or_local_removal(
    tmp_path,
):
    manager, app, key, operation = await setup(tmp_path)
    try:
        result = await app.deletion.dispatch(operation["id"])
        assert (
            result["state"] == "completed"
            and not app.store.deletion_operations.blocks_removal()
        )
        other = manager.get(1)
        other_draft = other.store.mutate_draft(
            "1/user:101", 0, lambda value: value.update(text="Other account draft")
        )
        other_path = other.store.path
        await manager.logout(2)
        await manager.remove(2)
        assert other_path.exists()
        assert other.store.draft("1/user:101") == other_draft
        assert [a["account_id"] for a in manager.registry.accounts()] == [1]
    finally:
        await manager.close()


async def test_browser_projection_reports_target_holds_without_mutating_draft(tmp_path):
    manager, app, key, operation = await setup(tmp_path)
    try:
        draft = app.store.mutate_draft(
            key, 0, lambda value: value.update(text="Keep editable text", reply_to=1)
        )
        before = app.conversation(key)
        assert before["reply_holds"] == {1: "pending"}
        assert before["draft"] == draft
        await app.deletion.dispatch(operation["id"])
        after = app.conversation(key)
        assert after["reply_holds"] == {1: "deleted"}
        assert after["draft"] == draft
    finally:
        await manager.close()


async def test_startup_finishes_saved_native_delete_effects_without_another_mutation(
    tmp_path,
):
    settings = Settings(data=tmp_path / "offline")
    first = AccountManager(settings)
    app = first.get(1)

    def interrupted(*args, **kwargs):
        raise RuntimeError("Authored local dependency interruption")

    app.sync.changed = interrupted
    with pytest.raises(RuntimeError, match="dependency interruption"):
        app.sync.deleted("user:101", [1])
    assert app.store.message("1/user:101", 1).deleted
    assert app.store.rows("SELECT key FROM cursors WHERE key LIKE 'deletion-effects:%'")
    await first.close()
    second = AccountManager(settings)
    try:
        await second.start()
        recovered = second.get(1)
        assert recovered.store.message("1/user:101", 1).deleted
        assert not recovered.store.rows(
            "SELECT key FROM cursors WHERE key LIKE 'deletion-effects:%'"
        )
        assert not recovered.transport.deletion_calls
    finally:
        await second.close()
