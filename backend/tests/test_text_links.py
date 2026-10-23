from datetime import UTC, datetime

from enloq.application.context import projection
from enloq.transport.telethon import PersonalTransport
from telethon import types

from backend.tests.test_rebuild import fixture


async def test_native_named_links_preserve_utf16_destinations_and_edits(tmp_path):
    app = fixture(tmp_path)
    transport = object.__new__(PersonalTransport)
    transport.account_id, transport.store = 1, app.store
    native = types.Message(
        id=1,
        peer_id=types.PeerUser(42),
        date=datetime.fromtimestamp(app.store.clock(), UTC),
        message="😀 Open the plan",
        entities=[
            types.MessageEntityTextUrl(12, 4, "https://example.test/real?x=1&y=2"),
            types.MessageEntityTextUrl(0, 1, "https://broken-boundary.test"),
            types.MessageEntityTextUrl(200, 3, "https://out-of-bounds.test"),
        ],
    )
    normalized = await transport.normalize(native)
    assert len(normalized.links) == 1
    assert normalized.links[0].offset == 12
    app.sync.ingest(normalized)
    restarted = fixture(tmp_path)
    saved = restarted.store.message("1/user:42", 1)
    assert saved.links == normalized.links
    assert projection(saved)["links"][0]["url"] == "https://example.test/real?x=1&y=2"
    native.entities = [
        types.MessageEntityTextUrl(12, 4, "https://example.test/changed")
    ]
    native.edit_date = datetime.fromtimestamp(app.store.clock() + 1, UTC)
    app.sync.ingest(await transport.normalize(native))
    assert app.store.message("1/user:42", 1).links[0].url.endswith("changed")
