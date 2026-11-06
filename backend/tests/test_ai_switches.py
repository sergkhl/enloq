"""ADR 62 Phase 3: each private chat's five switches are Off by default (Q2)
and govern only automatic work (Q3). A block's decision asks what the On
switches use (Q5); memory reads the chat itself (Q6); reminders always alert
(Q7); a switch turned On works on the chat's latest block (Q8); a chat whose
switches are Off settles as its originals (Q14); and with Voice and images Off
nothing is transcribed or read (Q20). Authored sources and offline stand-ins
only."""

import asyncio
import json

import pytest
from enloq.api.server import create_app
from enloq.application.content_readiness import content_kind
from enloq.config import Settings
from enloq.domain.models import Chat, Refused
from enloq.domain.reminders import LEAD
from enloq.inference.base import InvalidOutput
from enloq.inference.decider import validate
from enloq.inference.fake import choice
from enloq.inference.guidance import decision_questions
from enloq.storage.store import WINDOW
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from backend.tests.test_media_cache import voice
from backend.tests.test_rebuild import fixture, meanings, message, picture
from backend.tests.test_reminders import rule, stamp
from backend.tests.test_retained_groups import group

KEY = "1/user:42"
SWITCHES = ("suggestions", "emotion", "calming", "media", "remember")

pytestmark = pytest.mark.usefixtures("shipped_defaults")


class Speech:
    def __init__(self):
        self.calls = []

    async def transcribe(self, path):
        self.calls.append(path.name)
        return {"text": "Authored spoken words.", "language": "en"}


def rig(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.media.transcriber = Speech()
    rewrite = app.engine.generate

    async def answered(role, packet):
        value = await rewrite(role, packet)
        if role == "incoming_rewrite" and packet.get("images"):
            value = {**value, "image_context": meanings(packet)}
        return value

    app.engine.generate = answered
    return app, now


def note(app, identity, **kwargs):
    """A voice note with authored audio a transcription could read."""
    app.transport.media["user:42", identity] = b"OggS" + bytes([identity]) * 64
    return voice(app, identity, **kwargs)


def latest(packet):
    """The decision state's latest block: what its request decided."""
    return [m for m in packet["state"]["conversation"] if m.get("latest_block")]


def decisions(app):
    return [
        packet for role, packet in app.decider.calls if role == "emotion_classification"
    ]


def rewrites(app):
    return [packet for role, packet in app.engine.calls if role == "incoming_rewrite"]


def suggestion_sets(app):
    return [role for role, _ in app.engine.calls if role == "response_suggestions"]


async def settle(app, now, seconds=31, rounds=8):
    """Past the blocks' 30 quiet seconds, everything due runs."""
    now[0] += seconds
    for _ in range(rounds):
        await app.process_due()
    while app.tasks:
        await asyncio.gather(*app.tasks)


def switches(app, key=KEY):
    ai = app.conversation(key)["ai"]
    return {name: ai[name] for name in SWITCHES}


def test_every_switch_is_off_without_the_chats_own_choice(tmp_path):
    app, _ = rig(tmp_path)
    assert switches(app) == dict.fromkeys(SWITCHES, False)
    ai = app.conversation(KEY)["ai"]
    # The robot shows its outline: nothing runs in the chat (Q14).
    assert ai["account"] is True and ai["running"] is False
    assert app.emotion_switch.view(KEY) == {"enabled": False}
    assert app.media_switch.view(KEY) == {"enabled": False}
    # A chat's own choice is kept, and AI runs there while it is On.
    assert app.media_switch.set(KEY, True) == {"enabled": True}
    assert app.conversation(KEY)["ai"]["running"] is True
    assert app.media_switch.set(KEY, False) == {"enabled": False}
    assert app.conversation(KEY)["ai"]["running"] is False
    # Automatic suggestions runs only while the server-wide switch permits it.
    app.suggestions.set_settings(KEY, True, "friendly")
    app.suggestions.enabled = lambda: False
    assert app.conversation(KEY)["ai"]["running"] is False
    app.suggestions.enabled = lambda: True
    assert app.conversation(KEY)["ai"]["running"] is True
    # Account AI processing stays the master over the five.
    app.account_processing_enabled = lambda: False
    assert app.conversation(KEY)["ai"]["running"] is False


def test_groups_and_saved_messages_have_no_chat_switches(tmp_path):
    app, _ = rig(tmp_path)
    key = group(app)
    for switch in (app.emotion_switch, app.media_switch):
        assert switch.on(key) is False
        with pytest.raises(Refused, match="private chats only"):
            switch.set(key, True)
        with pytest.raises(Refused, match="private chats only"):
            switch.view(key)
        assert switch.on(app.store.saved_key) is False


def test_the_switch_routes_are_strict_and_private_only(tmp_path):
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
    with TestClient(server, base_url=origin["Origin"]) as client:
        trust_browser(client, settings.data)
        for suffix in ("emotion", "voice-and-images"):
            route = f"/api/accounts/1/chats/user:42/{suffix}"
            assert client.get(route).json() == {"enabled": False}
            for coerced in (1, "true", None):
                refused = client.post(route, json={"enabled": coerced}, headers=origin)
                assert refused.status_code == 422
            saved = client.post(route, json={"enabled": True}, headers=origin)
            assert saved.status_code == 200 and saved.json() == {"enabled": True}
            assert client.get(route).json() == {"enabled": True}
            group_route = f"/api/accounts/1/chats/chat:7/{suffix}"
            assert client.get(group_route).status_code == 400
        snapshot = client.get("/api/accounts/1/chats/user:42").json()
        assert snapshot["ai"]["emotion"] is True and snapshot["ai"]["media"] is True


def test_a_members_chats_start_with_every_switch_off(tmp_path):
    """A member's account starts every private chat Off as the owner's does,
    and a choice is that chat's own (Q2). test_members checks that every
    account route, the switches' included, refuses another member's account."""
    owner = fixture(tmp_path)
    member = fixture(tmp_path, account=2)
    key = "2/user:42"
    assert switches(member, key) == dict.fromkeys(SWITCHES, False)
    assert member.media_switch.set(key, True) == {"enabled": True}
    assert member.media_switch.on(key) and not owner.media_switch.on(KEY)


def test_a_decision_asks_and_validates_only_the_on_switches_questions():
    assert set(decision_questions("emotion_classification")) == {
        "emotion",
        "suggestions_useful",
        "sarcasm",
        "hostility",
    }
    packet = {"asks": ["emotion"]}
    assert set(decision_questions("emotion_classification", packet=packet)) == {
        "emotion"
    }
    result = validate(
        "emotion_classification", {"emotion": choice("worried", 0.9)}, packet
    )
    assert result.code == "worried" and result.usefulness is None
    assert result.hostility is None and not result.hostile
    # An answer to a question not asked, or a missing asked one, is invalid.
    with pytest.raises(InvalidOutput):
        validate(
            "emotion_classification",
            {
                "emotion": choice("worried", 0.9),
                "hostility": {"type": "noul", "noul": 0.9},
            },
            packet,
        )
    with pytest.raises(InvalidOutput):
        validate("emotion_classification", {}, {"asks": ["suggestions_useful"]})
    useful = validate(
        "emotion_classification",
        {"suggestions_useful": {"type": "noul", "noul": 0.8}},
        {"asks": ["suggestions_useful"]},
    )
    assert useful.decision is None and useful.code is None
    assert useful.suggestions_useful and useful.usefulness == 0.8
    # A reaction always asks its emotion; usefulness only when asked.
    reaction = validate(
        "reaction_assessment", {"emotion": choice("sad", 0.9)}, {"asks": ["emotion"]}
    )
    assert reaction.emotion == "sad" and reaction.usefulness is None


async def test_a_chat_whose_switches_are_off_settles_as_its_originals(tmp_path):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1, text="Authored plain text."))
    app.sync.ingest(message(app, 2, text="An authored reply.", outgoing=True))
    app.sync.ingest(picture(app, 3, text="An authored caption."))
    await settle(app, now)
    # No decision, no rewrite, no transcription and no image read (Q14, Q20).
    assert app.decider.calls == [] and app.engine.calls == []
    assert app.media.transcriber.calls == []
    assert not app.store.rows("SELECT 1 FROM derivatives")
    blocks = app.conversation(KEY)["blocks"]
    assert [b["state"] for b in blocks] == ["original", "original"]
    # With no stage, each is known as its originals after its quiet seconds.
    assert all(b["stage"] is None for b in blocks)
    assert content_kind(blocks) == "original"
    assert app.emotion.view(KEY).reason == "off"
    assert not app.store.rows(
        "SELECT 1 FROM jobs WHERE state IN ('queued','claimed','running')"
    )


@pytest.mark.parametrize(
    "switch,asks",
    [
        ("emotion", ["emotion"]),
        ("suggestions", ["suggestions_useful"]),
        ("calming", ["hostility", "sarcasm"]),
    ],
)
async def test_one_switch_on_asks_only_its_questions(tmp_path, switch, asks):
    app, now = rig(tmp_path)
    if switch == "emotion":
        app.emotion_switch.set(KEY, True)
    elif switch == "suggestions":
        app.suggestions.set_settings(KEY, True, "friendly")
    else:
        app.calming.set(KEY, True)
    app.sync.ingest(message(app, 1, text="Authored plain text."))
    await settle(app, now)
    (packet,) = decisions(app)
    assert packet["asks"] == asks
    assert app.emotion.record(KEY)["asks"] == asks
    # Text stays combined: only softening rewrites it.
    assert rewrites(app) == []


async def test_the_server_wide_switch_off_drops_usefulness_from_the_decision(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.suggestions.set_settings(KEY, True, "friendly")
    app.emotion_switch.set(KEY, True)
    app.suggestions.enabled = lambda: False
    app.sync.ingest(message(app, 1, text="Authored plain text."))
    await settle(app, now)
    assert [p["asks"] for p in decisions(app)] == [["emotion"]]


async def test_with_emotion_off_usefulness_alone_triggers_suggestions(tmp_path):
    app, now = rig(tmp_path)
    app.suggestions.set_settings(KEY, True, "friendly")
    # An authored emotion is never asked while Emotion is Off.
    app.decider.emotion, app.decider.useful = "angry", 0.9
    app.sync.ingest(message(app, 1, text="Authored question for a reply."))
    await settle(app, now)
    assert [p["asks"] for p in decisions(app)] == [["suggestions_useful"]]
    assert app.emotion.record(KEY)["code"] is None
    assert len(suggestion_sets(app)) == 1
    assert app.suggestions.view(KEY).state == "ready"
    view = app.emotion.view(KEY)
    assert (view.code, view.reason) == (None, "off")


async def test_with_emotion_off_a_low_usefulness_prepares_no_set(tmp_path):
    app, now = rig(tmp_path)
    app.suggestions.set_settings(KEY, True, "friendly")
    app.decider.emotion, app.decider.useful = "angry", 0.5
    app.sync.ingest(message(app, 1, text="Authored statement."))
    await settle(app, now)
    assert len(decisions(app)) == 1 and suggestion_sets(app) == []


async def test_emotion_off_clears_the_emoji_and_a_reaction_is_not_assessed(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.emotion_switch.set(KEY, True)
    app.decider.emotion = "worried"
    app.sync.ingest(message(app, 1, text="Authored worry."))
    await settle(app, now)
    assert app.emotion.view(KEY).code == "worried"
    app.emotion_switch.set(KEY, False)
    # The emoji goes at once; the decision's record stays (Q5, Q8).
    view = app.emotion.view(KEY)
    assert (view.code, view.reason) == (None, "off")
    assert app.emotion.record(KEY)["code"] == "worried"
    app.assessment.schedule(KEY)
    assert not app.store.rows("SELECT 1 FROM jobs WHERE role='reaction_assessment'")


async def test_memory_reads_the_chat_while_remember_this_chat_is_on(tmp_path):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1, text="Arrived while Off."))
    app.memory.remember(KEY, True)
    now[0] += 1
    app.sync.ingest(message(app, 2, text="Arrived while On.", date=now[0]))
    app.sync.ingest(
        message(app, 3, text="The owner's own words.", outgoing=True, date=now[0])
    )
    await settle(app, now)
    # Nothing else ran: memory read the chat itself (Q6).
    assert app.decider.calls == [] and rewrites(app) == []
    analyzed = app.store.rows(
        "SELECT message_id,origin FROM analyzed WHERE chat_key=? ORDER BY message_id",
        (KEY,),
    )
    # The one start from the cache read message 1, as a chat Off only by
    # default does once switched On.
    assert [(r["message_id"], r["origin"]) for r in analyzed] == [
        (1, "start"),
        (2, "read"),
        (3, "read"),
    ]
    # What arrives while Remember this chat is Off by choice is never learned.
    app.memory.remember(KEY, False)
    now[0] += 1
    app.sync.ingest(message(app, 4, text="Arrived while Off by choice.", date=now[0]))
    app.memory.remember(KEY, True)
    now[0] += 1
    app.sync.ingest(message(app, 5, text="Arrived On again.", date=now[0]))
    learned = {
        r["message_id"]
        for r in app.store.rows(
            "SELECT message_id FROM analyzed WHERE chat_key=?", (KEY,)
        )
    }
    assert 4 not in learned and 5 in learned


async def test_memory_reads_existing_transcripts_and_meanings_but_starts_none(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.memory.remember(KEY, True)
    now[0] += 1
    app.sync.ingest(note(app, 1, date=now[0]))
    app.sync.ingest(picture(app, 2, text="An authored caption.", date=now[0]))
    await settle(app, now)
    assert app.media.transcriber.calls == [] and app.engine.calls == []
    assert {
        r["message_id"]
        for r in app.store.rows(
            "SELECT message_id FROM analyzed WHERE chat_key=?", (KEY,)
        )
    } == {1, 2}


async def test_a_reminder_alerts_whatever_automatic_suggestions_says(tmp_path):
    now = [stamp("2026-10-25T08:00:00+00:00")]
    app = fixture(tmp_path, clock=lambda: now[0])
    view = app.reminders.create(KEY, rule(weekday=6))
    assert view.status == "active"
    next_at = view.next_at
    now[0] = next_at - LEAD + 1
    app.reminders.tick()
    # Automatic suggestions is Off: no set is prepared two minutes before.
    assert app.suggestions.record(KEY) is None
    now[0] = next_at
    app.reminders.tick()
    pending = app.reminders.pending(KEY)
    assert len(pending) == 1 and pending[0].occurrence
    assert app.reminders.get(KEY, view.id).status == "active"
    # Opening the chat prepares none either; the Suggestions button still asks.
    assert app.suggestions.open_reminders(KEY).state == "none"
    assert app.engine.calls == []


async def test_a_reminder_set_needs_both_automatic_suggestions_switches(tmp_path):
    now = [stamp("2026-10-25T08:00:00+00:00")]
    app = fixture(tmp_path, clock=lambda: now[0])
    app.suggestions.set_settings(KEY, True, "friendly")
    view = app.reminders.create(KEY, rule(weekday=6))
    now[0] = view.next_at - LEAD + 1
    app.reminders.tick()
    record = app.suggestions.record(KEY)
    assert record and record.get("reminders")


async def test_a_reminder_paused_before_resumes_at_its_next_delivery(tmp_path):
    now = [stamp("2026-10-25T08:00:00+00:00")]
    app = fixture(tmp_path, clock=lambda: now[0])
    view = app.reminders.create(KEY, rule(weekday=6))
    # As ADR 58 Q4 left it while Automatic suggestions was Off.
    with app.store.transaction() as db:
        db.execute("UPDATE reminder_schedules SET paused=1,next_at=NULL")
    now[0] = stamp("2026-11-03T08:00:00+00:00")
    app.reminders.tick()
    resumed = app.reminders.get(KEY, view.id)
    assert resumed.status == "active" and resumed.occurrence is None
    assert resumed.next_at == stamp("2026-11-08T09:00:00+00:00")


async def test_emotion_turned_on_decides_the_latest_block_from_its_caption(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1, text="Authored plain text."))
    app.sync.ingest(message(app, 2, text="An authored reply.", outgoing=True))
    app.sync.ingest(picture(app, 3, text="An authored caption."))
    await settle(app, now)
    assert app.decider.calls == []
    app.decider.emotion = "excited"
    app.emotion_switch.set(KEY, True)
    await settle(app, now, seconds=0)
    (packet,) = decisions(app)
    assert packet["asks"] == ["emotion"]
    # Its caption alone: the image was never read (Q20).
    assert latest(packet) == [
        {
            "id": 3,
            "from": "recipient",
            "text": "An authored caption.",
            "attachment": "image",
            "latest_block": True,
        }
    ]
    assert app.emotion.view(KEY).code == "excited"
    assert rewrites(app) == [] and not app.store.rows("SELECT 1 FROM derivatives")
    # Voice and images turned On rewrites it; the earlier block stays as is.
    app.media_switch.set(KEY, True)
    await settle(app, now, seconds=0)
    (rewrite,) = rewrites(app)
    assert [m["id"] for m in rewrite["messages"]] == [3]
    assert [i["message_id"] for i in rewrite["images"]] == [3]
    blocks = app.conversation(KEY)["blocks"]
    assert [b["state"] for b in blocks] == ["original", "ready"]
    # Its decision follows the rewrite, asking what Emotion uses.
    assert [p["asks"] for p in decisions(app)] == [["emotion"], ["emotion"]]


async def test_soften_turned_on_softens_the_latest_block_if_it_is_hostile(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1, text="Authored hostile wording."))
    await settle(app, now)
    app.decider.hostility = 1.0
    app.calming.set(KEY, True)
    await settle(app, now, seconds=0)
    assert [p["asks"] for p in decisions(app)] == [["hostility", "sarcasm"]]
    (rewrite,) = rewrites(app)
    assert rewrite.get("calm") is True
    (block,) = app.conversation(KEY)["blocks"]
    assert block["state"] == "ready" and block["calmed"]


async def test_automatic_suggestions_turned_on_answers_the_latest_block(tmp_path):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1, text="Authored question for a reply."))
    await settle(app, now)
    app.decider.useful = 0.9
    app.suggestions.set_settings(KEY, True, "friendly")
    await settle(app, now, seconds=0)
    assert [p["asks"] for p in decisions(app)] == [["suggestions_useful"]]
    assert len(suggestion_sets(app)) == 1


async def test_a_switch_turned_on_leaves_an_answered_or_old_block(tmp_path):
    app, now = rig(tmp_path)
    app.sync.ingest(message(app, 1, text="Authored plain text."))
    app.sync.ingest(message(app, 2, text="An authored reply.", outgoing=True))
    await settle(app, now)
    # Answered: Automatic suggestions turned On asks nothing for it (Q8).
    app.decider.useful = 0.9
    app.suggestions.set_settings(KEY, True, "friendly")
    await settle(app, now, seconds=0)
    assert decisions(app) == [] and suggestion_sets(app) == []
    # Emotion still estimates their latest messages, answered or not.
    app.emotion_switch.set(KEY, True)
    await settle(app, now, seconds=0)
    assert [p["asks"] for p in decisions(app)] == [["emotion", "suggestions_useful"]]
    assert suggestion_sets(app) == []
    app.emotion_switch.set(KEY, False)
    app.suggestions.set_settings(KEY, False, "friendly")
    app.sync.ingest(picture(app, 3, text="An authored caption."))
    await settle(app, now)
    # Past seven days, a switch turned On processes nothing older.
    now[0] += WINDOW
    app.media_switch.set(KEY, True)
    app.emotion_switch.set(KEY, True)
    await settle(app, now, seconds=0)
    assert len(decisions(app)) == 1 and rewrites(app) == []


async def test_voice_and_images_off_transcribes_and_reads_nothing(tmp_path):
    app, now = rig(tmp_path)
    app.emotion_switch.set(KEY, True)
    app.sync.ingest(note(app, 1, outgoing=True))
    app.sync.ingest(note(app, 2))
    await settle(app, now)
    # Neither the owner's own note nor the incoming one is transcribed, and
    # the decision sees no transcript (Q20).
    assert app.media.transcriber.calls == []
    (packet,) = decisions(app)
    assert "transcript" not in json.dumps(packet["state"])
    assert [m["id"] for m in latest(packet)] == [2]
    # Show transcript still works (Q3), and starts nothing by itself.
    incoming = app.store.message(KEY, 2)
    assert app.transcripts.request(incoming)["state"] == "running"
    await settle(app, now, seconds=0)
    assert len(app.media.transcriber.calls) == 1
    assert app.transcripts.status(incoming)["state"] == "done"
    assert len(decisions(app)) == 1 and rewrites(app) == []


async def test_a_softened_rewrite_leaves_images_unread_with_voice_and_images_off(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.calming.set(KEY, True)
    app.decider.hostility = 1.0
    app.sync.ingest(picture(app, 1, text="Authored hostile caption."))
    await settle(app, now)
    (rewrite,) = rewrites(app)
    assert rewrite.get("calm") is True and not rewrite.get("images")
    assert not app.store.rows("SELECT 1 FROM derivatives")
    (block,) = app.conversation(KEY)["blocks"]
    assert block["state"] == "ready" and block["calmed"]


async def test_rewrite_earlier_messages_reads_media_whatever_the_switch_says(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.sync.ingest(picture(app, 1, text="An authored caption."))
    await settle(app, now)
    (block,) = app.conversation(KEY)["blocks"]
    assert block["state"] == "original"
    # A media block Voice and images left unread waits for the deliberate tap.
    assert app.conversation(KEY)["older"]["available"] == 1
    assert app.rewriting.process_older(KEY) == 1
    await settle(app, now, seconds=0)
    (rewrite,) = rewrites(app)
    assert [i["message_id"] for i in rewrite["images"]] == [1]
    assert app.conversation(KEY)["blocks"][0]["state"] == "ready"
    # A deliberate run starts no decision by itself.
    assert decisions(app) == []


async def test_switching_off_and_on_again_resamples_nothing(tmp_path):
    app, now = rig(tmp_path)
    app.emotion_switch.set(KEY, True)
    app.suggestions.set_settings(KEY, True, "friendly")
    app.decider.emotion, app.decider.useful = "worried", 0.9
    app.sync.ingest(message(app, 1, text="Authored worry for a reply."))
    await settle(app, now)
    shown = app.suggestions.view(KEY)
    assert shown.state == "ready" and len(decisions(app)) == 1
    # Off hides the set and the emoji; On again shows both as they were.
    app.suggestions.set_settings(KEY, False, "friendly")
    app.emotion_switch.set(KEY, False)
    assert app.suggestions.view(KEY).reason == "off"
    app.suggestions.set_settings(KEY, True, "friendly")
    app.emotion_switch.set(KEY, True)
    await settle(app, now, seconds=0)
    assert app.suggestions.view(KEY).attempt == shown.attempt
    assert app.emotion.view(KEY).code == "worried"
    assert len(decisions(app)) == 1 and len(suggestion_sets(app)) == 1


async def test_a_set_off_cancelled_is_written_from_its_decision_when_on(tmp_path):
    app, now = rig(tmp_path)
    app.suggestions.set_settings(KEY, True, "friendly")
    app.decider.useful = 0.9
    app.sync.ingest(message(app, 1, text="Authored question for a reply."))
    now[0] += 31
    # The decision runs, then Off cancels its set before it is written.
    app.emotion.decide_held()
    (job,) = app.store.rows(
        "SELECT * FROM jobs WHERE role='emotion_classification' AND state='queued'"
    )
    assert app.rewriting.claim(job)
    await app.emotion.classify(job)
    app.suggestions.set_settings(KEY, False, "friendly")
    assert app.suggestions.record(KEY)["state"] == "cancelled"
    app.suggestions.set_settings(KEY, True, "friendly")
    await settle(app, now, seconds=0)
    # One decision only: its answer schedules the set again.
    assert len(decisions(app)) == 1 and len(suggestion_sets(app)) == 1
    assert app.suggestions.view(KEY).state == "ready"


async def test_memory_reads_a_photo_again_with_the_meaning_its_rewrite_made(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.memory.remember(KEY, True)
    app.media_switch.set(KEY, True)
    now[0] += 1
    app.sync.ingest(picture(app, 1, text="An authored caption.", date=now[0]))
    assert [r["message_id"] for r in app.memory.candidates(KEY)] == [1]
    await settle(app, now)
    assert rewrites(app)
    # Still a source, now with its image meaning (ADR 62 Q6).
    (row,) = app.memory.candidates(KEY)
    assert app.memory.projection(row)["image_context"]


async def test_show_transcript_keeps_a_read_voice_note_a_memory_source(tmp_path):
    app, now = rig(tmp_path)
    app.memory.remember(KEY, True)
    now[0] += 1
    app.sync.ingest(note(app, 1, date=now[0]))
    assert [r["message_id"] for r in app.memory.candidates(KEY)] == [1]
    app.transcripts.request(app.store.message(KEY, 1))
    await settle(app, now, seconds=0)
    (row,) = app.memory.candidates(KEY)
    assert app.memory.projection(row)["transcript"] == "Authored spoken words."


async def test_suggestions_on_under_the_server_wide_switch_off_asks_nothing(
    tmp_path,
):
    app, now = rig(tmp_path)
    app.emotion_switch.set(KEY, True)
    app.sync.ingest(message(app, 1, text="Authored question for a reply."))
    await settle(app, now)
    assert len(decisions(app)) == 1
    app.suggestions.enabled = lambda: False
    app.suggestions.set_settings(KEY, True, "friendly")
    await settle(app, now, seconds=0)
    # Usefulness could not be asked: no request at all.
    assert len(decisions(app)) == 1


async def test_soften_on_softens_a_rewritten_hostile_block(tmp_path):
    app, now = rig(tmp_path)
    app.media_switch.set(KEY, True)
    app.sync.ingest(picture(app, 1, text="Authored hostile caption."))
    await settle(app, now)
    (block,) = app.conversation(KEY)["blocks"]
    assert block["state"] == "ready" and not block["calmed"]
    app.decider.hostility = 1.0
    app.calming.set(KEY, True)
    await settle(app, now, seconds=0)
    assert [p["asks"] for p in decisions(app)] == [["hostility", "sarcasm"]]
    calmed = [r for r in rewrites(app) if r.get("calm")]
    assert len(calmed) == 1
    (block,) = app.conversation(KEY)["blocks"]
    assert block["state"] == "ready" and block["calmed"]


async def test_emotion_on_keeps_the_usefulness_answer_and_the_set(tmp_path):
    app, now = rig(tmp_path)
    app.suggestions.set_settings(KEY, True, "friendly")
    app.decider.useful = 0.9
    app.sync.ingest(message(app, 1, text="Authored question for a reply."))
    await settle(app, now)
    shown = app.suggestions.view(KEY)
    assert shown.state == "ready"
    # A later usefulness answer would differ; it is never asked again.
    app.decider.useful, app.decider.emotion = 0.1, "worried"
    app.emotion_switch.set(KEY, True)
    await settle(app, now, seconds=0)
    assert [p["asks"] for p in decisions(app)] == [
        ["suggestions_useful"],
        ["emotion"],
    ]
    record = app.emotion.record(KEY)
    assert record["asks"] == ["emotion", "suggestions_useful"]
    assert (record["code"], record["usefulness"]) == ("worried", 0.9)
    assert app.emotion.view(KEY).code == "worried"
    assert app.suggestions.view(KEY).attempt == shown.attempt


async def test_a_queued_decision_whose_switches_turned_off_is_never_sent(tmp_path):
    app, now = rig(tmp_path)
    app.emotion_switch.set(KEY, True)
    app.sync.ingest(message(app, 1, text="Authored plain text."))
    now[0] += 31
    app.emotion.decide_held()
    assert app.store.rows(
        "SELECT 1 FROM jobs WHERE role='emotion_classification' AND state='queued'"
    )
    app.emotion_switch.set(KEY, False)
    await settle(app, now, seconds=0)
    assert decisions(app) == []
    (state,) = app.store.rows(
        "SELECT state FROM jobs WHERE role='emotion_classification'"
    )
    assert state["state"] == "superseded"


async def test_voice_and_images_on_softens_anew_with_the_unread_images(tmp_path):
    app, now = rig(tmp_path)
    app.calming.set(KEY, True)
    app.decider.hostility = 1.0
    app.sync.ingest(picture(app, 1, text="Authored hostile caption."))
    await settle(app, now)
    (first,) = rewrites(app)
    assert first.get("calm") and not first.get("images")
    app.media_switch.set(KEY, True)
    await settle(app, now, seconds=0)
    second = rewrites(app)[-1]
    assert second.get("calm") and [i["message_id"] for i in second["images"]] == [1]
    (block,) = app.conversation(KEY)["blocks"]
    assert block["state"] == "ready" and block["calmed"]
    assert not json.loads(block["unanalysed"] or "[]")


async def test_with_emotion_off_an_earlier_emotion_qualifies_no_set(tmp_path):
    app, now = rig(tmp_path)
    app.emotion_switch.set(KEY, True)
    app.suggestions.set_settings(KEY, True, "friendly")
    app.decider.emotion, app.decider.useful = "worried", 0.1
    app.sync.ingest(message(app, 1, text="Authored worry."))
    await settle(app, now)
    assert app.suggestions.view(KEY).state == "ready"
    # Off, no emotion is used (Q5): the set that only it qualified hides.
    app.emotion_switch.set(KEY, False)
    assert app.suggestions.view(KEY).state != "ready"
