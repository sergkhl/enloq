#!/usr/bin/env python3
"""Finite first-attempt check of useful response suggestions (ADR 49 Q13).

Every request goes through the application's own stages and the ordinary
shared accounting, on authored conversations only: none copies a real
message, name, number or identifier. A fake transport means no Telegram read,
send or reaction. Each case's decision request (emotion and usefulness) runs
the real Emotion Classification: a block under the saved Processing Character
Threshold through the held path (ADR 49 Q3), a longer one after an authored
rewrite stand-in, so no incoming rewrite is requested. A block with a usable
non-neutral emotion or, calm, usefulness at 0.7 or more (ADR 56's either
signal and Q19 A) then runs the real Response Suggestions request. Memory is
off.

The service must be stopped; its exclusive owner lock is held throughout. At
most 40 decision and 16 suggestion requests, 1,800 seconds and $0.02 counted
across the Daily and Group Briefing Budgets. A request starts only while the
flat unknown estimate still fits the remaining bound. One first attempt per
case, without retry; owner settings are never changed. A malformed answer or
option set, or a route failure, blocks the switch; weak but well-formed
results are recorded for review as Low quality.
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
from enloq.domain.suggestions import ANSWERS, CALM_USEFULNESS, QUALIFYING
from enloq.inference.decider import DeciderEngine
from enloq.inference.decider import (
    within_adopted_rates as decider_rates_adopted,
)
from enloq.inference.guidance import DEFAULTS_REVISION
from enloq.inference.openrouter import OpenRouterEngine, within_adopted_rates
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.accounts import AccountRegistry
from enloq.storage.lock import OwnerLock
from enloq.storage.store import Store
from enloq.transport.fake import FakeTransport

MAX_DECISIONS, MAX_SUGGESTIONS, SECONDS, MAX_NANOS = 40, 16, 1800, 20_000_000
FAMILIES = ("daily", "group_briefing")
OWNER, RECIPIENT = True, False
LONG = (
    " I know it's a lot to ask and I've been putting it off for days, but the "
    "movers cancelled on me this morning and I honestly don't know who else to "
    "call. It's mostly boxes and the old sofa, nothing too heavy."
)


def said(outgoing, text, **fields):
    return {"outgoing": outgoing, "text": text, **fields}


# (name, earlier messages, the latest block's incoming messages, expected
# emotion, also acceptable, whether suggestions are useful, whether answer
# stances should appear). Short blocks take the held path under the default
# 200-character threshold; long ones get an authored rewrite stand-in.
CASES = [
    (
        "en_neutral_question",
        [],
        ["Are you coming to Anna's party on Saturday?"],
        "neutral",
        ["unknown"],
        True,
        True,  # Neutral and useful: suggested under ADR 56's either signal.
    ),
    (
        "en_excited_invitation",
        [],
        ["We're having a barbecue on Saturday!! You have to come 🎉 Are you in?"],
        "excited",
        ["positive"],
        True,
        True,
    ),
    (
        "ru_worried_request",
        [],
        ["Слушай, можешь завтра забрать Машу из садика? Я застряла на работе 😟"],
        "worried",
        ["frustrated"],
        True,
        True,
    ),
    (
        "en_thanks",
        [said(OWNER, "I've sent you the photos.")],
        ["Thanks!"],
        "positive",
        [],
        False,
        False,
    ),
    (
        "ru_ok",
        [said(OWNER, "Встречаемся в семь у входа.")],
        ["ок"],
        "neutral",
        ["positive"],
        False,
        False,
    ),
    (
        "en_goodnight",
        [said(OWNER, "Night, see you tomorrow.")],
        ["Good night, talk tomorrow 😊"],
        "positive",
        [],
        False,
        False,
    ),
    (
        "mixed_excited_question",
        [],
        ["Короче, мы выиграли! Want to celebrate tonight?"],
        "excited",
        ["positive"],
        True,
        True,
    ),
    (
        "en_sad_news",
        [],
        ["My grandma passed away this morning."],
        "sad",
        [],
        True,
        False,
    ),
    (
        "forwarded_link",
        [],
        [
            said(
                RECIPIENT,
                "Authored article about city parking https://example.com/parking",
                forwarded=True,
                forwarded_from="Authored City News",
            )
        ],
        "neutral",
        ["unknown"],
        False,
        False,
    ),
    (
        "en_answer_closes",
        [said(OWNER, "What time works for you?")],
        ["7 works for me."],
        "neutral",
        ["positive"],
        False,
        False,
    ),
    (
        "ru_frustrated_question",
        [said(RECIPIENT, "Ну что, едем в субботу?")],
        ["Ты опять не ответил. Мы едем или нет?"],
        "frustrated",
        ["angry"],
        True,
        True,
    ),
    (
        "en_confused",
        [said(OWNER, "See you at the dentist's.")],
        ["Wait, is it today or tomorrow?"],
        "confused",
        [],
        True,
        False,
    ),
    (
        "en_long_worried_request",
        [],
        ["Could you help me move this Saturday from ten?" + LONG],
        "worried",
        ["frustrated", "sad"],
        True,
        True,
    ),
    (
        "ru_long_excited_news",
        [],
        [
            "Ура!!! Нам наконец одобрили ипотеку, мы переезжаем в декабре! "
            "Я до сих пор не верю, полгода ждали ответа банка и уже думали, что "
            "ничего не выйдет, а сегодня позвонили и сказали, что всё одобрено. "
            "Вечером будем отмечать с родителями."
        ],
        "excited",
        ["positive"],
        True,
        False,
    ),
    (
        "en_long_information",
        [],
        [
            "For everyone on the list: the meeting moves to room 4B on the second "
            "floor from next week, same time. The projector there is newer, the "
            "coffee machine is next door, and nothing else changes for anyone. "
            "No need to reply."
        ],
        "neutral",
        ["unknown"],
        False,
        False,
    ),
    (
        "mixed_long_invitation",
        [],
        [
            "Привет! Мы с Олей устраиваем housewarming в следующую пятницу, really "
            "hope you can make it 😊 Будет немного людей, домашняя еда и настолки, "
            "начнём часов в семь, а закончим, когда все устанут. Адрес тот же, "
            "что и в прошлый раз. Сможешь прийти?"
        ],
        "positive",
        ["excited"],
        True,
        True,
    ),
]


class Bounds:
    """One allowance for both routes, counted in both budget families."""

    def __init__(self, registry, timezone):
        self.registry, self.timezone = registry, timezone
        self.initial = {
            family: registry.budget(timezone=timezone, family=family)
            for family in FAMILIES
        }
        self.started = time.monotonic()
        self.requests = {"decision": 0, "suggestion": 0}

    def spent(self):
        states = {
            family: self.registry.budget(timezone=self.timezone, family=family)
            for family in FAMILIES
        }
        return states, sum(
            states[family]["spent"] - self.initial[family]["spent"]
            for family in FAMILIES
        )

    def admit(self):
        states, spent = self.spent()
        if (
            time.monotonic() - self.started >= SECONDS
            or spent + FLAT_UNKNOWN > MAX_NANOS
            or any(state["reached"] for state in states.values())
        ):
            raise Refused("Finite check or shared daily bound reached")

    def left(self, kind):
        limit = MAX_DECISIONS if kind == "decision" else MAX_SUGGESTIONS
        return self.requests[kind] < limit


class ObservedDecider(DeciderEngine):
    def __init__(self, settings, registry, bounds):
        super().__init__(settings, user=registry.provider_user())
        self.bounds, self.current = bounds, None

    async def generate(self, role, packet):
        if role not in ("emotion_classification", "suggestion_labels"):
            raise Refused("This finite pass makes incoming decisions only")
        self.bounds.requests["decision"] += 1
        observation = self.current
        started = time.monotonic()
        if role == "suggestion_labels":
            # ADR 49 Q19: the labels of a written set, one request per set.
            try:
                response = await super().generate(role, packet)
                observation["label_answers"] = response.content
                return response
            finally:
                observation["label_seconds"] = time.monotonic() - started
        observation["state"] = packet["state"]
        try:
            response = await super().generate(role, packet)
            observation["answers"] = response.content
            return response
        finally:
            observation["decision_seconds"] = time.monotonic() - started


class ObservedRoute(OpenRouterEngine):
    def __init__(self, settings, registry, bounds):
        super().__init__(settings, user=registry.provider_user())
        self.bounds, self.current = bounds, None

    async def generate(self, role, packet):
        if role != "response_suggestions":
            raise Refused("This finite pass requests suggestions only")
        self.bounds.requests["suggestion"] += 1
        observation = self.current
        observation["suggestion_packet"] = {
            k: v for k, v in packet.items() if k != "guidance"
        }
        started = time.monotonic()
        try:
            return await super().generate(role, packet)
        finally:
            observation["suggestion_seconds"] = time.monotonic() - started


def verdict(code, expected, acceptable):
    if code is None:
        return None
    return (
        "match" if code == expected else "acceptable" if code in acceptable else "miss"
    )


async def observe(
    settings=None,
    *,
    root=ROOT,
    decider_factory=ObservedDecider,
    route_factory=ObservedRoute,
):
    settings = settings or Settings.load("personal")
    with OwnerLock(root / ".local/service.owner.lock"):
        registry = AccountRegistry(settings.data)
        bounds = Bounds(registry, settings.timezone)
        evidence = Path(
            tempfile.mkdtemp(prefix="useful-suggestions-check-", dir=root / ".local")
        )
        report = {
            "started": time.time(),
            "bounds": {
                "decisions": MAX_DECISIONS,
                "suggestions": MAX_SUGGESTIONS,
                "seconds": SECONDS,
                "counted_nanos": MAX_NANOS,
            },
            "telegram_calls": 0,
            "initial_budget": bounds.initial,
            "defaults_revision": DEFAULTS_REVISION,
            "threshold": registry.processing_threshold(),
            "results": [],
            "blocking": [],
        }
        decider = decider_factory(settings, registry, bounds)
        route = route_factory(settings, registry, bounds)

        def save():
            report["elapsed_seconds"] = time.monotonic() - bounds.started
            report["requests"] = dict(bounds.requests)
            report["final_budget"], report["counted_test_nanos"] = bounds.spent()
            private_write(
                evidence / "report.json",
                json.dumps(report, ensure_ascii=False, indent=2),
            )

        try:
            async with asyncio.timeout(SECONDS):
                if not registry.suggestions_enabled():
                    raise Refused("Suggestions are Off; the owner's choice is kept")
                # Verifies both exact routes before any request.
                await decider.prepare({})
                await route.prepare({})
                report["routes"] = {
                    "decision": decider.rates,
                    "suggestion": route.rates,
                }
                if not decider_rates_adopted(decider.rates) or not (
                    within_adopted_rates(route.rates)
                ):
                    raise Refused("Rate increase requires reviewing finite cost bounds")
                save()
                with tempfile.TemporaryDirectory(
                    prefix="useful-suggestions-fixture-", dir=root / ".local"
                ) as directory:
                    store = Store(
                        Path(directory) / "rewriting.sqlite3",
                        account_id=1,
                        mode="personal",
                        timezone=settings.timezone,
                        shared_path=registry.path,
                    )
                    transport = FakeTransport()
                    app = Conversations(store, transport, route, decider)
                    # ADR 54: a long case keeps its authored rewrite stand-in here.
                    store.rewrites_text = True
                    # Calming decides first (ADR 53); scripts/check-calming.py checks it.
                    app.calming.default = False
                    work = app.rewriting.work
                    work.guidance = registry.guidance
                    work.admit = bounds.admit
                    app.suggestions.enabled = registry.suggestions_enabled
                    app.memory.enabled = lambda: False
                    try:
                        for index, case in enumerate(CASES):
                            if not bounds.left("decision"):
                                raise Refused("The decision request bound is reached")
                            bounds.admit()
                            await run_case(
                                app, store, decider, route, bounds, report, index, case
                            )
                            save()
                        assert not transport.deliveries
                        assert not transport.reaction_calls
                    finally:
                        tasks = list(app.tasks)
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        store.close()
        except TimeoutError:
            report["stopped"] = "Thirty-minute deadline reached"
        except Refused as failure:
            report["stopped"] = str(failure)
        finally:
            results = report["results"]
            report["summary"] = {
                "cases": len(results),
                "decided": sum(
                    r.get("decision", {}).get("state") == "ready" for r in results
                ),
                "emotion": {
                    v: sum(r.get("emotion_verdict") == v for r in results)
                    for v in ("match", "acceptable", "miss")
                },
                "useful_miss": sum(r.get("useful_verdict") == "miss" for r in results),
                "suggested": sum(bool(r.get("options")) for r in results),
                "answers_missing": sum(bool(r.get("answers_missing")) for r in results),
            }
            save()
            report["within_bounds"] = (
                report["requests"]["decision"] <= MAX_DECISIONS
                and report["requests"]["suggestion"] <= MAX_SUGGESTIONS
                and report["counted_test_nanos"] <= MAX_NANOS
                and report["elapsed_seconds"] <= SECONDS
            )
            save()
            await decider.client.aclose()
            await route.client.aclose()
            print(
                json.dumps(
                    {
                        "evidence": str(evidence / "report.json"),
                        "within_bounds": report["within_bounds"],
                        "requests": report["requests"],
                        "counted_test_nanos": report["counted_test_nanos"],
                        "blocking": report["blocking"],
                        "stopped": report.get("stopped"),
                        "summary": report["summary"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )


async def run_case(app, store, decider, route, bounds, report, index, case):
    name, earlier, latest, expected, acceptable, useful, answers = case
    peer = f"user:{930 + index}"
    key = f"1/{peer}"
    store.put_chat(
        Chat(
            account_id=1,
            peer=peer,
            title="Authored useful-suggestions check",
            kind="private",
            ai_eligible=True,
        ),
        activity=time.time(),
    )
    value = {
        "name": name,
        "earlier": earlier,
        "latest": latest,
        "expected": expected,
        "acceptable": acceptable,
        "expected_useful": useful,
        "expected_answers": answers,
    }
    report["results"].append(value)
    decider.current = route.current = value
    sources = [
        *earlier,
        *(said(RECIPIENT, m) if isinstance(m, str) else m for m in latest),
    ]
    start = time.time() - 60 - len(sources)
    for number, source in enumerate(sources, 1):
        fields = {k: source[k] for k in ("forwarded", "forwarded_from") if k in source}
        app.sync.ingest(
            Message(
                account_id=1,
                peer=peer,
                message_id=number,
                revision=1,
                date=start + number,
                text=source["text"],
                outgoing=source["outgoing"],
                **fields,
            ),
            # Earlier messages are read history; the latest block is new.
            target=number > len(earlier),
        )
    block = store.rows(
        "SELECT * FROM blocks WHERE chat_key=? ORDER BY date DESC LIMIT 1", (key,)
    )[0]
    if store.rows("SELECT 1 FROM incoming_deferred WHERE scope=?", (block["id"],)):
        # Held under the threshold: its quiet is skipped for this authored pass.
        value["path"] = "held"
        with store.transaction() as db:
            db.execute(
                "UPDATE incoming_deferred SET due=0 WHERE scope=?", (block["id"],)
            )
        app.emotion.decide_held()
    else:
        value["path"] = "rewritten"
        job = store.rows(
            "SELECT * FROM jobs WHERE scope=? AND role='incoming_rewrite' AND state='queued'",
            (block["id"],),
        )[0]
        packet = await incoming(app, block)
        signal = app.emotion.capture(block, packet, job["id"])
        # The authored stand-in for a validated rewrite's ready transaction.
        with store.transaction() as db:
            db.execute("UPDATE jobs SET state='done' WHERE id=?", (job["id"],))
            db.execute(
                "UPDATE blocks SET state='ready',rendered='Authored stand-in rewrite.',rendered_revision=revision WHERE id=?",
                (block["id"],),
            )
            app.emotion.schedule(db, block, job, packet, [], signal)
    queued = store.rows(
        "SELECT * FROM jobs WHERE scope=? AND role='emotion_classification' AND state='queued'",
        (key,),
    )
    if not queued:
        raise Refused(f"{name}: the block cannot be decided")
    assert app.rewriting.claim(queued[0])
    await app.emotion.classify(queued[0])
    record = app.emotion.record(key) or {}
    value["decision"] = {
        k: record.get(k)
        for k in ("state", "code", "useful", "usefulness", "decision", "failure")
    }
    value["decision_receipt"] = store.rows(
        "SELECT model,provider,usage,error FROM receipts WHERE id=?",
        (queued[0]["id"],),
    )
    if record.get("state") != "ready":
        # A malformed answer or a route failure blocks the switch.
        report["blocking"].append(name)
        return
    value["emotion_verdict"] = verdict(record["code"], expected, acceptable)
    value["useful_verdict"] = "match" if record["useful"] == useful else "miss"
    attempt = store.rows(
        "SELECT * FROM jobs WHERE scope=? AND role='response_suggestions' AND state='queued'",
        (key,),
    )
    if not attempt:
        value["suggestions"] = (
            "not_qualifying"
            if not (
                record["code"] in QUALIFYING
                or (record["usefulness"] or 0) >= CALM_USEFULNESS
            )
            else "not_queued"
        )
        return
    if not bounds.left("suggestion"):
        value["suggestions"] = "suggestion request bound reached"
        return
    bounds.admit()
    assert app.rewriting.claim(attempt[0])
    await app.suggestions.run(attempt[0])
    outcome = app.suggestions.record(key) or {}
    value["suggestions"] = outcome.get("state")
    value["suggestion_failure"] = outcome.get("failure")
    value["options"] = outcome.get("options") or []
    value["suggestion_receipt"] = store.rows(
        "SELECT model,provider,usage,error FROM receipts WHERE id=?",
        (attempt[0]["id"],),
    )
    if outcome.get("state") == "failed":
        report["blocking"].append(name)
        return
    stances = {o["purpose"] for o in value["options"]} & ANSWERS
    value["answers_missing"] = answers and not stances


if __name__ == "__main__":
    os.umask(0o077)
    asyncio.run(observe())
