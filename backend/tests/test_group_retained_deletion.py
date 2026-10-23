"""Inactive group recovery needs checked own cached sources and renewed intent."""

import pytest
from enloq.domain.models import Conflict, Refused
from enloq.storage.store import RETENTION, WINDOW

from backend.tests.test_group_deletion import group, submit, targets


def clock(app):
    now = [app.store.clock()]
    app.store.clock = lambda: now[0]
    return now


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_checked_same_group_retry_recovers_outside_active_window(tmp_path, peer):
    app, key = group(tmp_path, peer)
    now = clock(app)
    original = app.store.message(key, 1)
    operation = await submit(app, key)
    app.transport.deletion_failure = Refused("Authored native refusal")
    failed = await app.deletion.dispatch(operation["id"])
    assert failed["state"] == "failed"
    now[0] += WINDOW + 1
    activity = app.store.rows("SELECT activity FROM chats WHERE key=?", (key,))
    assert not app.store.recent_chats("group")
    assert not app.deletion.eligible(original)
    with pytest.raises(Refused, match="activity"):
        await submit(app, key)
    with pytest.raises(Conflict, match="checked"):
        await submit(app, key, retry_of=operation["id"])
    checked = await app.deletion.check(key, operation["id"])
    assert checked["retry_targets"] == targets(app, key)
    assert app.transport.deletion_reads == [(peer, (1,))]
    assert app.retained.entries()[0]["deletion_count"] == 1
    app.transport.deletion_failure = None
    retried = await submit(app, key, checked["retry_targets"], retry_of=operation["id"])
    stored = app.store.deletion_operations.get(retried["id"])
    assert stored["retained_retry"] and stored["retry_of"] == operation["id"]
    completed = await app.deletion.dispatch(retried["id"])
    assert completed["counts"]["acknowledged"] == 1
    assert completed["counts"]["absent"] == 0
    assert app.transport.deletion_calls[-1].peer == peer
    assert app.transport.deletion_calls[-1].sources == (original,)
    assert app.store.message(key, 1).deleted
    assert app.store.rows("SELECT activity FROM chats WHERE key=?", (key,)) == activity
    assert app.retained.entries() == []
    assert not app.store.groups.get(key).processing_enabled
    assert not app.engine.calls and not app.transport.reads


async def test_access_restoration_requires_new_check_before_inactive_retry(tmp_path):
    app, key = group(tmp_path)
    now = clock(app)
    operation = await submit(app, key)
    app.transport.deletion_failure = TimeoutError()
    await app.deletion.dispatch(operation["id"])
    now[0] += WINDOW + 1
    app.store.groups.observe(key, access="lost", reason="authored_native_loss")
    with pytest.raises(Refused, match="access"):
        await app.deletion.check(key, operation["id"])
    assert not app.transport.deletion_reads
    assert app.retained.entries()[0]["reasons"] == ["deletion"]
    assert app.deletion.get(key, operation["id"])["retry_targets"] == []
    app.store.groups.observe(key, access="available", reason="authored_restored")
    with pytest.raises((Refused, Conflict)):
        await submit(app, key, retry_of=operation["id"])
    checked = await app.deletion.check(key, operation["id"])
    assert checked["retry_targets"] == targets(app, key)
    app.transport.deletion_failure = None
    retried = await submit(app, key, checked["retry_targets"], retry_of=operation["id"])
    assert (
        app.store.deletion_operations.get(retried["id"])["access_revision"]
        > app.store.deletion_operations.get(operation["id"])["access_revision"]
    )
    assert (await app.deletion.dispatch(retried["id"]))["counts"]["acknowledged"] == 1


@pytest.mark.parametrize(
    "change", ["lost", "unsupported", "migrated", "epoch", "expired", "author", "media"]
)
async def test_inactive_accepted_retry_rechecks_exact_guards_at_handoff(
    tmp_path, change
):
    from enloq.domain.models import MediaInfo

    app, key = group(tmp_path)
    now = clock(app)
    operation = await submit(app, key)
    app.transport.deletion_failure = Refused("Authored native refusal")
    await app.deletion.dispatch(operation["id"])
    now[0] += WINDOW + 1
    checked = await app.deletion.check(key, operation["id"])
    retried = await submit(app, key, checked["retry_targets"], retry_of=operation["id"])
    if change in ("lost", "unsupported", "migrated"):
        app.store.groups.observe(
            key,
            access="lost" if change != "unsupported" else "available",
            supported=change != "unsupported",
            reason="authored_change",
            migrated_to="channel:99" if change == "migrated" else None,
        )
    elif change == "epoch":
        app.store.groups.mark_gap(key)
    elif change == "expired":
        now[0] += RETENTION
    else:
        original = app.store.message(key, 1)
        changed = original.model_copy(
            update={"author_peer": "user:7"}
            if change == "author"
            else {"media": "document", "media_info": MediaInfo(file_name="changed.txt")}
        )
        # Keep the reviewed local revision: full native content/media proof matters.
        with app.store.transaction() as db:
            db.execute(
                "UPDATE messages SET body=? WHERE key=?",
                (changed.model_dump_json(), changed.key),
            )
    calls = len(app.transport.deletion_calls)
    app.transport.deletion_failure = None
    stopped = await app.deletion.dispatch(retried["id"])
    assert stopped["state"] == "failed"
    assert stopped["counts"]["acknowledged"] == 0
    assert len(app.transport.deletion_calls) == calls
    assert not app.store.message(key, 1).deleted


async def test_inactive_checked_retry_cannot_expand_or_retarget_its_sources(tmp_path):
    app, key = group(tmp_path, count=2)
    now = clock(app)
    operation = await submit(app, key, targets(app, key, [1]))
    app.transport.deletion_failure = Refused("Authored native refusal")
    await app.deletion.dispatch(operation["id"])
    now[0] += WINDOW + 1
    checked = await app.deletion.check(key, operation["id"])
    assert checked["retry_targets"] == targets(app, key, [1])
    with pytest.raises(Conflict, match="checked"):
        await submit(app, key, targets(app, key, [1, 2]), retry_of=operation["id"])
    with pytest.raises(Refused):
        await submit(
            app, "1/channel:42", checked["retry_targets"], retry_of=operation["id"]
        )
    assert len(app.store.rows("SELECT id FROM deletion_operations")) == 1
    assert app.transport.deletion_reads == [("chat:42", (1,))]


async def test_expired_inactive_sources_keep_status_but_gain_no_native_read_or_retry(
    tmp_path,
):
    app, key = group(tmp_path)
    now = clock(app)
    operation = await submit(app, key)
    app.transport.deletion_failure = TimeoutError()
    await app.deletion.dispatch(operation["id"])
    now[0] += RETENTION + 1
    app.store.expire()
    checked = await app.deletion.check(key, operation["id"])
    assert checked["retry_targets"] == [] and checked["targets"][0]["preview"] == ""
    assert checked["targets"][0]["observation"] == "unavailable"
    assert app.transport.deletion_reads == []
    assert app.retained.entries()[0]["deletion_count"] == 1
    with pytest.raises((Refused, Conflict)):
        await submit(
            app, key, [{"message_id": 1, "revision": 1}], retry_of=operation["id"]
        )
    assert len(app.transport.deletion_calls) == 1


async def test_private_inactive_retry_keeps_existing_recent_gate(tmp_path):
    from backend.tests.test_deletion_operations import accept, originals
    from backend.tests.test_rebuild import fixture

    app = fixture(tmp_path)
    now = clock(app)
    reviewed = originals(app, 1)
    operation = await accept(app, reviewed)
    app.transport.deletion_failure = Refused("Authored native refusal")
    await app.deletion.dispatch(operation["id"])
    now[0] += WINDOW + 1
    checked = await app.deletion.check("1/user:42", operation["id"])
    assert checked["retry_targets"] == []
    with pytest.raises((Refused, Conflict)):
        await accept(app, reviewed, retry_of=operation["id"])
