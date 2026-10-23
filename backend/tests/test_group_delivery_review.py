"""Focused GB7 review races use authored metadata and no native session."""

import pytest
from enloq.domain.models import Refused
from telethon import functions, types

from backend.tests.test_group_intake import entity
from backend.tests.test_group_send_transport import setup
from backend.tests.test_selected_transport import Evidence


@pytest.mark.parametrize("awaited", ["metadata", "sender", "reply"])
async def test_same_access_rights_change_during_preparation_cannot_bless_old_rights(
    tmp_path, awaited
):
    app, now, key, rpc, description, receipts = setup(tmp_path)
    revision = app.store.groups.get(key).access_revision
    request_type = {
        "metadata": functions.channels.GetChannelsRequest,
        "sender": functions.channels.GetSendAsRequest,
        "reply": functions.channels.GetMessagesRequest,
    }[awaited]
    changed = False

    def update_rights(request):
        nonlocal changed
        if changed or not isinstance(request, request_type):
            return
        changed = True
        # This is a real eligibility change without loss/restoration or a new
        # peer. The in-flight authored metadata response still carries the old
        # permitted rights, as may happen when a response trails an update.
        restricted = entity(
            description.peer,
            now[0],
            banned_rights=types.ChatBannedRights(None, send_messages=True),
        )
        app.transport._observe_group(restricted, observed=app.transport.observation())
        assert app.store.groups.get(key).access_revision == revision

    rpc.before_wire = update_rights
    evidence = Evidence()
    with pytest.raises(Refused):
        await app.transport.send(description, evidence)
    assert changed
    assert app.store.groups.get(key).access == "available"
    assert app.store.groups.get(key).access_revision == revision
    assert not rpc.final and not receipts and "exposed" not in evidence.events


async def test_minimal_reply_metadata_does_not_discard_freshly_verified_full_rights(
    tmp_path,
):
    app, now, key, rpc, description, _ = setup(tmp_path)
    revision = app.store.groups.get(key).access_revision

    def minimal_reply_metadata(request):
        if isinstance(request, functions.channels.GetMessagesRequest):
            rpc.metadata = entity(description.peer, now[0], min=True)

    rpc.before_wire = minimal_reply_metadata
    result = await app.transport.send(description, Evidence())
    assert result.pairs == ((description.random_ids[0], 700),)
    assert len(rpc.final) == 1
    assert app.store.groups.get(key).access_revision == revision
    assert rpc.final[0].send_as.user_id == description.account_id
