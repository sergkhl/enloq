import asyncio
from types import SimpleNamespace

import pytest
from enloq.api.server import create_app
from enloq.application.context import projection
from enloq.config import Settings
from enloq.domain.models import Refused
from fastapi.testclient import TestClient
from telethon.tl import types

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import fixture, message
from backend.tests.test_transport import entityless_transport, native_message


@pytest.mark.parametrize("outgoing", [False, True])
async def test_native_forward_attribution_uses_only_supplied_names(outgoing):
    values = [native_message(i, types.PeerUser(42), out=outgoing) for i in range(1, 6)]
    for native in values[1:]:
        native.fwd_from = types.MessageFwdHeader(date=native.date)
    values[1]._forward = SimpleNamespace(
        sender=types.User(id=7, first_name="Anna", last_name="Example"), chat=None
    )
    values[2]._forward = SimpleNamespace(
        sender=None,
        chat=types.Channel(
            id=8,
            title="Sample channel",
            photo=types.ChatPhotoEmpty(),
            date=values[2].date,
        ),
    )
    values[3].fwd_from.from_name = "Hidden profile name"
    actual = await entityless_transport().normalize_page(values)
    assert [(m.forwarded, m.forwarded_from) for m in actual] == [
        (False, None),
        (True, "Anna Example"),
        (True, "Sample channel"),
        (True, "Hidden profile name"),
        (True, None),
    ]
    assert all(m.outgoing == outgoing for m in actual)


async def test_cached_attribution_repair_preserves_content_versions_and_ai(tmp_path):
    app = fixture(tmp_path)
    source = message(app)
    app.sync.ingest(source)
    app.sync.ingest(source.model_copy(update={"revision": 2, "text": "Latest words"}))
    current = app.store.message("1/user:42", 1)
    app.store.mutate_draft("1/user:42", 0, lambda d: d.update(text="Unsent draft"))
    before = {
        table: app.store.rows(f"SELECT * FROM {table}")
        for table in (
            "blocks",
            "jobs",
            "derivatives",
            "drafts",
            "message_versions",
            "memory_entries",
        )
    }
    app.transport.messages = [
        source.model_copy(
            update={
                "revision": 3,
                "text": "Do not import this content during metadata repair",
                "forwarded": True,
                "forwarded_from": "Sample source",
            }
        )
    ]
    assert await app.forwarding.refresh("1/user:42", [1]) == [
        {
            "message_id": 1,
            "forwarded": True,
            "forwarded_from": "Sample source",
            "forward_date": None,
            "forward_signature": None,
            "imported": None,
            "forward_read": True,
        },
    ]
    after = app.store.message("1/user:42", 1)
    assert after.text == current.text and after.revision == current.revision

    # Emotion extraction consumes attribution on future calls. Learning it
    # still changes no native content, queued work, drafts or cached results.
    def native(m):
        return {
            k: v
            for k, v in projection(m).items()
            if k not in {"forwarded", "forwarded_from"}
        }

    assert native(after) == native(current)
    assert projection(after)["forwarded"] is True
    assert {
        table: app.store.rows(f"SELECT * FROM {table}") for table in before
    } == before
    assert not app.transport.downloads and not app.transport.deliveries
    reopened = fixture(tmp_path)
    assert (await reopened.forwarding.refresh("1/user:42", [1]))[0]["forwarded"]
    assert not reopened.transport.exact_reads


async def test_repair_is_exact_cached_scoped_and_does_not_guess_missing_origins(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    for identity, attrs in [
        (1, {}),
        (2, {}),
        (3, {"deleted": True}),
        (4, {"date": now[0] - 31 * 86400}),
        (5, {"forwarded": False}),
        (6, {}),
    ]:
        app.sync.ingest(message(app, identity, **attrs), target=False)
    app.transport.messages = [
        message(app, 1, forwarded=True, forwarded_from="Wrong account").model_copy(
            update={"account_id": 2}
        ),
        message(app, 2, forwarded=True).model_copy(update={"peer": "user:43"}),
        message(app, 3, forwarded=True),
        message(app, 6, forwarded=True),
    ]

    # Return even unrequested/wrong-peer results to exercise the receiver guard.
    async def exact(peer, identities):
        app.transport.exact_reads.append((peer, tuple(identities)))
        return app.transport.messages

    app.transport.exact_many = exact
    result = await app.forwarding.refresh("1/user:42", [1, 2, 3, 4, 5, 99])
    assert app.transport.exact_reads == [("user:42", (1, 2))]
    assert [m["forwarded"] for m in result] == [None, None, None, None, False]
    assert app.store.message("1/user:42", 6).forwarded is None
    await app.forwarding.refresh("1/user:42", [1, 2])
    assert len(app.transport.exact_reads) == 1
    now[0] += 61
    app.transport.messages = [
        message(app, 1, forwarded=False),
        message(app, 2, forwarded=True),
    ]
    await app.forwarding.refresh("1/user:42", [1, 2])
    assert app.store.message("1/user:42", 1).forwarded is False
    assert app.store.message("1/user:42", 2).forwarded is True


async def test_concurrent_tabs_share_read_and_failure_can_retry(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.sync.ingest(message(app), target=False)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def exact(peer, identities):
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        if calls == 1:
            raise ConnectionError("Authored offline failure")
        return [message(app, forwarded=True)]

    app.transport.exact_many = exact
    first = asyncio.create_task(app.forwarding.refresh("1/user:42", [1]))
    await entered.wait()
    second = asyncio.create_task(app.forwarding.refresh("1/user:42", [1]))
    release.set()
    values = await asyncio.gather(first, second)
    assert calls == 1 and all(v[0]["forwarded"] is None for v in values)
    now[0] += 61
    assert (await app.forwarding.refresh("1/user:42", [1]))[0]["forwarded"]
    assert calls == 2


async def test_repair_does_not_overwrite_a_live_change_or_resurrect_removed_data(
    tmp_path,
):
    app = fixture(tmp_path)
    first, second = message(app, 1), message(app, 2)
    app.sync.ingest(first, target=False)
    app.sync.ingest(second, target=False)

    async def exact(peer, identities):
        app.sync.ingest(
            first.model_copy(
                update={
                    "revision": 2,
                    "text": "Live edit",
                    "forwarded": True,
                    "forwarded_from": "Live source",
                }
            ),
            target=False,
        )
        with app.store.transaction() as db:
            db.execute("DELETE FROM messages WHERE key=?", (second.key,))
        return [
            first.model_copy(update={"forwarded": False}),
            second.model_copy(update={"forwarded": True}),
        ]

    app.transport.exact_many = exact
    result = await app.forwarding.refresh("1/user:42", [1, 2])
    assert [
        {k: m[k] for k in ("message_id", "forwarded", "forwarded_from")} for m in result
    ] == [{"message_id": 1, "forwarded": True, "forwarded_from": "Live source"}]
    assert app.store.message("1/user:42", 1).text == "Live edit"
    assert app.store.message("1/user:42", 2) is None


async def test_maximum_batch_and_unknown_metadata_never_clear_known_attribution(
    tmp_path,
):
    app = fixture(tmp_path)
    for identity in range(1, 102):
        app.sync.ingest(message(app, identity), target=False)
    with pytest.raises(Refused):
        await app.forwarding.refresh("1/user:42", list(range(1, 102)))
    app.transport.messages = [message(app, i, forwarded=False) for i in range(1, 101)]
    await app.forwarding.refresh("1/user:42", list(range(1, 101)))
    assert len(app.transport.exact_reads[0][1]) == 100
    source = message(app, 101, forwarded=True, forwarded_from="Known source")
    app.sync.ingest(source, target=False)
    app.sync.ingest(
        source.model_copy(
            update={"revision": 2, "text": "Edited words", "forwarded_from": None}
        ),
        target=False,
    )
    app.sync.ingest(message(app, 101, revision=2, text="Edited words"), target=False)
    current = app.store.message("1/user:42", 101)
    assert current.forwarded and current.forwarded_from == "Known source"


def test_forwarding_api_requires_trust_and_bounds_cached_id_requests(tmp_path):
    app = create_app(Settings(data=tmp_path))
    base = "/api/accounts/1/chats/user:101/originals/forwarding"
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        assert client.post(base, json={"identities": [1]}).status_code == 403
        trust_browser(client, tmp_path)
        for identities in ([], [0], list(range(1, 102))):
            assert client.post(base, json={"identities": identities}).status_code == 422
        assert client.post(base, json={"identities": [999999]}).json() == []
        assert not app.state.accounts.get(1).transport.exact_reads


async def test_forward_header_details_are_captured_without_resolving_peers():
    from datetime import UTC, datetime

    values = [native_message(i, types.PeerUser(42)) for i in range(1, 5)]
    original = datetime(2024, 3, 4, 15, 4, tzinfo=UTC)
    values[1].fwd_from = types.MessageFwdHeader(
        date=original, from_name="Authored channel", post_author=" Authored author "
    )
    values[2].fwd_from = types.MessageFwdHeader(
        date=original, from_name="Authored import", imported=True
    )
    values[3].fwd_from = types.MessageFwdHeader(
        date=original, from_id=types.PeerChannel(8), channel_post=11
    )
    actual = await entityless_transport().normalize_page(values)
    assert [
        (m.forwarded, m.forwarded_from, m.forward_date, m.forward_signature, m.imported)
        for m in actual
    ] == [
        (False, None, None, None, None),
        (True, "Authored channel", original.timestamp(), "Authored author", False),
        (True, "Authored import", original.timestamp(), None, True),
        # A short live update: the header arrives without the source entity.
        (True, None, original.timestamp(), None, False),
    ]
    assert not any(m.forward_read for m in actual)


def test_later_updates_keep_captured_details_and_metadata_only(tmp_path):
    app = fixture(tmp_path)
    detailed = message(
        app,
        forwarded=True,
        forwarded_from="Authored channel",
        forward_date=1_700_000_000.0,
        forward_signature="Authored author",
        imported=False,
    )
    app.sync.ingest(detailed)
    blocks = app.store.rows("SELECT * FROM blocks")
    jobs = app.store.rows("SELECT * FROM jobs")
    # An update carrying less (legacy or entityless) never erases details.
    app.sync.ingest(message(app, forwarded=True))
    current = app.store.message("1/user:42", 1)
    assert (
        current.forwarded_from,
        current.forward_date,
        current.forward_signature,
    ) == (
        "Authored channel",
        1_700_000_000.0,
        "Authored author",
    )
    assert current.revision == detailed.revision
    assert app.store.rows("SELECT * FROM blocks") == blocks
    assert app.store.rows("SELECT * FROM jobs") == jobs


async def test_repair_fills_confirmed_forwards_missing_details_once(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    header = {"forward_date": now[0] - 86400, "imported": False}
    for identity, attrs in [
        (1, {"forwarded": True, "forwarded_from": "Known"}),  # legacy confirmed
        (2, {"forwarded": True, **header}),  # nameless, never read
        (3, {"forwarded": True, "forwarded_from": "Complete", **header}),
        (4, {"forwarded": True}),  # its read will fail
    ]:
        app.sync.ingest(message(app, identity, **attrs), target=False)
    before = {
        table: app.store.rows(f"SELECT * FROM {table}")
        for table in ("blocks", "jobs", "derivatives", "message_versions")
    }
    reads = []

    async def exact(peer, identities):
        reads.append(tuple(identities))
        if 4 in identities and len(reads) == 1:
            raise ConnectionError("Authored offline failure")
        return [
            message(
                app,
                1,
                forwarded=True,
                forwarded_from="Renamed",
                forward_date=now[0] - 7200,
                forward_signature="Authored author",
                imported=True,
            ),
            # A successful read that still lacks a name is final.
            message(app, 2, forwarded=True, **header),
        ]

    app.transport.exact_many = exact
    result = await app.forwarding.refresh("1/user:42", [1, 2, 3, 4])
    assert reads == [(1, 2, 4)]
    # The failed batch keeps everything retryable after 60 seconds.
    assert [m["forward_read"] for m in result] == [False, False, False, False]
    now[0] += 61
    result = {
        m["message_id"]: m
        for m in await app.forwarding.refresh("1/user:42", [1, 2, 3, 4])
    }
    assert reads[-1] == (1, 2, 4)
    # Known values are never replaced; missing details are filled in.
    assert (
        result[1]["forwarded_from"],
        result[1]["forward_date"],
        result[1]["forward_signature"],
        result[1]["imported"],
        result[1]["forward_read"],
    ) == ("Known", now[0] - 7200, "Authored author", True, True)
    assert result[2]["forwarded_from"] is None and result[2]["forward_read"]
    assert not result[4]["forward_read"]  # Missing from the read: retry later.
    now[0] += 61
    await app.forwarding.refresh("1/user:42", [1, 2, 3, 4])
    assert reads[-1] == (4,)
    assert {
        table: app.store.rows(f"SELECT * FROM {table}") for table in before
    } == before
    assert not app.transport.downloads


async def test_live_nameless_forward_gets_one_intake_read_without_delaying_work(
    tmp_path,
):
    app = fixture(tmp_path)
    reads = []

    async def exact(peer, identities):
        reads.append((peer, tuple(identities)))
        return [
            message(
                app,
                identities[0],
                forwarded=True,
                forwarded_from="Authored channel",
                forward_date=app.store.clock() - 3600,
                imported=False,
            )
        ]

    app.transport.exact_many = exact
    app.sync.ingest(message(app, 1, forwarded=True), live=True)
    job = app.store.rows("SELECT * FROM jobs WHERE role='incoming_rewrite'")
    await asyncio.gather(*app.tasks)
    assert reads == [("user:42", (1,))]
    learned = app.store.message("1/user:42", 1)
    assert learned.forwarded_from == "Authored channel" and learned.forward_read
    # The fill-in leaves the quiet job and its due time untouched.
    assert app.store.rows("SELECT * FROM jobs WHERE role='incoming_rewrite'") == job
    # Named, outgoing, already-read and unadmitted forwards get no intake read.
    app.sync.ingest(message(app, 2, forwarded=True, forwarded_from="Named"), live=True)
    app.sync.ingest(message(app, 3, forwarded=True, outgoing=True), live=True)
    app.sync.ingest(message(app, 4, forwarded=True), target=False)
    await asyncio.gather(*app.tasks)
    assert reads == [("user:42", (1,))]


async def test_intake_read_failure_and_processing_off_never_delay_or_read(tmp_path):
    from enloq.domain.models import ProcessingPaused

    app = fixture(tmp_path)

    async def failing(peer, identities):
        raise ConnectionError("Authored offline failure")

    app.transport.exact_many = failing
    app.sync.ingest(message(app, 1, forwarded=True), live=True)
    due = app.store.rows("SELECT due FROM jobs WHERE role='incoming_rewrite'")
    await asyncio.gather(*app.tasks)
    assert app.store.rows("SELECT due FROM jobs WHERE role='incoming_rewrite'") == due
    assert not app.store.message("1/user:42", 1).forward_read

    def paused():
        raise ProcessingPaused("Authored processing off")

    app.admit_ai = paused
    reads = []

    async def exact(peer, identities):
        reads.append(identities)
        return []

    app.transport.exact_many = exact
    app.sync.ingest(message(app, 2, forwarded=True), live=True)
    await asyncio.gather(*app.tasks)
    assert reads == []


async def test_fill_in_keeps_recorded_work_current_and_conflicts_do_not(tmp_path):
    from enloq.application.context import current, incoming
    from enloq.domain.source_revision import dependency_fingerprint

    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1, forwarded=True))
    block = app.store.rows("SELECT * FROM blocks")[0]
    packet = await incoming(app, block)
    assert current(app.store, packet)
    app.store.learn_forwarding(
        message(
            app,
            1,
            forwarded=True,
            forwarded_from="Authored channel",
            forward_date=1_700_000_000.0,
            forward_signature="Authored author",
            imported=False,
        )
    )
    assert current(app.store, packet)
    # The header details are display/model inputs, never digest inputs.
    stored = app.store.message("1/user:42", 1)
    assert dependency_fingerprint(
        stored.model_copy(update={"forward_date": None, "forward_signature": None})
    ) == dependency_fingerprint(stored)
    # Any name fills in the recorded unknown name; a conflicting known value,
    # here the recorded forward state, still makes the work non-current.
    with app.store.transaction() as db:
        db.execute(
            "UPDATE messages SET body=json_set(body,'$.forwarded',json('false')) WHERE key=?",
            ("1/user:42/1",),
        )
    assert not current(app.store, packet)


def test_transient_digest_proves_equality_after_attribution_fill_in(tmp_path):
    from enloq.domain.source_revision import dependency_fingerprint, proves

    app = fixture(tmp_path)
    seen = message(app, 7, forwarded=True)
    digest = dependency_fingerprint(seen)
    named = seen.model_copy(update={"forwarded_from": "Authored channel"})
    assert dependency_fingerprint(named) != digest
    assert proves(named, digest, [True, None])
    assert not proves(named, digest, [True, "Another"])
    assert not proves(
        named.model_copy(update={"text": "Changed"}), digest, [True, None]
    )
