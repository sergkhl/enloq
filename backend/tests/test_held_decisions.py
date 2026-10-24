"""ADR 49 Q3: the decision request for blocks held under the character
threshold, from the cache only. Authored sources and fake providers."""

import json

import pytest
from enloq.application.conversations import Conversations
from enloq.domain.models import Chat, MediaInfo
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import Store
from enloq.transport.fake import FakeTransport

from backend.tests.test_rebuild import message

KEY = "1/user:42"


@pytest.fixture
def rig(tmp_path):
    now = [2_000_000_000.0]
    registry = AccountRegistry(tmp_path)  # The shipped 200-character threshold.
    store = Store(
        tmp_path / "rewriting.sqlite3",
        account_id=1,
        clock=lambda: now[0],
        shared_path=registry.path,
    )
    store.put_chat(
        Chat(
            account_id=1,
            peer="user:42",
            title="Authored",
            kind="private",
            ai_eligible=True,
        )
    )
    app = Conversations(store, FakeTransport())
    # A worried block a reply would answer (ADR 49 Q2).
    app.decider.emotion, app.decider.useful = "worried", 0.9
    return app, now, registry


def held(app):
    return app.store.rows("SELECT * FROM incoming_deferred")


def decisions(app):
    return [
        packet for role, packet in app.decider.calls if role == "emotion_classification"
    ]


def jobs(app, role):
    return app.store.rows("SELECT * FROM jobs WHERE role=? ORDER BY rowid", (role,))


def cache(app, identity, **body):
    with app.store.transaction() as db:
        db.execute(
            "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
            (f"{KEY}/{identity}", 1, json.dumps(body)),
        )


async def settle(app, rounds=6):
    for _ in range(rounds):
        await app.process_due()


async def test_a_held_block_is_judged_from_its_originals_after_its_quiet(rig):
    app, now, _ = rig
    app.sync.ingest(message(app, 1, text="Are you coming tomorrow?"), live=True)
    assert len(held(app)) == 1 and not jobs(app, "incoming_rewrite")
    now[0] += 29
    await settle(app)
    assert not app.decider.calls
    assert app.emotion.view(KEY).reason == "unavailable"
    now[0] += 1
    await settle(app)
    # One decision from the originals; the rewrite still waits for 200.
    assert len(decisions(app)) == 1
    assert not [c for c in app.engine.calls if c[0] == "incoming_rewrite"]
    [packet] = decisions(app)
    assert set(packet["state"]) == {"conversation"}
    [entry] = packet["state"]["conversation"]
    assert entry == {
        "id": 1,
        "from": "recipient",
        "text": "Are you coming tomorrow?",
        "latest_block": True,
    }
    assert app.rewriting.block(f"{KEY}/1")["state"] == "original"
    assert len(held(app)) == 1
    record = app.emotion.record(KEY)
    assert (record["code"], record["useful"]) == ("worried", True)
    assert app.emotion.view(KEY).code == "worried"
    # Suggestions follow as for any block (ADR 49 Q2), still without a rewrite.
    assert app.suggestions.view(KEY).state == "ready"
    assert [c[0] for c in app.engine.calls] == ["response_suggestions"]
    # Nothing was read from Telegram or prepared as media.
    assert not app.transport.exact_reads and not app.transport.downloads
    # The decided revision is never asked again.
    now[0] += 60
    await settle(app)
    assert len(decisions(app)) == 1


async def test_supporting_context_uses_cached_meanings_only(rig):
    app, now, _ = rig
    image = MediaInfo(mime="image/png", size=10)
    app.sync.ingest(
        message(app, 1, text="", media="image", media_info=image), target=False
    )
    app.sync.ingest(
        message(app, 2, text="", media="image", media_info=image), target=False
    )
    voice = MediaInfo(voice=True, duration=3)
    app.sync.ingest(
        message(app, 3, text="", media="audio", media_info=voice), target=False
    )
    app.sync.ingest(message(app, 4, text="Seen them.", outgoing=True), target=False)
    cache(app, 1, image_context="Authored cached meaning: a red door.")
    cache(app, 3, transcript={"text": "authored cached transcript", "language": "en"})
    app.sync.ingest(message(app, 5, text="So which one?"), live=True)
    now[0] += 31
    await settle(app)
    [packet] = decisions(app)
    entries = {e["id"]: e for e in packet["state"]["conversation"]}
    assert entries[1]["image_meaning"] == "Authored cached meaning: a red door."
    assert entries[2]["attachment"] == "image" and "image_meaning" not in entries[2]
    assert entries[3]["transcript"] == "authored cached transcript"
    assert entries[4]["from"] == "owner"
    assert entries[5]["latest_block"] and not entries[1].get("latest_block")
    assert "data:" not in json.dumps(packet)  # Never raw images.
    assert not app.transport.downloads and not app.transport.exact_reads
    # A later cached meaning changes a used dependency, as for any decision.
    cache(app, 2, image_context="Authored later meaning.")
    assert app.emotion.view(KEY).reason == "changed"


async def test_non_ai_media_only_blocks_queue_nothing(rig):
    app, now, _ = rig
    sticker = MediaInfo(sticker="static", emoji="👍")
    app.sync.ingest(
        message(app, 1, text="", media="sticker", media_info=sticker), live=True
    )
    assert not held(app)
    now[0] += 31
    await settle(app)
    assert not app.decider.calls and not jobs(app, "emotion_classification")


async def test_a_growing_held_block_is_decided_once_for_its_latest_revision(rig):
    app, now, _ = rig
    app.sync.ingest(message(app, 1, text="Hi!"), live=True)
    now[0] += 20
    app.sync.ingest(message(app, 2, text="Are you free tonight?"), live=True)
    now[0] += 10
    await settle(app)
    assert not app.decider.calls  # The quiet restarted with the second message.
    now[0] += 20
    await settle(app)
    [packet] = decisions(app)
    latest = [e["id"] for e in packet["state"]["conversation"] if e.get("latest_block")]
    assert latest == [1, 2]
    assert len(jobs(app, "emotion_classification")) == 1
    # A changed held block is held anew and decided again.
    app.sync.ingest(message(app, 3, text="Or tomorrow?"), live=True)
    now[0] += 31
    await settle(app)
    assert len(decisions(app)) == 2
    latest = [
        e["id"]
        for e in decisions(app)[1]["state"]["conversation"]
        if e.get("latest_block")
    ]
    assert latest == [1, 2, 3]


@pytest.mark.usefixtures("text_rewriting")  # Released by the threshold
async def test_a_released_block_is_classified_after_its_rewrite_without_duplicates(
    rig,
):
    app, now, registry = rig
    app.notifications.unavailable = lambda: True
    app.sync.ingest(message(app, 1, text="Are you coming tomorrow?"), live=True)
    now[0] += 31
    # The held decision is queued, then the threshold admits the block first.
    queued = app.rewriting.next_job()
    assert queued["role"] == "emotion_classification"
    assert json.loads(queued["payload"])["held"] is True
    registry.set_processing_threshold(0)
    job = app.rewriting.next_job()
    assert job["role"] == "incoming_rewrite"
    assert (
        app.store.rows("SELECT state FROM jobs WHERE id=?", (queued["id"],))[0]["state"]
        == "superseded"
    )
    assert "decided" not in json.loads(job["payload"])
    await settle(app)
    # One rewrite, then one classification after it: no held duplicate.
    assert [c[0] for c in app.engine.calls][:1] == ["incoming_rewrite"]
    assert len(decisions(app)) == 1
    assert not json.loads(jobs(app, "emotion_classification")[-1]["payload"]).get(
        "held"
    )
    assert app.emotion.view(KEY).code == "worried"


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
async def test_a_held_block_past_its_lifetime_is_never_classified(rig):
    app, now, _ = rig
    app.sync.ingest(
        message(app, 1, text="Old short question?", date=now[0] - 13 * 3600), live=True
    )
    now[0] += 31
    await settle(app)
    assert len(held(app)) == 1
    assert not app.decider.calls and not jobs(app, "emotion_classification")
    assert json.loads(held(app)[0]["payload"])["decided"] is True


async def test_an_ineligible_chat_holds_nothing_to_decide(rig):
    app, now, _ = rig
    chat = app.store.chat(KEY)
    app.store.put_chat(chat.model_copy(update={"ai_eligible": False}))
    app.sync.ingest(message(app, 1, text="Are you coming?"), live=True)
    now[0] += 31
    await settle(app)
    assert not app.decider.calls and not jobs(app, "emotion_classification")
