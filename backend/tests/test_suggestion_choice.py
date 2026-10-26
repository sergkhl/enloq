"""ADR 56: either signal, each chat's Suggestions choice, idiom sets and the
remembered fold. Fake providers and authored messages only."""

import pytest
from enloq.domain import suggestions as domain
from enloq.domain.models import Chat, Conflict, Refused
from enloq.domain.suggestions import CALM_USEFULNESS, QUALIFYING
from enloq.inference.guidance import (
    DEFAULTS_REVISION,
    IDIOMS_FRAME,
    instructions,
)

from backend.tests.test_emotion import KEY
from backend.tests.test_rebuild import message
from backend.tests.test_suggestion_controls import BASE, draft, served  # noqa: F401
from backend.tests.test_suggestion_controls import ready as published
from backend.tests.test_suggestion_generation import (
    Engine,
    drain,
    incoming_only,
    jobs,
    rig,
)

# Suggestions following ADR 30's decision; calming (ADR 53) is test_calming's.
pytestmark = pytest.mark.usefixtures("calming_off", "text_rewriting")


class Muse(Engine):
    name, model = "muse", "authored-muse"


@pytest.mark.parametrize(
    "code,useful",
    [
        ("neutral", 0.9),
        ("neutral", 0.7),
        ("unknown", 0.9),
        ("sad", 0.1),
        ("neutral", 0.1),
        # Q19 A: a yes below 0.7, like the closing answer's 0.545, is not
        # enough for a calm block.
        ("neutral", 0.545),
        ("unknown", 0.1),
        (None, 0.9),
    ],
)
async def test_either_a_non_neutral_emotion_or_a_clear_yes_on_usefulness_suggests(
    tmp_path, code, useful
):
    # The owner's "either Emotional or Useful", reversing ADR 49 Q2 C, with
    # Q19 A's bar for calm blocks; a failed decision (None) qualifies nothing.
    app, _ = rig(tmp_path)
    app.engine.emotion, app.engine.useful = code, useful
    app.sync.ingest(message(app, text="Are you coming on Saturday?"))
    await drain(app)
    expected = code is not None and (code in QUALIFYING or useful >= CALM_USEFULNESS)
    assert bool(jobs(app)) is expected
    assert app.engine.count() == expected
    view = app.suggestions.view(KEY)
    assert view.state == ("ready" if expected else "none")
    if expected:
        current = app.store.draft(KEY)
        inserted = app.suggestions.insert(KEY, current["revision"], view.attempt, 0)
        assert inserted["draft"]["state"] == "inserted"


async def test_each_private_chat_chooses_off_or_on(tmp_path, monkeypatch):
    app, _ = rig(tmp_path)
    assert app.suggestions.settings(KEY) == {"automatic": True, "style": "friendly"}
    with pytest.raises(Refused, match="Unknown"):
        app.suggestions.set_settings(KEY, True, "unknown")
    app.sync.ingest(message(app, text="I had an awful day."))
    await drain(app)
    shown = app.suggestions.view(KEY)
    assert shown.state == "ready"
    # Off shows nothing and inserts nothing...
    assert app.suggestions.set_settings(KEY, False, "friendly") == {
        "automatic": False,
        "style": "friendly",
    }
    assert app.suggestions.view(KEY).reason == "off"
    current = app.store.draft(KEY)
    with pytest.raises(Conflict, match="Off"):
        app.suggestions.insert(KEY, current["revision"], shown.attempt, 0)
    # ...and a set prepared before shows again on switching back.
    app.suggestions.set_settings(KEY, True, "friendly")
    assert app.suggestions.view(KEY).attempt == shown.attempt
    # Off, a new block gets no request; On again regenerates nothing.
    app.suggestions.set_settings(KEY, False, "friendly")
    app.sync.ingest(message(app, 2, text="It keeps getting worse."))
    await drain(app)
    app.suggestions.set_settings(KEY, True, "friendly")
    await drain(app)
    assert app.engine.count() == 1 and len(jobs(app)) == 1
    assert app.suggestions.view(KEY).state == "none"
    # The choice is the chat's own, kept once made.
    restarted, _ = rig(tmp_path)
    assert restarted.suggestions.settings(KEY) == {
        "automatic": True,
        "style": "friendly",
    }
    assert restarted.store.rows("SELECT automatic,style FROM suggestion_settings") == [
        {"automatic": 1, "style": "friendly"}
    ]


async def test_off_cancels_the_chats_queued_work(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app, text="I had an awful day."))
    await incoming_only(app)
    [queued] = jobs(app, state="queued")
    app.suggestions.set_settings(KEY, False, "friendly")
    assert [j["state"] for j in jobs(app)] == ["cancelled"]
    assert app.suggestions.record(KEY)["state"] == "cancelled"
    app.suggestions.set_settings(KEY, True, "friendly")
    await drain(app)
    assert app.engine.count() == 0
    assert not app.store.rows("SELECT * FROM reservations WHERE id=?", (queued["id"],))


async def test_groups_have_no_suggestions_choice(tmp_path):
    app, _ = rig(tmp_path)
    app.store.put_chat(
        Chat(account_id=1, peer="chat:7", title="Authored group", kind="group")
    )
    for call in (
        lambda: app.suggestions.settings("1/chat:7"),
        lambda: app.suggestions.set_settings("1/chat:7", False, "friendly"),
    ):
        with pytest.raises(Refused, match="private chats only"):
            call()


async def test_an_idiom_chat_writes_idiom_sets_from_its_next_block(
    tmp_path, monkeypatch
):
    # The writer is the next test's; here both sets go to the rig's engine.
    monkeypatch.setattr(domain, "IDIOMS_WRITER", "mimo")
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app, text="I had an awful day."))
    await drain(app)
    first = app.suggestions.view(KEY)
    assert app.suggestions.set_settings(KEY, True, "idioms") == {
        "automatic": True,
        "style": "idioms",
    }
    # Q5 A: the set already written stays as it is; nothing is regenerated.
    await drain(app)
    assert app.engine.count() == 1
    assert app.suggestions.view(KEY).attempt == first.attempt
    app.sync.ingest(message(app, 2, text="It keeps getting worse."))
    await drain(app)
    written = [p for r, p in app.engine.calls if r == "response_suggestions"]
    assert "idioms" not in written[0] and written[1]["idioms"] is True
    assert app.suggestions.record(KEY)["idioms"] is True
    assert app.suggestions.view(KEY).state == "ready"
    assert app.suggestions.settings(KEY) == {"automatic": True, "style": "idioms"}


async def test_a_choice_changed_while_a_set_waits_applies_to_the_next_block(
    tmp_path, monkeypatch
):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app, text="I had an awful day."))
    await incoming_only(app)
    app.suggestions.set_settings(KEY, True, "idioms")
    await drain(app)
    written = [p for r, p in app.engine.calls if r == "response_suggestions"]
    assert len(written) == 1 and "idioms" not in written[0]


@pytest.mark.parametrize("writer", ["mimo", "muse"])
async def test_the_gates_winner_writes_idiom_sets_and_plain_sets_stay_on_mimo(
    tmp_path, monkeypatch, writer
):
    monkeypatch.setattr(domain, "IDIOMS_WRITER", writer)
    app, _ = rig(tmp_path)
    app.calmer = Muse()
    app.sync.ingest(message(app, text="I had an awful day."))
    await drain(app)
    assert (app.engine.count(), app.calmer.count()) == (1, 0)
    app.suggestions.set_settings(KEY, True, "idioms")
    app.sync.ingest(message(app, 2, text="It keeps getting worse."))
    await drain(app)
    idiom_set = (1, 1) if writer == "muse" else (2, 0)
    assert (app.engine.count(), app.calmer.count()) == idiom_set
    receipt = app.store.rows(
        "SELECT model FROM receipts WHERE role='response_suggestions' ORDER BY rowid DESC LIMIT 1"
    )[0]
    assert receipt["model"] == (
        "authored-muse" if writer == "muse" else "authored-model"
    )


def test_the_idioms_frame_and_field_reach_only_idiom_sets():
    assert DEFAULTS_REVISION == 21
    plain = instructions("response_suggestions", {})
    idioms = instructions("response_suggestions", {"idioms": True})
    assert IDIOMS_FRAME not in plain and "[Owner guidance: idioms]" not in plain
    assert idioms.startswith(plain) and IDIOMS_FRAME in idioms
    assert idioms.index(IDIOMS_FRAME) < idioms.index("[Owner guidance: idioms]")


async def test_a_folded_set_stays_folded_everywhere_until_the_next_set(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app, text="I had an awful day."))
    await drain(app)
    shown = app.suggestions.view(KEY)
    assert shown.folded is False
    _, before = app.store.changes()
    folded = app.suggestions.fold(KEY, shown.attempt, True)
    assert folded.folded is True and folded.options == shown.options
    # Every client hears of it, and a restart reads the same state.
    assert KEY in app.store.changes(before)[0]
    restarted, _ = rig(tmp_path)
    assert restarted.suggestions.view(KEY).folded is True
    with pytest.raises(Conflict, match="Suggestions changed"):
        app.suggestions.fold(KEY, "f" * 32, False)
    assert app.suggestions.fold(KEY, shown.attempt, False).folded is False
    app.suggestions.fold(KEY, shown.attempt, True)
    app.sync.ingest(message(app, 2, text="It keeps getting worse."))
    await drain(app)
    following = app.suggestions.view(KEY)
    assert following.state == "ready" and following.attempt != shown.attempt
    assert following.folded is False


async def test_choice_and_fold_routes(served, monkeypatch):  # noqa: F811
    client, manager, conversation = served
    assert client.get(f"{BASE}/suggestions/settings").json() == {
        "automatic": True,
        "style": "friendly",
    }
    chosen = client.post(
        f"{BASE}/suggestions/settings", json={"automatic": True, "style": "idioms"}
    )
    assert chosen.json() == {"automatic": True, "style": "idioms"}
    assert (
        client.post(
            f"{BASE}/suggestions/settings",
            json={"automatic": True, "style": "sometimes"},
        ).status_code
        == 422
    )
    record = await published(conversation)
    shown = client.get(BASE).json()["suggestions"]
    assert shown["state"] == "ready" and shown["folded"] is False
    folded = client.post(
        f"{BASE}/suggestions/fold", json={"attempt": record["attempt"], "folded": True}
    )
    assert folded.status_code == 200 and folded.json()["folded"] is True
    assert client.get(BASE).json()["suggestions"]["folded"] is True
    stale = client.post(
        f"{BASE}/suggestions/fold", json={"attempt": "c" * 32, "folded": False}
    )
    assert stale.status_code == 409
    off = client.post(
        f"{BASE}/suggestions/settings", json={"automatic": False, "style": "friendly"}
    )
    assert off.json() == {"automatic": False, "style": "friendly"}
    assert client.get(BASE).json()["suggestions"]["reason"] == "off"
    current = draft(client)
    inserted = client.post(
        f"{BASE}/suggestions/insert",
        json={
            "revision": current["revision"],
            "attempt": record["attempt"],
            "index": 0,
        },
    )
    assert inserted.status_code == 409


def test_an_astral_emoji_escape_is_read_as_its_emoji_and_control_characters_fail():
    # Muse's \u0001F973 in the idioms gate's second run.
    from enloq.inference.base import EngineResponse, InvalidOutput
    from enloq.inference.work import validate_output

    raw = '{"options": [{"purpose": "accept", "text": "Count me in \\u0001F973"}]}'
    result = validate_output("response_suggestions", EngineResponse(raw), {})
    assert result.options[0].text == "Count me in 🥳"
    broken = '{"options": [{"purpose": "accept", "text": "Count me in \\u0007"}]}'
    with pytest.raises(InvalidOutput, match="invalid_text"):
        validate_output("response_suggestions", EngineResponse(broken), {})
