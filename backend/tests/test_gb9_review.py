"""Independent authored regressions for GB9 integration boundaries."""

import asyncio

import pytest
from enloq.domain.models import Message
from enloq.storage.store import WINDOW

from backend.tests.test_retained_groups import rig


def authored_forward(app, key):
    cached = Message(
        account_id=1,
        peer=app.store.chat(key).peer,
        message_id=1,
        revision=1,
        date=app.store.clock(),
        author_peer="user:7",
        text="Authored cached group forward",
        forwarded=True,
    )
    app.sync.ingest(cached)
    app.store.mutate_draft(key, 0, lambda draft: draft.update(text="Kept words"))
    native = cached.model_copy(
        update={
            "forwarded_from": "Authored native origin",
            "forward_date": app.store.clock() - 60,
            "forward_read": True,
        }
    )
    app.transport.messages = [native]
    return cached, native


async def test_retained_forwarding_metadata_repair_stays_cache_only(tmp_path):
    app, now, key = rig(tmp_path)
    source, _ = authored_forward(app, key)
    now[0] += WINDOW + 1
    assert app.retained.assert_open(key).peer == source.peer
    assert app.store.groups.get(key).access == "available"
    assert app.store.recent_chats("group", cached_access=True) == []

    # Opening the retained Conversation invokes this same route through
    # useForwardingMetadata whenever cached attribution is still incomplete.
    values = await app.forwarding.refresh(key, [source.message_id])

    assert app.transport.exact_reads == []
    assert values[0]["forwarded_from"] is None
    assert not app.store.message(key, source.message_id).forward_read
    assert app.store.draft(key)["text"] == "Kept words"


async def test_active_group_forwarding_metadata_repair_keeps_bounded_exact_read(
    tmp_path,
):
    app, _, key = rig(tmp_path)
    source, native = authored_forward(app, key)

    values = await app.forwarding.refresh(key, [source.message_id])

    assert app.transport.exact_reads == [(source.peer, (source.message_id,))]
    assert values[0]["forwarded_from"] == native.forwarded_from
    assert app.store.message(key, source.message_id).forward_read
    assert app.store.message(key, source.message_id).revision == source.revision
    assert app.store.draft(key)["text"] == "Kept words"


@pytest.mark.parametrize("change", ["inactive", "lost", "restored"])
async def test_held_group_forwarding_read_cannot_learn_after_admission_changes(
    tmp_path, change
):
    app, now, key = rig(tmp_path)
    source, native = authored_forward(app, key)
    entered, release = asyncio.Event(), asyncio.Event()

    async def held(peer, identities):
        app.transport.exact_reads.append((peer, tuple(identities)))
        entered.set()
        await release.wait()
        return [native]

    app.transport.exact_many = held
    read = asyncio.create_task(app.forwarding.refresh(key, [source.message_id]))
    await asyncio.wait_for(entered.wait(), 2)
    if change == "inactive":
        now[0] += WINDOW + 1
    else:
        app.store.groups.observe(key, access="lost", reason="authored_native_loss")
        if change == "restored":
            app.store.groups.observe(
                key, access="available", reason="authored_native_return"
            )
    release.set()
    values = await asyncio.wait_for(read, 2)

    assert app.transport.exact_reads == [(source.peer, (source.message_id,))]
    assert values[0]["forwarded_from"] is None
    assert not app.store.message(key, source.message_id).forward_read
    assert app.store.message(key, source.message_id).revision == source.revision
    assert app.store.draft(key)["text"] == "Kept words"
