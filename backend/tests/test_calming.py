"""ADR 53: calming hostile messages in private chats, on by default. The mood
decision comes first, a frustrated or angry block is rewritten calmly at any
length, and a chat's switch turns it off. Authored sources, fake providers."""

import json

import pytest
from enloq.api.server import create_app
from enloq.application.content_readiness import content_kind
from enloq.application.conversations import Conversations
from enloq.config import Settings
from enloq.domain.models import Chat, Refused
from enloq.inference.base import ProviderRefused
from enloq.inference.fake import FakeEngine
from enloq.inference.guidance import (
    DEFAULTS,
    DEFAULTS_REVISION,
    SARCASM_HINT,
    instructions,
    snapshot,
)
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import Store
from enloq.transport.fake import FakeTransport
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_rebuild import image_bytes, message

KEY = "1/user:42"
# Over the shipped 200-character threshold, which no longer admits text for
# rewriting (ADR 54).
LONG = (
    "I have asked you THREE times about the refund of 240 dollars. If it is not "
    "in my account by Friday the 14th I am filing a complaint with the bank. "
    "Honestly, this is useless; do your job and stop ignoring my messages."
)


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
    app.decider.emotion = "angry"
    # When each rewrite starts, how many decisions were already asked for.
    order = []
    generate = app.engine.generate

    async def recorded(role, packet):
        if role == "incoming_rewrite":
            order.append((role, len(decisions(app))))
        return await generate(role, packet)

    app.engine.generate = recorded
    return app, now, order


def decisions(app):
    return [
        packet for role, packet in app.decider.calls if role == "emotion_classification"
    ]


def rewrites(app):
    return [packet for role, packet in app.engine.calls if role == "incoming_rewrite"]


def jobs(app, role):
    return app.store.rows("SELECT * FROM jobs WHERE role=? ORDER BY rowid", (role,))


def block(app, identity=None):
    blocks = app.stages(KEY)
    if identity is None:
        return blocks[-1]
    return next(b for b in blocks if identity in b["sources"])


async def settle(app, rounds=6):
    for _ in range(rounds):
        await app.process_due()


def test_calming_is_on_by_default_and_a_chat_turns_it_off(rig):
    app, _, _ = rig
    assert app.calming.view(KEY) == {"enabled": True}
    assert app.conversation(KEY)["calming"] is True
    assert app.calming.set(KEY, False) == {"enabled": False}
    assert app.conversation(KEY)["calming"] is False
    assert app.store.rows("SELECT enabled FROM calming")[0]["enabled"] == 0
    # Setting the same value keeps the chat's choice and when it was made.
    changed = app.store.rows("SELECT changed FROM calming")[0]["changed"]
    app.calming.set(KEY, False)
    assert app.store.rows("SELECT changed FROM calming")[0]["changed"] == changed
    assert app.calming.set(KEY, True) == {"enabled": True}
    app.store.put_chat(
        Chat(account_id=1, peer="chat:7", title="Authored group", kind="group")
    )
    with pytest.raises(Refused):
        app.calming.set("1/chat:7", True)
    assert not app.calming.on("1/chat:7")


async def test_an_angry_image_block_is_decided_first_and_calmed_on_its_route(rig):
    app, now, order = rig
    # ADR 53: calmed text has its own route (Muse in the shipped app).
    app.calmer = FakeEngine()
    app.transport.media["user:42", 1] = image_bytes()
    app.sync.ingest(message(app, 1, text=LONG, media="image"), live=True)
    now[0] += 30
    await settle(app)
    # One decision, before the one rewrite, and none after it.
    assert len(decisions(app)) == 1 and not rewrites(app)
    ((role, calmed),) = app.calmer.calls
    assert role == "incoming_rewrite" and calmed["calm"] is True
    first = jobs(app, "emotion_classification")
    assert len(first) == 1 and json.loads(first[0]["payload"])["first"] is True
    assert first[0]["state"] == "done"
    current = block(app)
    assert current["state"] == "ready" and current["calmed"] is True
    view = app.emotion.view(KEY)
    assert view.code == "angry" and view.reason == "available"
    # The decision request carries originals only, never the calming flag.
    assert "calm" not in json.dumps(decisions(app)[0]["state"])


async def test_long_hostile_text_is_held_decided_and_calmed(rig):
    app, now, order = rig
    app.sync.ingest(message(app, 1, text=LONG), live=True)
    assert block(app)["state"] == "original"
    now[0] += 30
    await settle(app)
    # ADR 54: text is held whatever its length, then calmed (ADR 53 Q6).
    assert order == [("incoming_rewrite", 1)] and len(decisions(app)) == 1
    assert rewrites(app)[0]["calm"] is True
    assert block(app)["state"] == "ready" and block(app)["calmed"] is True


async def test_a_sarcastic_block_is_calmed_with_the_decisions_hint(rig):
    app, now, _ = rig
    app.decider.emotion, app.decider.sarcasm = "frustrated", 0.9
    app.sync.ingest(message(app, 1, text=LONG), live=True)
    app.sync.ingest(
        message(app, 2, text="Oh great, 'urgent' again.", outgoing=True), live=True
    )
    app.sync.ingest(
        message(app, 3, text="Love that for us.", date=now[0] + 1), live=True
    )
    now[0] += 31
    await settle(app)
    # Both blocks are held, then calmed: each rewrite is told it is sarcastic.
    first, held = rewrites(app)
    assert first["calm"] and first["sarcastic"] and held["calm"] and held["sarcastic"]
    released = [
        json.loads(j["payload"])
        for j in jobs(app, "incoming_rewrite")
        if "calm" in json.loads(j["payload"])
    ]
    assert len(released) == 2 and all(r["sarcastic"] is True for r in released)
    hinted = instructions("incoming_rewrite", first, snapshot())
    assert SARCASM_HINT in hinted
    assert SARCASM_HINT not in instructions(
        "incoming_rewrite", {"calm": True}, snapshot()
    )


async def test_text_that_is_not_hostile_stays_combined(rig):
    app, now, order = rig
    app.decider.emotion = "worried"
    app.sync.ingest(message(app, 1, text=LONG.lower()), live=True)
    now[0] += 30
    await settle(app)
    # ADR 54: decided from its originals, not rewritten.
    assert not order and len(decisions(app)) == 1
    assert block(app)["state"] == "original" and block(app)["calmed"] is False
    assert app.emotion.view(KEY).code == "worried"


async def test_calming_off_keeps_the_rewrite_first_then_the_decision(rig):
    app, now, order = rig
    app.calming.set(KEY, False)
    app.transport.media["user:42", 1] = image_bytes()
    app.sync.ingest(message(app, 1, text=LONG, media="image"), live=True)
    now[0] += 30
    await settle(app)
    assert order == [("incoming_rewrite", 0)] and len(decisions(app)) == 1
    assert "calm" not in rewrites(app)[0]
    assert block(app)["calmed"] is False
    assert "first" not in json.loads(jobs(app, "emotion_classification")[0]["payload"])
    assert app.emotion.view(KEY).code == "angry"


async def test_a_failed_decision_lets_the_rewrite_run_uncalmed(rig):
    app, now, order = rig
    app.decider.emotion = None  # No answer: the decision fails.
    app.transport.media["user:42", 1] = image_bytes()
    app.sync.ingest(message(app, 1, text=LONG, media="image"), live=True)
    now[0] += 30
    await settle(app)
    assert order[0] == ("incoming_rewrite", 1)
    assert "calm" not in rewrites(app)[0]
    assert block(app)["state"] == "ready" and block(app)["calmed"] is False
    # ADR 30's decision then follows the rewrite, as with calming off.
    assert len(decisions(app)) == 2


async def test_an_image_block_keeps_its_decision_after_the_rewrite_reads_it(rig):
    app, now, _ = rig
    app.transport.media["user:42", 1] = image_bytes()
    app.sync.ingest(message(app, 1, text=LONG, media="image"), live=True)
    now[0] += 30
    await settle(app)
    # The decision came before the image's meaning, so it never saw it.
    assert "image_meaning" not in json.dumps(decisions(app)[0]["state"])
    assert app.media.cached(app.store.message(KEY, 1))["image_context"]
    view = app.emotion.view(KEY)
    assert view.code == "angry" and view.reason == "available"
    assert block(app)["calmed"] is True


async def test_a_short_hostile_block_is_calmed_whatever_its_length(rig):
    app, now, order = rig
    app.sync.ingest(message(app, 1, text="You're useless. Answer me."), live=True)
    assert block(app)["state"] == "original"
    now[0] += 30
    await settle(app)
    # The held block's decision (ADR 49 Q3) releases its one calm rewrite.
    assert len(decisions(app)) == 1 and order == [("incoming_rewrite", 1)]
    assert rewrites(app)[0]["calm"] is True
    (released,) = jobs(app, "incoming_rewrite")
    payload = json.loads(released["payload"])
    assert payload["calm"] == "angry" and payload["class"] == "new"
    assert block(app)["state"] == "ready" and block(app)["calmed"] is True
    assert app.emotion.view(KEY).code == "angry"
    assert not app.store.rows("SELECT * FROM incoming_deferred")


async def test_a_refused_calmed_rewrite_retries_calmly_without_a_new_decision(rig):
    app, now, _ = rig
    app.decider.sarcasm = 0.9
    app.calmer = FakeEngine()
    calmed = app.calmer.generate

    async def busy(role, packet):
        raise ProviderRefused("Authored upstream rate limit", retry_after=20)

    app.calmer.generate = busy
    app.sync.ingest(message(app, 1, text="You're useless. Answer me."), live=True)
    now[0] += 30
    await settle(app)
    assert block(app)["state"] == "queued"
    (retry,) = [
        json.loads(j["payload"])
        for j in jobs(app, "incoming_rewrite")
        if j["state"] == "queued"
    ]
    # The retry is still the release: calm, sarcastic, and counted.
    assert retry["calm"] == "angry" and retry["sarcastic"] is True
    assert retry["refusals"] == 1
    app.calmer.generate = calmed
    now[0] += 20
    await settle(app)
    assert block(app)["state"] == "ready" and block(app)["calmed"] is True
    assert len(decisions(app)) == 1
    ((_, packet),) = app.calmer.calls
    assert packet["calm"] and packet["sarcastic"]


async def test_a_calmed_rewrite_refused_three_times_fails_with_retry(rig):
    app, now, _ = rig

    async def busy(role, packet):
        raise ProviderRefused("Authored upstream rate limit", retry_after=20)

    app.calmer = FakeEngine()
    app.calmer.generate = busy
    app.sync.ingest(message(app, 1, text="You're useless. Answer me."), live=True)
    now[0] += 30
    await settle(app)
    for _ in range(3):
        now[0] += 20
        await settle(app)
    assert block(app)["state"] == "failed"
    assert "use Retry" in block(app)["error"]
    assert len(decisions(app)) == 1


async def test_a_short_block_that_is_not_hostile_stays_held(rig):
    app, now, _ = rig
    app.decider.emotion = "neutral"
    app.sync.ingest(message(app, 1, text="See you at six."), live=True)
    now[0] += 30
    await settle(app)
    assert len(decisions(app)) == 1 and not rewrites(app)
    assert block(app)["state"] == "original" and not block(app)["deciding"]


async def test_a_held_blocks_readiness_waits_for_its_decision(rig):
    app, now, _ = rig
    app.decider.emotion = "neutral"
    app.sync.ingest(message(app, 1, text="See you at six."), live=True)
    now[0] += 30
    assert block(app)["deciding"]
    assert content_kind([block(app)]) is None
    # Without AI the originals are what can be read.
    assert content_kind([block(app)], unavailable=True) == "original"
    await settle(app)
    assert content_kind([block(app)]) == "original"
    app.calming.set(KEY, False)
    app.sync.ingest(message(app, 2, text="Or seven.", date=now[0]), live=True)
    assert not block(app)["deciding"]


async def test_an_older_held_block_is_decided_for_its_calming_only(rig):
    app, now, order = rig
    app.sync.ingest(message(app, 1, text="You idiot."), live=True)
    app.sync.ingest(
        message(app, 2, text="Authored reply.", outgoing=True, date=now[0] + 5),
        live=True,
    )
    app.decider.emotion = "neutral"
    app.sync.ingest(message(app, 3, text="Fine, call me.", date=now[0] + 10), live=True)
    app.decider.emotion = "angry"
    now[0] += 31
    await settle(app)
    # The first block is no longer the chat's current source: its decision
    # settles its calming only, and nothing shows it.
    calming = [
        j
        for j in jobs(app, "emotion_classification")
        if json.loads(j["payload"]).get("calming")
    ]
    assert len(calming) == 1 and calming[0]["state"] == "done"
    assert block(app, 1)["calmed"] is True
    assert rewrites(app)[0]["messages"][0]["id"] == 1
    assert app.emotion.record(KEY)["sources"] == [3]


async def test_a_held_block_past_its_lifetime_is_decided_for_calming_only(rig):
    app, now, _ = rig
    app.sync.ingest(
        message(app, 1, text="You idiot.", date=now[0] - 13 * 3600), live=True
    )
    now[0] += 31
    await settle(app)
    # Too old for the header (ADR 49 Q3), it is still decided for calming.
    (decision,) = jobs(app, "emotion_classification")
    assert json.loads(decision["payload"])["calming"] is True
    assert block(app)["calmed"] is True and app.emotion.record(KEY) is None


async def test_switching_off_keeps_what_was_calmed(rig):
    app, now, _ = rig
    app.sync.ingest(message(app, 1, text=LONG), live=True)
    now[0] += 30
    await settle(app)
    app.calming.set(KEY, False)
    assert block(app)["calmed"] is True
    # A changed source is processed anew, in the chat's current state: with
    # calming off, text stays combined (ADR 54).
    app.sync.ingest(message(app, 1, text=LONG + " Thanks.", revision=2), live=True)
    assert block(app)["calmed"] is False
    now[0] += 30
    await settle(app)
    assert block(app)["state"] == "original" and block(app)["calmed"] is False


def test_calming_guidance_frames_only_blocks_being_calmed():
    guidance = snapshot()
    assert DEFAULTS_REVISION == 15
    assert "calming" in DEFAULTS
    plain = instructions("incoming_rewrite", {}, guidance)
    calm = instructions("incoming_rewrite", {"calm": True}, guidance)
    assert DEFAULTS["calming"] not in plain
    assert calm.startswith(plain) and DEFAULTS["calming"] in calm


def test_the_calming_switch_is_strict_and_private_only(tmp_path):
    settings = Settings(data=tmp_path)
    server = create_app(settings)
    app = server.state.accounts.get(1)
    app.store.put_chat(
        Chat(account_id=1, peer="user:42", title="Authored", kind="private")
    )
    app.store.put_chat(
        Chat(account_id=1, peer="chat:7", title="Authored group", kind="group")
    )
    origin = {"Origin": "http://127.0.0.1:8765"}
    route = "/api/accounts/1/chats/user:42/calming"
    with TestClient(server, base_url=origin["Origin"]) as client:
        assert client.get(route).status_code == 401
        trust_browser(client, settings.data)
        assert client.get(route).json() == {"enabled": True}
        for coerced in (0, "false", None):
            refused = client.post(route, json={"enabled": coerced}, headers=origin)
            assert refused.status_code == 422
        saved = client.post(route, json={"enabled": False}, headers=origin)
        assert saved.status_code == 200 and saved.json() == {"enabled": False}
        assert client.get(route).json() == {"enabled": False}
        group = "/api/accounts/1/chats/chat:7/calming"
        assert client.get(group).status_code == 400
