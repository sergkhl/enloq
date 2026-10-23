"""ADR 55 Q10-Q13, Q22, Q23: traits of a chat's person and the account's
holder, collected for the owner's eyes only."""

import pytest
from enloq.application.memory import ACCOUNT
from enloq.domain import traits as starter
from enloq.domain.models import Conflict, Refused
from enloq.inference.base import InvalidOutput

from backend.tests.test_memory import DAY, KEY, conversation, fold, no_start
from backend.tests.test_rebuild import fixture, message

# Memory as before ADR 56's Off default: these chats learn without switching
# on; test_memory_default covers the default as shipped.
pytestmark = pytest.mark.usefixtures("remember_on")


def answers(subject, level="3", evidence=0.9, choice=None):
    result = {}
    for name, question in starter.questions(subject).items():
        if question["type"] == "noul":
            result[name] = {"type": "noul", "noul": evidence}
        elif question["type"] == "score":
            result[name] = {
                "type": "score",
                "score": float(level),
                "probabilities": {
                    str(i): 0.8 if str(i) == level else 0.05 for i in range(5)
                },
                "confidence": 0.7,
            }
        else:
            code = choice or next(iter(question["criteria"]))
            result[name] = {
                "type": "choice",
                "choice": code,
                "probabilities": {
                    c: 0.9 if c == code else 0.01 for c in question["criteria"]
                },
            }
    return result


def test_the_starter_list_asks_each_trait_with_its_evidence():
    person, owner = starter.questions("person"), starter.questions("owner")
    assert len(person) == 2 * 16 and len(owner) == 2 * 14
    assert {"closeness", "relationship"} <= set(person)
    assert not {"closeness", "relationship"} & set(owner)
    assert person["directness"]["type"] == "score"
    assert len(person["directness"]["criteria"]) == 5
    assert person["conflict"]["type"] == "choice"
    assert person["evidence_humor"]["type"] == "noul"
    assert "the recipient" in person["humor"]["instructions"]
    assert "messages from the owner" in owner["humor"]["instructions"]


def test_answers_are_read_as_the_likeliest_value_or_fail_without_content():
    read = starter.validate(answers("person", level="4"), "person")
    assert read["directness"] == {"value": "4", "probability": 0.8, "evidence": 0.9}
    assert read["relationship"]["value"] == "partner"
    assert starter.label("directness", "4") == "Very direct"
    assert starter.label("relationship", "unclear") is None
    bad = answers("person")
    bad["humor"]["probabilities"]["9"] = 0.1
    with pytest.raises(InvalidOutput, match="invalid_probabilities"):
        starter.validate(bad, "person")
    missing = answers("owner")
    missing.pop("evidence_humor")
    with pytest.raises(InvalidOutput, match="invalid_answer"):
        starter.validate(missing, "owner")


async def published(app, now):
    """A memory update that publishes, which schedules the traits."""
    conversation(app, now)
    for identity, day in ((8, 3), (9, 2), (10, 1)):
        app.sync.ingest(
            message(app, identity, date=now[0] - day * DAY, text=f"Theirs {identity}."),
            target=False,
        )
    for identity, day in ((5, 3), (6, 2), (7, 1)):
        app.sync.ingest(
            message(
                app,
                identity,
                date=now[0] - day * DAY,
                text=f"Mine {identity}.",
                outgoing=True,
            ),
            target=False,
        )
    app.memory.record(KEY, [f"{KEY}/{i}" for i in (1, 2)])
    now[0] += 1800
    assert app.memory.schedule(KEY)
    await fold(app)


async def run_trait_jobs(app):
    jobs = app.store.rows(
        "SELECT * FROM jobs WHERE role IN ('trait_scales','trait_notes') AND state='queued' ORDER BY rowid"
    )
    for job in jobs:
        assert app.rewriting.claim(job)
        await app.rewriting.run(job)
    return jobs


async def test_a_published_memory_update_judges_both_subjects(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    await published(app, now)
    jobs = await run_trait_jobs(app)
    assert sorted((j["role"], j["scope"]) for j in jobs) == [
        ("trait_notes", KEY),
        ("trait_notes", ACCOUNT),
        ("trait_scales", KEY),
        ("trait_scales", ACCOUNT),
    ]
    view = app.traits.view(KEY)
    directness = next(s for s in view["scales"] if s["trait"] == "directness")
    assert (directness["value"], directness["label"], directness["current"]) == (
        "2",
        "Mixed",
        True,
    )
    assert (
        next(s for s in view["scales"] if s["trait"] == "relationship")["label"]
        == "Partner"
    )
    assert "relationship" not in {
        s["trait"] for s in app.traits.view(ACCOUNT)["scales"]
    }
    # The decision model reads the person's month; the holder's own across chats.
    states = [p for r, p in app.decider.calls if r == "trait_scales"]
    assert {p["subject"] for p in states} == {"person", "owner"}
    person = next(p for p in states if p["subject"] == "person")["state"]
    assert {m["from"] for m in person["conversation"]} == {"recipient", "owner"}
    # A notes pass at most once a day.
    app.traits.schedule(KEY)
    assert [
        r["role"]
        for r in app.store.rows(
            "SELECT role FROM jobs WHERE role IN ('trait_scales','trait_notes') AND state='queued'"
        )
    ] == ["trait_scales", "trait_scales"]


async def test_low_evidence_keeps_the_last_value_and_the_owners_is_never_changed(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    await published(app, now)
    await run_trait_jobs(app)
    revision = app.traits.view(KEY)["revision"]
    app.traits.set_scale(KEY, revision, "humor", "4")
    with pytest.raises(Refused):
        app.traits.set_scale(KEY, revision + 1, "humor", "9")
    with pytest.raises(Refused):
        app.traits.set_scale(ACCOUNT, 0, "closeness", "1")
    app.decider.trait_evidence = 0.2
    app.decider.trait_level = "0"
    now[0] += 60
    app.traits.schedule(KEY)
    await run_trait_jobs(app)
    view = app.traits.view(KEY)
    scales = {s["trait"]: s for s in view["scales"]}
    assert scales["directness"]["value"] == "2" and not scales["directness"]["current"]
    assert (scales["humor"]["value"], scales["humor"]["source"]) == ("4", "owner")
    cleared = app.traits.set_scale(KEY, view["revision"], "directness", None)
    directness = next(s for s in cleared["scales"] if s["trait"] == "directness")
    assert (directness["value"], directness["source"]) == (None, "owner")


async def test_thin_material_asks_nothing(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    no_start(app)
    app.sync.ingest(message(app, 1, text="hey"), target=False)
    app.traits.schedule(KEY)
    await run_trait_jobs(app)
    assert not [r for r, _ in app.decider.calls if r == "trait_scales"]
    assert app.traits.state(KEY)["scales_judged"] == now[0]
    assert app.traits.view(KEY)["habits"] is None


def test_notes_keep_the_script_of_the_subjects_own_words():
    from enloq.application.traits import Traits

    assert Traits.script([{"text": "Привет, как дела?"}, {"text": "ok"}]) == "Cyrillic"
    assert Traits.script([{"text": "Fine, thanks."}]) == "Latin"
    assert Traits.script([{"text": "🙂"}]) is None


async def test_notes_need_three_own_messages_on_two_days(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    no_start(app)
    for identity in (1, 2, 3):
        app.sync.ingest(
            message(
                app, identity, date=now[0] - 3600 * identity, text=f"Theirs {identity}."
            ),
            target=False,
        )
    app.traits.schedule(KEY)
    await run_trait_jobs(app)
    assert not app.traits.view(KEY)["notes"]  # Three messages, one day.
    app.sync.ingest(
        message(app, 4, date=now[0] - 2 * DAY, text="Theirs, earlier."), target=False
    )
    now[0] += DAY
    app.traits.schedule(KEY)
    await run_trait_jobs(app)
    notes = app.traits.view(KEY)["notes"]
    assert [(n["text"], n["source"], n["confidence"]) for n in notes] == [
        ("Writes in short authored messages.", "model", 0.8)
    ]
    assert sorted(notes[0]["citations"]) == [2, 3, 4]
    # A deleted cited message drops its citation; below three, the note goes.
    app.sync.deleted("user:42", [4])
    assert not app.traits.view(KEY)["notes"]


async def test_the_owners_notes_stay_theirs(tmp_path):
    app = fixture(tmp_path)
    view = app.traits.add_note(KEY, 0, "Hates phone calls.")
    note = view["notes"][0]
    assert (note["source"], note["citations"], note["confidence"]) == (
        "owner",
        [],
        None,
    )
    view = app.traits.edit_note(KEY, view["revision"], note["id"], "Prefers text.")
    assert [n["text"] for n in view["notes"]] == ["Prefers text."]
    with pytest.raises(Conflict):
        app.traits.remove_note(KEY, view["revision"] - 1, note["id"])
    assert not app.traits.remove_note(KEY, view["revision"], note["id"])["notes"]
    for index in range(10):
        app.traits.add_note(
            ACCOUNT, app.traits.state(ACCOUNT)["revision"], f"Mine {index}."
        )
    with pytest.raises(Refused):
        app.traits.add_note(ACCOUNT, app.traits.state(ACCOUNT)["revision"], "One more.")


def test_habits_are_counted_without_a_model(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    no_start(app)
    base = now[0] - 2 * DAY
    identity = 0
    for day in range(2):
        for index in range(6):
            start = base + day * DAY + index * 600
            identity += 1
            app.sync.ingest(
                message(app, identity, date=start, text="Mine.", outgoing=True),
                target=False,
            )
            identity += 1
            app.sync.ingest(
                message(app, identity, date=start + 120, text="Theirs."),
                target=False,
            )
    habits = app.traits.habits(KEY)
    assert habits["messages"] == 12 and habits["reply_seconds"] == 120
    assert habits["voice_share"] == 0 and habits["starts_share"] == 0
    assert (habits["active_to"] - habits["active_from"]) % 24 == 4
    own = app.traits.habits(ACCOUNT)
    assert own["messages"] == 12 and own["starts_share"] == 1


async def test_trait_jobs_are_memory_work_behind_communication(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    await published(app, now)
    with app.store.transaction() as db:
        db.execute(
            "INSERT INTO jobs VALUES ('busy',?,'incoming_rewrite','1','queued',?,'{}',NULL)",
            (KEY, now[0] + DAY),
        )
    assert app.rewriting.next_job() is None
    app.memory.enabled = lambda: False
    with app.store.transaction() as db:
        db.execute("DELETE FROM jobs WHERE id='busy'")
    assert app.rewriting.next_job() is None
    app.memory.enabled = lambda: True
    assert app.rewriting.next_job()["role"] in ("trait_scales", "trait_notes")
    # Remember this chat off cancels the chat's waiting traits.
    app.memory.remember(KEY, False)
    assert not app.store.rows(
        "SELECT * FROM jobs WHERE scope=? AND role LIKE 'trait_%' AND state='queued'",
        (KEY,),
    )


def test_traits_api_for_a_chat_and_the_account(tmp_path):
    from enloq.api.server import create_app
    from enloq.config import Settings
    from fastapi.testclient import TestClient

    from backend.tests.auth_helpers import trust_browser

    app = create_app(Settings(data=tmp_path / "app"))
    origin = {"Origin": "http://127.0.0.1:8765"}
    with TestClient(app, base_url="http://127.0.0.1:8765") as client:
        trust_browser(client, tmp_path / "app")
        base = "/api/accounts/1/chats/user:101/traits"
        view = client.get(base).json()
        assert len(view["scales"]) == 16 and view["notes"] == []
        view = client.post(
            f"{base}/scales/formality",
            json={"revision": 0, "value": "4"},
            headers=origin,
        ).json()
        formality = next(s for s in view["scales"] if s["trait"] == "formality")
        assert (formality["label"], formality["source"]) == ("Formal", "owner")
        view = client.post(
            f"{base}/notes",
            json={"revision": view["revision"], "text": "Night owl."},
            headers=origin,
        ).json()
        assert [n["text"] for n in view["notes"]] == ["Night owl."]
        own = client.get("/api/accounts/1/traits").json()
        assert len(own["scales"]) == 14 and own["notes"] == []
        assert (
            client.post(
                "/api/accounts/1/traits/scales/closeness",
                json={"revision": 0, "value": "1"},
                headers=origin,
            ).status_code
            == 400
        )


async def test_message_ids_never_stay_in_a_notes_text(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    no_start(app)
    for identity, day in ((1, 3), (2, 2), (3, 1)):
        app.sync.ingest(
            message(app, identity, date=now[0] - day * DAY, text=f"Theirs {identity}."),
            target=False,
        )

    async def generate(role, packet):
        return {
            "add": [
                {
                    "text": "Asks for favours (1, 2, 3)",
                    "citations": [1, 2, 3],
                    "confidence": 0.6,
                },
                {"text": "(id 1, 2, 3)", "citations": [1, 2, 3], "confidence": 0.5},
            ],
            "remove": [],
        }

    app.engine.generate = generate
    app.traits.schedule(KEY)
    await run_trait_jobs(app)
    assert [n["text"] for n in app.traits.view(KEY)["notes"]] == ["Asks for favours"]
