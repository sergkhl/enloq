#!/usr/bin/env python3
"""Finite first-attempt ADR 29 checks of the exact MiMo/Xiaomi Model Route.

--check 1: the new route with the current guidance, covering every role it
serves: at most 16 requests, 1,800 seconds and $0.05 counted test spending.
Since ADR 30, Recipient Emotion and Reaction Assessment are checked on their
own route by scripts/check-emotion.py.
--check 2: structured incoming and speech rewriting: at most 12 requests,
1,200 seconds and $0.03 counted test spending.
--check 3: the structured-rewriting improvement over all thirteen authored
incoming cases: at most 16 requests, 1,800 seconds and $0.03.

The service must be stopped: its exclusive owner lock and the ordinary shared
accounting are held throughout. Every case is wholly authored; none copies a
real message, name, number, date, link or identifier. A fake transport means
no Telegram read, send or reaction, and voice notes carry authored transcripts
through a stand-in recognizer, so no audio exists or reaches the provider.
One first attempt per case, without retry or resampling; owner settings are
never changed. Format outcomes go to the report only: the registry's format
counts measure the running route, which ADR 29 restarts at the switch.
"""

import argparse
import asyncio
import io
import json
import os
import tempfile
import time
from pathlib import Path

from enloq.application.context import incoming
from enloq.application.conversations import Conversations
from enloq.config import ROOT, Settings, private_write
from enloq.domain.models import (
    Chat,
    MediaInfo,
    Message,
    Refused,
)
from enloq.inference.guidance import DEFAULTS_REVISION
from enloq.inference.openrouter import (
    OpenRouterEngine,
    within_adopted_rates,
)
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.accounts import AccountRegistry
from enloq.storage.lock import OwnerLock
from enloq.storage.store import Store
from enloq.transport.fake import FakeTransport
from PIL import Image, ImageDraw

BOUNDS = {
    1: {"requests": 16, "seconds": 1800, "counted_nanos": 50_000_000},
    2: {"requests": 12, "seconds": 1200, "counted_nanos": 30_000_000},
    # The structured-rewriting improvement: every authored incoming case of
    # Checks 1 and 2 must keep its content (owner's 2026-10-09 decision).
    3: {"requests": 16, "seconds": 1800, "counted_nanos": 30_000_000},
}


def text(body, **fields):
    return {"text": body, **fields}


def voice(transcript):
    return {"voice": transcript}


# Incoming cases: (name, sources, what a reviewer checks). A source is authored
# text, an authored transcript or an authored image, optionally forwarded.
CHECK_1_INCOMING = [
    (
        "english_fragments",
        [
            text("hey"),
            text("so about saturday"),
            text("can you still bring the speaker? mine broke"),
            text("and pls tell me by thu if not, then i'll rent one"),
        ],
        "One combined request: the speaker for Saturday, the Thursday deadline and "
        "the rental fallback, in the sender's casual voice.",
    ),
    (
        "russian_block",
        [
            text("Привет! Посмотрела вчера квартиру на тихой улице у парка."),
            text("Хозяйка хочет залог сразу за два месяца, по-моему, это много."),
            text("Можешь спросить у знакомого юриста, это вообще нормально?"),
        ],
        "Russian kept; the deposit concern stays the sender's opinion; the request "
        "to ask a lawyer is preserved.",
    ),
    (
        "mixed_language_requests",
        [
            text("Привет, can you send me the invoice from last month?"),
            text("И ещё напомни, пожалуйста, во сколько завтра встреча"),
            text("also not sure the address in the invite is right, проверь плз"),
        ],
        "Three distinct requests kept apart (invoice, meeting time, address "
        "check), the same shape as the retained mixed-language Low quality case.",
    ),
    (
        "image_with_text",
        [
            text(
                "Посмотри, это нормальный счёт? Мне кажется, доставку посчитали дважды"
            ),
            {"image": True},
        ],
        "The question stays a question; hidden image context notes the two "
        "identical delivery lines without inserting a description into the text.",
    ),
    (
        "voice_transcript",
        [
            voice(
                "Hi, um, so it's about the, the dentist thing. I can't do Tuesday, "
                "no wait, I can do Tuesday but only after four. Could you maybe ask "
                "them if, like, half past four works? Thanks."
            )
        ],
        "Tuesday after four with the half-past-four question; the self-correction "
        "resolves to its final version without inventing confirmation.",
    ),
]
CHECK_2_INCOMING = [
    (
        "two_topic_burst",
        [
            text("ok so the landlord came by"),
            text("he says the heater will be fixed on monday"),
            text("btw did you get my package??"),
            text("it says delivered but i'm not sure"),
            text("also the heater guy needs someone home 9-12"),
        ],
        "Two short paragraphs in order of first mention: the heater (Monday, "
        "someone home 9-12) then the package question; no summary line.",
    ),
    (
        "parallel_items",
        [
            text(
                "For the cabin trip can you check whether there are towels, if dogs "
                "are allowed, what time check-in is and whether there's parking?"
            )
        ],
        "Four parallel questions as bullet lines; no Markdown.",
    ),
    (
        "single_point",
        [text("Running 10 minutes late, sorry!")],
        "Stays one natural sentence.",
    ),
    (
        "rambling_transcript",
        [
            voice(
                "So, um, yeah, I talked to, uh, Miroslava? Or Miroslav, I'm not sure, "
                "from the bank, and they said the transfer, like, the transfer should "
                "arrive on Wednesday, no, sorry, Thursday. I think. Um, and they need "
                "a copy of your passport, but I'm not a hundred percent sure it's the "
                "passport, maybe it's the ID card. Anyway, call me back."
            )
        ],
        "Clear written text: Thursday (final version), the sender's hedges kept as "
        "words, a possibly misheard name marked with (?), the document doubt and "
        "the call-back request kept.",
    ),
    (
        "text_and_voice_repeat",
        [
            text("Don't forget the cake for Sunday!"),
            voice(
                "Hey, just reminding you, the cake, for Sunday, don't forget it, and, "
                "um, can you also bring candles? Like ten or so."
            ),
        ],
        "The repeated cake reminder appears once, with the candles request.",
    ),
    (
        "forwarded_notice",
        [
            text(
                "Water will be off on Monday from 10:00 to 14:00 for pipe repairs.",
                forwarded_from="Authored building management",
            ),
            text("fyi this is our building, fill some bottles"),
        ],
        "The notice stays attributed to its author; the sender's own suggestion "
        "stays the sender's.",
    ),
    (
        "russian_speech",
        [
            voice(
                "Ну, короче, я, э-э, сегодня не успеваю, давай завтра, нет, "
                "послезавтра лучше, после обеда. И это, захвати зарядку, если не "
                "сложно."
            )
        ],
        "Russian written text: the day after tomorrow in the afternoon, the "
        "charger request; fillers gone.",
    ),
    (
        "mixed_language_regression",
        [
            text("Привет! Can you check the contract before Friday?"),
            text("особенно пункт про штрафы, I'm worried about it"),
            text("и скажи, если надо что-то поменять"),
        ],
        "Requests kept distinct (check before Friday, the penalty clause, say what "
        "to change) with the worry kept.",
    ),
]
OUTGOING = [
    (
        "outgoing_neutral",
        "english_fragments",
        "neutral",
        "cant do friday sorry, maybe next week? lmk which day works for u",
        "Neutral; keeps the refusal, the next-week offer and the question; keeps the "
        "owner's single-paragraph shape.",
    ),
    (
        "outgoing_professional",
        "russian_block",
        "professional",
        "Я сейчас не могу одолжить деньги. Могу помочь найти вариант подешевле на "
        "выходных.",
        "Professional in Russian; the refusal and the weekend offer stay; no "
        "invented promise or apology for fault (Adaptive left with ADR 53).",
    ),
]
MEMORY = {
    "name": "memory_consolidation",
    "messages": [
        "Договорились: я покупаю билеты, ты бронируешь гостиницу.",
        "Ок, гостиница забронирована с 14 по 16 ноября.",
    ],
    "existing": [
        ("trip-a", "Planning a trip together in November.", [80]),
        ("trip-b", "A November trip is being planned together.", [81]),
    ],
    "expectation": "One cited agreement (tickets/hotel, 14-16 November); the two "
    "redundant generated entries merged with their citations.",
}
SUGGESTION = {
    "name": "response_suggestions",
    "emotion": "worried",
    "text": "My flight got cancelled and the next one is only on Thursday. "
    "I don't know what to do about the hotel.",
    "expectation": "Up to three short options: acknowledgement, support or a "
    "gentle question; no invented facts, promises or advice as fact.",
}


class ObservedRoute(OpenRouterEngine):
    def __init__(self, settings, registry, initial, bounds):
        super().__init__(settings, user=registry.provider_user())
        self.registry, self.initial, self.bounds = registry, initial, bounds
        self.started = time.monotonic()
        self.requests = 0
        self.current = None
        self.timezone = settings.timezone

    def admit(self):
        state = self.registry.budget(timezone=self.timezone)
        if (
            self.requests >= self.bounds["requests"]
            or time.monotonic() - self.started >= self.bounds["seconds"]
            or state["spent"] - self.initial["spent"] + FLAT_UNKNOWN
            > self.bounds["counted_nanos"]
            or state["reached"]
        ):
            raise Refused("Finite check or shared daily bound reached")

    def observe(self, role, packet):
        self.requests += 1
        call = {
            "role": role,
            "guidance_version": packet["guidance"]["version"],
            "defaults_revision": packet["guidance"].get("defaults_revision"),
            "attached_images": len(packet.get("images", [])),
            "packet": {
                k: v for k, v in packet.items() if k not in {"guidance", "images"}
            },
        }
        self.current.setdefault("calls", []).append(call)
        return call

    async def generate(self, role, packet):
        call, started = self.observe(role, packet), time.monotonic()
        try:
            return await super().generate(role, packet)
        finally:
            call["completion_seconds"] = time.monotonic() - started

    async def generate_stream(self, role, packet, on_text):
        call, started = self.observe(role, packet), time.monotonic()

        def show(value):
            if value.strip() and "first_text_seconds" not in call:
                call["first_text_seconds"] = time.monotonic() - started
            on_text(value)

        try:
            response = await super().generate_stream(role, packet, show)
            # Authored cases only, so the raw output may support diagnosis.
            call["raw"] = response.content
            return response
        except Exception as failure:
            receipt = getattr(failure, "receipt", None)
            call["raw"] = receipt.content if receipt else None
            raise
        finally:
            call["completion_seconds"] = time.monotonic() - started


class AuthoredSpeech:
    """Stands in for local Whisper: returns the authored transcript stored as
    the fake audio bytes. No audio exists and nothing leaves the machine."""

    async def transcribe(self, path):
        return {"text": Path(path).read_bytes().decode()[len("authored:") :]}


def bill_image():
    picture = Image.new("RGB", (420, 220), "white")
    draw = ImageDraw.Draw(picture)
    lines = [
        "AUTHORED SAMPLE BILL",
        "Chair x2 ........ 4,000",
        "Delivery ........ 300",
        "Delivery ........ 300",
        "Total ........... 4,600",
    ]
    for index, line in enumerate(lines):
        draw.text((20, 20 + 38 * index), line, fill="black", font_size=22)
    target = io.BytesIO()
    picture.save(target, format="PNG")
    return target.getvalue()


async def observe(
    check,
    settings=None,
    *,
    root=ROOT,
    engine_factory=ObservedRoute,
    cases=None,
    requests=None,
    seconds=None,
    counted_nanos=None,
    note=None,
):
    """``cases`` and lower ``requests``/``seconds``/``counted_nanos`` select a
    separately recorded continuation after a concrete contract correction; they
    can never raise a check's recorded bounds."""
    bounds = dict(BOUNDS[check])
    for key, value in (
        ("requests", requests),
        ("seconds", seconds),
        ("counted_nanos", counted_nanos),
    ):
        if value is not None:
            if not 0 < value <= bounds[key]:
                raise ValueError("A continuation can only lower a recorded bound")
            bounds[key] = value
    names = {c[0] for c in CHECK_1_INCOMING + CHECK_2_INCOMING}
    if cases and (check != 2 or not set(cases) <= names):
        raise ValueError("Case selection applies to Check 2's authored cases")
    settings = settings or Settings.load("personal")
    with OwnerLock(root / ".local/service.owner.lock"):
        registry = AccountRegistry(settings.data)
        initial = registry.budget(timezone=settings.timezone)
        evidence = Path(
            tempfile.mkdtemp(prefix=f"route-check-{check}-", dir=root / ".local")
        )
        report = {
            "check": check,
            "started": time.time(),
            "bounds": bounds,
            "telegram_calls": 0,
            "initial_budget": initial,
            "defaults_revision": DEFAULTS_REVISION,
            "selected_cases": cases,
            "note": note,
            "results": [],
            "format": [],
        }
        engine = engine_factory(settings, registry, initial, bounds)

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
            async with asyncio.timeout(bounds["seconds"]):
                # Verifies the exact route and its structured-output contract.
                await engine.prepare({})
                report["route"] = engine.rates
                if not within_adopted_rates(engine.rates):
                    raise Refused("Rate increase requires reviewing finite cost bounds")
                save()
                with tempfile.TemporaryDirectory(
                    prefix="route-fixture-", dir=root / ".local"
                ) as directory:
                    store = Store(
                        Path(directory) / "rewriting.sqlite3",
                        account_id=1,
                        mode="personal",
                        timezone=settings.timezone,
                        shared_path=registry.path,
                    )
                    transport = FakeTransport()
                    app = Conversations(store, transport, engine)
                    # ADR 54: this check rewrites its text cases; the owner's setting stays.
                    store.rewrites_text = True
                    # Calming decides first (ADR 53); scripts/check-calming.py checks it.
                    app.calming.default = False
                    app.media.transcriber = AuthoredSpeech()
                    work = app.rewriting.work
                    work.guidance = registry.guidance
                    work.admit = engine.admit
                    work.on_format = lambda role, ok: report["format"].append(
                        {"role": role, "ok": ok}
                    )
                    # Each role is driven explicitly, one request per case.
                    app.memory.enabled = lambda: False
                    app.suggestions.enabled = lambda: False
                    tone = {"tone": "neutral", "revision": 0}
                    app.rewriting.preferences = lambda: tone
                    chats = {}

                    def chat(name):
                        peer = f"user:{700 + len(chats)}"
                        key = f"1/{peer}"
                        store.put_chat(
                            Chat(
                                account_id=1,
                                peer=peer,
                                title="Authored route check",
                                kind="private",
                                ai_eligible=True,
                            ),
                            activity=time.time(),
                        )
                        chats[name] = (peer, key)
                        return peer, key

                    def begin(name, **fields):
                        engine.admit()
                        engine.current = {"name": name, **fields}
                        report["results"].append(engine.current)

                    def finish(attempt):
                        value = engine.current
                        value["outcome"] = store.rows(
                            "SELECT state,error FROM jobs WHERE id=?", (attempt,)
                        )
                        value["reservations"] = store.rows(
                            "SELECT status,actual FROM reservations WHERE id=?",
                            (attempt,),
                        )
                        value["receipts"] = store.rows(
                            "SELECT model,provider,usage,error FROM receipts WHERE id=?",
                            (attempt,),
                        )
                        save()
                        print(
                            json.dumps(
                                {
                                    "case": value["name"],
                                    "state": (value["outcome"] or [{}])[0].get("state"),
                                    "requests": engine.requests,
                                    "counted_test_nanos": report["counted_test_nanos"],
                                }
                            ),
                            flush=True,
                        )

                    async def rewrite_incoming(name, sources, expectation):
                        begin(name, sources=sources, expectation=expectation)
                        peer, key = chat(name)
                        start = time.time() - 31 - len(sources)
                        for number, source in enumerate(sources, 1):
                            fields = {}
                            if source.get("forwarded_from"):
                                fields.update(
                                    forwarded=True,
                                    forwarded_from=source["forwarded_from"],
                                )
                            if "voice" in source:
                                transport.media[peer, number] = (
                                    "authored:" + source["voice"]
                                ).encode()
                                fields.update(
                                    media="audio", media_info=MediaInfo(voice=True)
                                )
                            elif source.get("image"):
                                transport.media[peer, number] = bill_image()
                                fields.update(media="image")
                            store.clock = lambda: start + number
                            app.sync.ingest(
                                Message(
                                    account_id=1,
                                    peer=peer,
                                    message_id=number,
                                    revision=1,
                                    date=start + number,
                                    text=source.get("text", ""),
                                    **fields,
                                ),
                                target=False,
                            )
                        store.clock = time.time
                        # Deliberate cached processing keeps authored short
                        # cases independent of the saved automatic threshold.
                        app.rewriting.process_older(key)
                        jobs = store.rows(
                            "SELECT * FROM jobs WHERE scope LIKE ? AND role='incoming_rewrite' AND state='queued'",
                            (key + "/%",),
                        )
                        assert len(jobs) == 1, "One combined block per case"
                        job = jobs[0]
                        assert job["due"] <= time.time()
                        assert app.rewriting.claim(job)
                        try:
                            await app.rewriting.incoming(job)
                        except Refused:
                            pass  # The first failed outcome is retained.
                        finally:
                            engine.current["rendered"] = store.rows(
                                "SELECT rendered,state FROM blocks WHERE id=?",
                                (job["scope"],),
                            )
                            # ADR 30: emotion has its own route and check
                            # (scripts/check-emotion.py); none is made here.
                            with store.transaction() as db:
                                db.execute(
                                    "UPDATE jobs SET state='cancelled',error='Not part of this check' WHERE role='emotion_classification' AND state='queued'"
                                )
                            engine.current["image_context"] = [
                                json.loads(r["body"]).get("image_context")
                                for r in store.rows(
                                    "SELECT body FROM derivatives WHERE source LIKE ?",
                                    (key + "/%",),
                                )
                            ]
                            finish(job["id"])

                    async def rewrite_outgoing(name, source, value, draft, expectation):
                        begin(name, tone=value, draft=draft, expectation=expectation)
                        tone.update(tone=value, revision=tone["revision"] + 1)
                        key = chats[source][1]
                        current = store.draft(key)
                        current = store.mutate_draft(
                            key, current["revision"], lambda d: d.update(text=draft)
                        )
                        owner = app.rewriting.outgoing_work
                        pending = owner.start(key, current["revision"])
                        try:
                            await owner.tasks[pending["attempt"]]
                        except Refused:
                            pass
                        finally:
                            engine.current["result"] = store.draft(key)["text"]
                            finish(pending["attempt"])

                    async def consolidate_memory():
                        case = MEMORY
                        begin(**case)
                        peer, key = chat(case["name"])
                        old = time.time() - 10 * 86400
                        for number, body in enumerate(case["messages"], 1):
                            app.sync.ingest(
                                Message(
                                    account_id=1,
                                    peer=peer,
                                    message_id=number,
                                    revision=1,
                                    date=old + number,
                                    text=body,
                                    outgoing=number % 2 == 0,
                                ),
                                target=False,
                            )
                        app.sync.ingest(
                            Message(
                                account_id=1,
                                peer=peer,
                                message_id=200,
                                revision=1,
                                date=time.time() - 31 * 60,
                                text="Authored recent activity anchor.",
                                outgoing=True,
                            ),
                            target=False,
                        )
                        with store.transaction() as db:
                            db.execute(
                                "UPDATE chats SET activity=? WHERE key=?",
                                (time.time() - 31 * 60, key),
                            )
                            for identity, body, citations in case["existing"]:
                                db.execute(
                                    "INSERT INTO memory_entries (id,chat_key,text,citations,source,outdated,created) VALUES (?,?,?,?,'model',NULL,?)",
                                    (
                                        identity,
                                        key,
                                        body,
                                        json.dumps(citations),
                                        old - 10 * 86400,
                                    ),
                                )
                        app.memory.enabled = lambda: True
                        # Its chat remembers without switching on, as before
                        # ADR 56's Off default.
                        app.memory.default = True
                        app.memory.record(
                            key,
                            [f"{key}/{i}" for i in range(1, len(case["messages"]) + 1)],
                        )
                        try:
                            attempt = app.memory.schedule(key)
                            assert attempt, "Authored case did not meet admission"
                            job = store.rows(
                                "SELECT * FROM jobs WHERE id=?", (attempt,)
                            )[0]
                            assert app.rewriting.claim(job)
                            try:
                                await app.memory.fold(job)
                            except Refused:
                                pass
                            finally:
                                engine.current["entries"] = app.memory.entries(key)
                                engine.current["state"] = app.memory.state(key)
                                finish(attempt)
                        finally:
                            app.memory.enabled = lambda: False

                    async def suggest():
                        case = SUGGESTION
                        begin(**case)
                        peer, key = chat(case["name"])
                        app.sync.ingest(
                            Message(
                                account_id=1,
                                peer=peer,
                                message_id=1,
                                revision=1,
                                date=time.time() - 60,
                                text=case["text"],
                            ),
                            target=False,
                        )
                        app.rewriting.process_older(key)
                        block = store.rows(
                            "SELECT * FROM blocks WHERE chat_key=?", (key,)
                        )[0]
                        job = store.rows(
                            "SELECT * FROM jobs WHERE scope=? AND role='incoming_rewrite' AND state='queued'",
                            (block["id"],),
                        )[0]
                        # The authored emotion stands in for a validated incoming
                        # result: only the suggestion request is made.
                        packet = await incoming(app, block, explicit=True)
                        signal = app.emotion.capture(block, packet, job["id"])
                        version = registry.guidance()["version"]
                        with store.transaction() as db:
                            db.execute(
                                "UPDATE jobs SET state='done' WHERE id=?", (job["id"],)
                            )
                            db.execute(
                                "UPDATE blocks SET state='ready',rendered=?,rendered_revision=revision WHERE id=?",
                                (case["text"], block["id"]),
                            )
                            # A useful decision (ADR 49 Q2) stands in too.
                            app.emotion.publish(
                                db, key, signal, case["emotion"], version, useful=True
                            )
                            app.suggestions.schedule(
                                db,
                                block,
                                # Stand in for normal latest-block publication;
                                # cached preparation keeps its deliberate class.
                                job | {"payload": json.dumps({"class": "new"})},
                                packet,
                                [],
                                enabled=True,
                            )
                        attempt = store.rows(
                            "SELECT * FROM jobs WHERE scope=? AND role='response_suggestions' AND state='queued'",
                            (key,),
                        )[0]
                        assert app.rewriting.claim(attempt)
                        # The owner's global switch still decides (ADR 25).
                        app.suggestions.enabled = registry.suggestions_enabled
                        try:
                            await app.suggestions.run(attempt)
                        except Refused:
                            pass
                        finally:
                            app.suggestions.enabled = lambda: False
                            record = app.suggestions.record(key) or {}
                            engine.current["record"] = {
                                k: record.get(k)
                                for k in ("state", "failure", "options")
                            }
                            finish(attempt["id"])

                    try:
                        if check == 1:
                            for case in CHECK_1_INCOMING:
                                await rewrite_incoming(*case)
                            for case in OUTGOING:
                                await rewrite_outgoing(*case)
                            await consolidate_memory()
                            await suggest()
                        elif check == 2:
                            for case in CHECK_2_INCOMING:
                                if not cases or case[0] in cases:
                                    await rewrite_incoming(*case)
                        else:
                            for case in CHECK_1_INCOMING + CHECK_2_INCOMING:
                                await rewrite_incoming(*case)
                        assert engine.requests <= bounds["requests"]
                        assert not transport.deliveries
                    finally:
                        tasks = list(app.tasks)
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        store.close()
        except TimeoutError:
            report["stopped"] = "Deadline reached"
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", type=int, choices=sorted(BOUNDS), required=True)
    parser.add_argument("--case", action="append", dest="cases")
    parser.add_argument("--requests", type=int)
    parser.add_argument("--seconds", type=int)
    parser.add_argument("--counted-nanos", type=int)
    parser.add_argument("--note")
    arguments = parser.parse_args()
    asyncio.run(
        observe(
            arguments.check,
            cases=arguments.cases,
            requests=arguments.requests,
            seconds=arguments.seconds,
            counted_nanos=arguments.counted_nanos,
            note=arguments.note,
        )
    )
