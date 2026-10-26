"""Authored browser fixture for ADR 25 response suggestions.

It prepares suggestion records through the real capture/publication path and
an end-to-end pipeline through the real scheduler with a gated scripted engine.
No provider, Telegram or user content is involved.
"""

import asyncio
import json
import time

from enloq.application.context import incoming
from enloq.domain.models import Chat, Message
from enloq.domain.suggestions import SuggestionOption
from enloq.inference.base import EngineResponse
from enloq.inference.fake import choice, label_choice
from fastapi import Request

PEER, PIPELINE = "user:126", "user:127"
OPTIONS = [
    {"purpose": "acknowledge", "text": "That sounds like a really hard day."},
    {"purpose": "clarify", "text": "Do you want to tell me what happened?"},
]
# ADR 49 Q4, Q8: a fixture showing all three optional answer stances.
ANSWERS = [
    {"purpose": "accept", "text": "Yes, I'll be there."},
    {"purpose": "decline", "text": "Sorry, I can't make it this time."},
    {"purpose": "later", "text": "Let me check and get back to you."},
]
QUESTION = "We're having a barbecue on Saturday! Are you coming?"


def register(app):
    state = {"identity": 10}

    def conversation():
        return app.state.accounts.get(1)

    def ingest(peer, **fields):
        state["identity"] += 1
        app_ = conversation()
        app_.sync.ingest(
            Message(
                account_id=1,
                peer=peer,
                message_id=state["identity"],
                revision=1,
                date=time.time(),
                **fields,
            )
        )
        return state["identity"]

    def settle_incoming(key):
        """Keep the authored set stable: no background incoming run replaces it."""
        with conversation().store.transaction() as db:
            db.execute(
                "UPDATE jobs SET state='cancelled' WHERE role='incoming_rewrite' AND state='queued' AND scope IN (SELECT id FROM blocks WHERE chat_key=?)",
                (key,),
            )
            db.execute(
                "UPDATE blocks SET state='ready',rendered='Authored combined rewrite.',rendered_revision=revision WHERE chat_key=?",
                (key,),
            )

    async def ready(state_name="ready", options=OPTIONS, code="sad"):
        app_ = conversation()
        key = f"1/{PEER}"
        block = app_.store.rows(
            "SELECT * FROM blocks WHERE chat_key=? ORDER BY date DESC,id DESC LIMIT 1",
            (key,),
        )[0]
        packet = await incoming(app_, block)
        attempt = f"{state['identity']:032x}"
        emotion = app_.emotion.capture(block, packet, attempt)
        with app_.store.transaction() as db:
            # A useful decision (ADR 49 Q2) stands in beside the emotion.
            app_.emotion.publish(db, key, emotion, code, 0, useful=True)
            record = app_.suggestions.capture(db, block, packet, [], code, attempt)
            app_.suggestions.store_record(db, key, record)
            if state_name == "ready":
                app_.suggestions.publish(
                    db, key, attempt, [SuggestionOption(**o) for o in options], 0
                )
            elif state_name == "failed":
                app_.suggestions.fail(db, key, attempt, "format")
            elif state_name == "preparing":
                app_.suggestions.enqueue(db, key, record, attempt, time.time() + 3600)
            elif state_name == "waiting":
                # Queued behind the Daily Budget: held without a spinner.
                app_.suggestions.enqueue(db, key, record, attempt, time.time() + 3600)
                db.execute(
                    "UPDATE jobs SET error='Daily budget reached' WHERE id=?",
                    (attempt,),
                )
        return attempt

    async def setup(state_name, *, options, text, code):
        conversation().store.put_chat(
            Chat(account_id=1, peer=PEER, title="Suggestion fixture", kind="private")
        )
        ingest(PEER, text="I will call you after work.", outgoing=True)
        ingest(PEER, text=text)
        settle_incoming(f"1/{PEER}")
        return await ready(state_name, options=options, code=code)

    # The app's journeys (Maestro) call these without a trusted client, like
    # the members fixture: answer stances to choose from (ADR 49 Q4, Q8) and a
    # decision-model outage to report and clear (Q14).
    @app.post("/fixture/answer-suggestions")
    async def answer_suggestions():
        return {
            "attempt": await setup(
                "ready", options=ANSWERS, text=QUESTION, code="excited"
            )
        }

    @app.post("/fixture/decision-outage")
    async def decision_outage():
        app.state.accounts.registry.set_decision_route(False, "listing")
        return {"ok": True}

    @app.post("/fixture/decision-restored")
    async def decision_restored():
        app.state.accounts.registry.set_decision_route(True)
        return {"ok": True}

    @app.post("/api/test/suggestions")
    async def suggestions(request: Request):
        body = await request.json()
        action = body.get("action")
        app_ = conversation()
        if action == "reset":
            if gate := state.pop("gate", None):
                gate.set()
            if engine := state.pop("engine", None):
                app_.engine = engine
            if decider := state.pop("decider", None):
                app_.decider = decider
            return {"ok": True}
        if action == "setup":
            attempt = await setup(
                body.get("state", "ready"),
                options=body.get("options", OPTIONS),
                text=body.get("text", "Today was really hard at work."),
                code=body.get("code", "sad"),
            )
            return {"attempt": attempt}
        if action == "incoming":
            ingest(PEER, text=body.get("text", "And now the train is late."))
            settle_incoming(f"1/{PEER}")
            return {"ok": True}
        if action == "outgoing":
            ingest(PEER, text="Sent from another client.", outgoing=True)
            return {"ok": True}
        if action == "pipeline":
            state.setdefault("engine", app_.engine)
            previous = state["engine"]
            state["gate"] = gate = asyncio.Event()
            calls = state["calls"] = []
            options = body.get("options", OPTIONS)

            class Scripted:
                name, model, provider = "offline", "authored-suggestions", "local"

                async def generate(self, role, packet):
                    text = json.dumps(packet)
                    if "Pipeline fixture" not in text:
                        return await previous.generate(role, packet)
                    calls.append(role)
                    if role == "incoming_rewrite":
                        return EngineResponse(
                            {
                                "text": "I am worried about tomorrow.",
                                "image_context": [],
                            },
                            actual_nanos=0,
                        )
                    if role == "response_suggestions":
                        await gate.wait()
                        return EngineResponse({"options": options}, actual_nanos=0)
                    return await previous.generate(role, packet)

            state.setdefault("decider", app_.decider)
            deciding = state["decider"]

            class Deciding:
                """ADR 30: the classification after publication reads it."""

                name, model, provider = "offline", "authored-decisions", "local"

                async def generate(self, role, packet):
                    if "Pipeline fixture" not in json.dumps(packet):
                        return await deciding.generate(role, packet)
                    calls.append(role)
                    if role == "suggestion_labels":
                        # ADR 49 Q19: the authored options keep their labels.
                        return EngineResponse(
                            {
                                f"purpose_{index}": label_choice(option["purpose"])
                                for index, option in enumerate(options, 1)
                            },
                            actual_nanos=0,
                        )
                    return EngineResponse(
                        {
                            "emotion": choice("worried", 0.9),
                            "suggestions_useful": {"type": "noul", "noul": 0.9},
                        },
                        actual_nanos=0,
                    )

            app_.engine = Scripted()
            app_.decider = Deciding()
            app_.store.put_chat(
                Chat(
                    account_id=1,
                    peer=PIPELINE,
                    title="Pipeline fixture",
                    kind="private",
                )
            )
            ingest(PIPELINE, text=f"Pipeline fixture {time.time_ns()}")
            with app_.store.transaction() as db:
                db.execute(
                    "UPDATE jobs SET due=0 WHERE state='queued' AND scope IN (SELECT id FROM blocks WHERE chat_key=?)",
                    (f"1/{PIPELINE}",),
                )
            return {"ok": True}
        if action == "release":
            state["gate"].set()
            return {"calls": state.get("calls", [])}
        if action == "calls":
            return {"calls": state.get("calls", [])}
        return {"ok": False}
