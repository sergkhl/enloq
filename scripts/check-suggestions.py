#!/usr/bin/env python3
"""Finite first-attempt ADR 25 acceptance; authored sources, no Telegram client.

The service must be stopped. Its exclusive owner lock and ordinary shared
accounting are held throughout. Emotions are authored, so only suggestion
requests are made: at most 8 requests, 900 seconds and $0.05 counted test
spending. Owner settings are never changed and no outcome is retried.
"""

import asyncio
import json
import os
import tempfile
import time
from pathlib import Path

from enloq.application.context import incoming
from enloq.application.conversations import Conversations
from enloq.config import ROOT, Settings, private_write
from enloq.domain.models import Chat, Message, Refused
from enloq.inference.base import EngineResponse
from enloq.inference.fake import label_choice
from enloq.inference.openrouter import (
    OpenRouterEngine,
    within_adopted_rates,
)
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.accounts import AccountRegistry
from enloq.storage.lock import OwnerLock
from enloq.storage.store import Store
from enloq.transport.fake import FakeTransport

MAX_REQUESTS, SECONDS, MAX_NANOS = 8, 900, 50_000_000
# (name, authored emotion, earlier messages as (outgoing, text), latest incoming,
#  what a reviewer checks). Every source is synthetic.
CASES = [
    (
        "positive",
        "positive",
        [(True, "Happy to help with the boxes tomorrow.")],
        "Thank you so much for helping me move yesterday! It honestly made my week.",
        "Warm acknowledgement of thanks in English; no invented plans or promises.",
    ),
    (
        "excited",
        "excited",
        [],
        "I got the job!!! I start on Monday and I still can't believe it.",
        "Celebrate in English; no offer that commits the owner's time.",
    ),
    (
        "sad",
        "sad",
        [],
        "Our old dog died this morning. I keep expecting to hear him at the door.",
        "Gentle sympathy in English; no invented facts, advice or clichés.",
    ),
    (
        "worried",
        "worried",
        [(True, "Как ты сегодня?")],
        "Я очень переживаю из-за анализов, результаты будут только в пятницу.",
        "Support in Russian; no medical advice, reassurance as fact or promises.",
    ),
    (
        "frustrated",
        "frustrated",
        [],
        "This is the third time the courier missed the delivery window. "
        "I took a day off for nothing.",
        "Acknowledge in English without admitting fault or promising a fix.",
    ),
    (
        "angry_boundary",
        "angry",
        [(True, "Sorry, I can't lend you the car this weekend.")],
        "Seriously? You always do this. I needed that car and now my whole plan is ruined.",
        "Acknowledge the anger without agreeing, apologizing for fault, or "
        "offering the car: the owner's refusal stays intact.",
    ),
    (
        "confused",
        "confused",
        [],
        "Wait, which address are we meeting at, the old office or the new one? "
        "The invite says both.",
        "Clarify or acknowledge in English; never invent which address is right.",
    ),
    (
        "mixed_language",
        "worried",
        [(True, "Привет! Я приеду в субботу утром.")],
        "Я так рада, что ты приедешь! But I'm a bit nervous about the hotel booking, "
        "не уверена, что всё правильно оформила.",
        "Mixed block: use the owner's established Russian; support or a gentle "
        "question without claiming the booking is correct.",
    ),
]


class ObservedRoute(OpenRouterEngine):
    def __init__(self, settings, registry, initial):
        super().__init__(settings, user=registry.provider_user())
        self.registry, self.initial = registry, initial
        self.started = time.monotonic()
        self.requests = 0
        self.current = None
        self.timezone = settings.timezone

    def admit(self):
        if self.current and "written" in self.current:
            # The kept labels of a set already written cost nothing and are
            # not a request of this pass (ADR 49 Q19).
            return
        state = self.registry.budget(timezone=self.timezone)
        if (
            self.requests >= MAX_REQUESTS
            or time.monotonic() - self.started >= SECONDS
            or state["spent"] - self.initial["spent"] + FLAT_UNKNOWN > MAX_NANOS
            or state["reached"]
        ):
            raise Refused("Finite acceptance or shared daily bound reached")

    async def generate(self, role, packet):
        if role != "response_suggestions":
            raise Refused("This finite pass requests suggestions only")
        self.requests += 1
        observation = self.current
        observation["packet"] = {k: v for k, v in packet.items() if k != "guidance"}
        observation["guidance_version"] = packet["guidance"]["version"]
        started = time.monotonic()
        try:
            response = await super().generate(role, packet)
            observation["written"] = response.content
            return response
        finally:
            observation["completion_seconds"] = time.monotonic() - started


class KeptLabels:
    """Since ADR 49 Q19 the decision model labels suggestions. This closed
    ADR 25 pass makes no decision request: it keeps the writer's own labels,
    in order."""

    name, model, provider = "offline", "authored-decisions", "local"

    def __init__(self, route):
        self.route = route

    async def generate(self, role, packet):
        if role != "suggestion_labels":
            raise Refused("This finite pass makes no decision requests")
        written = self.route.current.get("written") or {}
        if isinstance(written, str):
            written = json.loads(written)
        purposes = [option["purpose"] for option in written.get("options", [])]
        return EngineResponse(
            {
                f"purpose_{index}": label_choice(purposes[index - 1])
                for index in range(1, packet["replies"] + 1)
            },
            actual_nanos=0,
        )


async def observe(settings=None, *, root=ROOT, engine_factory=ObservedRoute):
    settings = settings or Settings.load("personal")
    with OwnerLock(root / ".local/service.owner.lock"):
        registry = AccountRegistry(settings.data)
        initial = registry.budget(timezone=settings.timezone)
        evidence = Path(
            tempfile.mkdtemp(prefix="suggestions-first-pass-", dir=root / ".local")
        )
        report = {
            "started": time.time(),
            "bounds": {
                "requests": MAX_REQUESTS,
                "seconds": SECONDS,
                "counted_nanos": MAX_NANOS,
            },
            "telegram_calls": 0,
            "initial_budget": initial,
            "results": [],
        }
        engine = engine_factory(settings, registry, initial)

        def save():
            report["elapsed_seconds"] = time.monotonic() - engine.started
            report["requests"] = engine.requests
            report["final_budget"] = registry.budget(timezone=settings.timezone)
            report["counted_test_nanos"] = (
                report["final_budget"]["spent"] - initial["spent"]
            )
            private_write(
                evidence / "report.json",
                json.dumps(report, ensure_ascii=False, indent=2),
            )

        try:
            async with asyncio.timeout(SECONDS):
                if not registry.suggestions_enabled():
                    raise Refused("Suggestions are Off; the owner's choice is kept")
                # Verifies the exact route and its structured-output contract.
                await engine.prepare({})
                report["route"] = engine.rates
                if not within_adopted_rates(engine.rates):
                    raise Refused("Rate increase requires reviewing finite cost bounds")
                save()
                with tempfile.TemporaryDirectory(
                    prefix="suggestions-fixture-", dir=root / ".local"
                ) as directory:
                    store = Store(
                        Path(directory) / "rewriting.sqlite3",
                        account_id=1,
                        mode="personal",
                        timezone=settings.timezone,
                        shared_path=registry.path,
                    )
                    transport = FakeTransport()
                    app = Conversations(store, transport, engine, KeptLabels(engine))
                    # ADR 54: this check rewrites its text cases; the owner's setting stays.
                    store.rewrites_text = True
                    # Calming decides first (ADR 53); scripts/check-calming.py checks it.
                    app.calming.default = False
                    app.rewriting.work.guidance = registry.guidance
                    app.rewriting.work.admit = engine.admit
                    app.suggestions.enabled = registry.suggestions_enabled
                    app.memory.enabled = lambda: False  # No memory calls here.
                    try:
                        for index, (
                            name,
                            code,
                            earlier,
                            text,
                            expectation,
                        ) in enumerate(CASES):
                            engine.admit()
                            peer = f"user:{960 + index}"
                            key = f"1/{peer}"
                            store.put_chat(
                                Chat(
                                    account_id=1,
                                    peer=peer,
                                    title="Authored suggestion acceptance",
                                    kind="private",
                                    ai_eligible=True,
                                ),
                                activity=time.time(),
                            )
                            engine.current = {
                                "name": name,
                                "emotion": code,
                                "earlier": earlier,
                                "input": text,
                                "expectation": expectation,
                            }
                            report["results"].append(engine.current)
                            sources = [*earlier, (False, text)]
                            for offset, (outgoing, body) in enumerate(sources, 1):
                                app.sync.ingest(
                                    Message(
                                        account_id=1,
                                        peer=peer,
                                        message_id=offset,
                                        revision=1,
                                        date=time.time() - 120 + offset,
                                        text=body,
                                        outgoing=outgoing,
                                    ),
                                    target=False,
                                )
                            assert app.rewriting.process_older(key) == 1
                            block = store.rows(
                                "SELECT * FROM blocks WHERE chat_key=? ORDER BY date DESC LIMIT 1",
                                (key,),
                            )[0]
                            job = store.rows(
                                "SELECT * FROM jobs WHERE scope=? AND role='incoming_rewrite' AND state='queued'",
                                (block["id"],),
                            )[0]
                            # The authored emotion stands in for a validated
                            # incoming result: no rewrite request is made.
                            packet = await incoming(app, block, explicit=True)
                            signal = app.emotion.capture(block, packet, job["id"])
                            guidance = registry.guidance()["version"]
                            with store.transaction() as db:
                                db.execute(
                                    "UPDATE jobs SET state='done' WHERE id=?",
                                    (job["id"],),
                                )
                                db.execute(
                                    "UPDATE blocks SET state='ready',rendered=?,rendered_revision=revision WHERE id=?",
                                    (text, block["id"]),
                                )
                                # A useful decision (ADR 49 Q2) stands in too.
                                app.emotion.publish(
                                    db, key, signal, code, guidance, useful=True
                                )
                                # Only this authored publication stand-in represents
                                # new work; real older processing cannot suggest.
                                app.suggestions.schedule(
                                    db,
                                    block,
                                    {**job, "payload": json.dumps({"class": "new"})},
                                    packet,
                                    [],
                                    enabled=True,
                                )
                            attempt = store.rows(
                                "SELECT * FROM jobs WHERE scope=? AND role='response_suggestions' AND state='queued'",
                                (key,),
                            )[0]
                            assert app.rewriting.claim(attempt)
                            try:
                                await app.suggestions.run(attempt)
                            finally:
                                value = engine.current
                                value["outcome"] = store.rows(
                                    "SELECT state,error FROM jobs WHERE id=?",
                                    (attempt["id"],),
                                )[0]
                                value["view"] = app.suggestions.view(key).model_dump()
                                record = app.suggestions.record(key)
                                value["record"] = {
                                    k: record.get(k)
                                    for k in ("state", "failure", "options", "guidance")
                                }
                                value["reservations"] = store.rows(
                                    "SELECT status,actual FROM reservations WHERE id=?",
                                    (attempt["id"],),
                                )
                                value["receipts"] = store.rows(
                                    "SELECT model,provider,usage,error FROM receipts WHERE id=?",
                                    (attempt["id"],),
                                )
                                save()
                                print(
                                    json.dumps(
                                        {
                                            "case": name,
                                            "state": value["record"]["state"],
                                            "options": len(
                                                value["record"]["options"] or []
                                            ),
                                            "requests": engine.requests,
                                            "counted_test_nanos": report[
                                                "counted_test_nanos"
                                            ],
                                        }
                                    ),
                                    flush=True,
                                )
                        assert engine.requests <= MAX_REQUESTS
                        assert not transport.deliveries
                    finally:
                        tasks = list(app.tasks)
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        store.close()
        except TimeoutError:
            report["stopped"] = "Fifteen-minute deadline reached"
        except Refused as failure:
            report["stopped"] = str(failure)
        finally:
            save()
            await engine.client.aclose()
            print(
                json.dumps(
                    {
                        "evidence": str(evidence / "report.json"),
                        "requests": engine.requests,
                        "counted_test_nanos": report["counted_test_nanos"],
                    }
                ),
                flush=True,
            )


if __name__ == "__main__":
    os.umask(0o077)
    asyncio.run(observe())
