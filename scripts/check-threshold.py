#!/usr/bin/env python3
"""Finite first-attempt ADR 35 incoming-format check (CT5).

At most six requests, 600 seconds and $0.05 counted spending, inside the
existing Daily Budget. The service must be stopped: its exclusive OwnerLock
is held throughout. The exact MiMo/Xiaomi listing and rates are refreshed
before any model call. Every dispatch needs room for the larger of its
conservative prepared-request/output bound and the ordinary unknown estimate;
an unknown outcome stops the pass. There is no automatic retry or resampling.

The six cases are wholly authored, including a prepared transcript and a
small generated image. A fake Telegram transport performs no native action;
no audio exists or reaches the provider. Deliberate cache-only older processing
exercises short inputs without changing the owner's saved character threshold.
This checks provider format independently of automatic admission, which the
offline/backend/browser cases verify. Emotion, suggestions and memory are off
in the fixture; owner settings and the running route's format counts stay intact.

First outcomes, authored raw outputs and review expectations are retained only
in a private .local report. Terminal output contains counts/outcomes and the
report path, never request/response content. Bounds may only be lowered.
"""

import argparse
import asyncio
import io
import json
import math
import os
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from enloq.application.conversations import Conversations
from enloq.config import ROOT, Settings, private_write
from enloq.domain.models import Chat, MediaInfo, Message, Refused
from enloq.inference.guidance import DEFAULTS_REVISION, instructions
from enloq.inference.openrouter import (
    MODEL,
    OpenRouterEngine,
    output_schema,
    output_tokens,
    thinking,
    within_adopted_rates,
)
from enloq.inference.work import FLAT_UNKNOWN
from enloq.storage.accounts import AccountRegistry
from enloq.storage.lock import OwnerLock
from enloq.storage.store import Store
from enloq.transport.fake import FakeTransport
from PIL import Image, ImageDraw

BOUNDS = {"requests": 6, "seconds": 600, "counted_nanos": 50_000_000}
# Every UTF-8 request byte is charged as a token. Add room for hidden framing
# and image expansion on top of the complete encoded image URL. The only
# check image is 320 x 192; 8,192 image tokens is deliberately conservative.
FRAMING_TOKENS, IMAGE_TOKENS = 2048, 8192
ROLE = "incoming_rewrite"

TEXT_FIRST = (
    "For the community workshop, please check whether the blue table can be "
    "moved beside the window."
)
TEXT_SECOND = (
    "If it fits, leave the drawing sheets there; otherwise, keep them on the "
    "shelf and tell me before Friday."
)
CASES = [
    {
        "name": "combined_199",
        "sources": [{"text": TEXT_FIRST}, {"text": TEXT_SECOND}],
        "expectation": "Keep the table question, conditional location for the "
        "drawing sheets and the Friday deadline. Two originals total 199 "
        "trimmed code points; this deliberate check is threshold-exempt.",
    },
    {
        "name": "combined_200",
        "sources": [
            {"text": TEXT_FIRST.replace("the blue table", "that blue table")},
            {"text": TEXT_SECOND},
        ],
        "expectation": "Keep the same question, conditional locations and "
        "Friday deadline. Two originals total 200 trimmed code points.",
    },
    {
        "name": "trimmed_cyrillic_emoji",
        "sources": [
            {"text": " \nПривет! Проверь, пожалуйста, открыт ли зал в субботу.\t"},
            {"text": "\u00a0Если открыт, возьми синий блокнот.\nСпасибо 👩🏽‍💻🙂  "},
        ],
        "expectation": "Keep Russian, the hall question and the conditional "
        "blue-notebook request. Trimming excludes outer whitespace, keeps "
        "internal newlines, and counts each emoji code point separately.",
    },
    {
        "name": "prepared_authored_transcript",
        "sources": [
            {
                "voice": "Ну, э-э, встречу давай во вторник, нет, в среду, после "
                "обеда. И захвати, пожалуйста, папку, если она ещё у тебя."
            }
        ],
        "expectation": "Keep the final Wednesday-afternoon correction and "
        "conditional folder request as clear Russian written text. A nonblank "
        "prepared target transcript requires at least one visible part.",
    },
    {
        "name": "text_and_authored_image",
        "sources": [
            {"text": "Are both delivery lines meant to be here? Please check."},
            {"image": True},
        ],
        "expectation": "Keep the question and check request. Hidden image "
        "meaning identifies two delivery lines without inventing a resolution.",
    },
    {
        "name": "image_only_with_textual_support",
        "support": "Here is the sample bill we can check during the workshop.",
        "sources": [{"image": True}],
        "expectation": "The target has only an image: empty visible parts remain "
        "allowed despite nonblank textual Supporting Context. The hidden image "
        "meaning must describe the authored bill, including both delivery lines.",
    },
]


def conservative_request_bound(system, packet, schema, rates):
    """Bound the actual prepared input, exact instructions/schema and output cap.

    The JSON includes the nested user text and complete encoded image URLs as
    the transport does. Every byte is a token, plus independent image/framing
    allowances; hidden reasoning is already inside the ordinary output cap.
    """
    images = packet.get("images", [])
    plain = {
        k: v
        for k, v in packet.items()
        if k not in ("images", "guidance", "calm", "sarcastic")
    }
    content = [{"type": "text", "text": json.dumps(plain, ensure_ascii=False)}]
    for image in images:
        content.extend(
            [
                {
                    "type": "text",
                    "text": json.dumps(
                        dict(attachment="image", native_message_id=image["message_id"])
                    ),
                },
                {"type": "image_url", "image_url": {"url": image["data_url"]}},
            ]
        )
    cap = output_tokens(ROLE)
    payload = {
        "model": MODEL,
        # The largest permitted pseudonym length, without retaining its value.
        "user": "x" * 128,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "max_tokens": cap,
        "extra_body": {"thinking": thinking(ROLE)},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "enloq",
                "strict": True,
                "schema": schema,
            },
        },
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    size = len(json.dumps(payload, ensure_ascii=False).encode())
    tokens = size + FRAMING_TOKENS + IMAGE_TOKENS * len(images)
    cost = math.ceil(
        (Decimal(rates["prompt"]) * tokens + Decimal(rates["completion"]) * cap)
        * 1_000_000_000
    )
    return {
        "prepared_request_bytes": size,
        "input_token_bound": tokens,
        "output_token_limit": cap,
        "conservative_cost_nanos": cost,
        "request_bound_nanos": max(cost, FLAT_UNKNOWN),
    }


class ObservedThreshold(OpenRouterEngine):
    def __init__(self, settings, registry, initial, bounds, *, client=None):
        super().__init__(settings, user=registry.provider_user(), client=client)
        self.registry, self.initial, self.bounds = registry, initial, bounds
        self.timezone = settings.timezone
        self.started = time.monotonic()
        self.requests = 0
        self.current = None
        self.attempts = []
        self.stopped = None

    def reservations(self):
        if not self.attempts:
            return []
        marks = ",".join("?" for _ in self.attempts)
        return self.registry.rows(
            f"SELECT id,bucket,status,actual FROM model_reservations "
            f"WHERE account_id=1 AND id IN ({marks})",
            tuple(self.attempts),
        )

    def counted(self):
        # Count our retained attempts across dispatch days, even if the ten-minute
        # check straddles midnight. Day allowance is checked independently below.
        return sum(
            (row["actual"] or 0)
            if row["status"] == "receipted"
            else 0
            if row["status"] == "refused"
            else FLAT_UNKNOWN
            for row in self.reservations()
        )

    def stop(self, reason):
        self.stopped = reason
        raise Refused(reason)

    def guard(self, required=FLAT_UNKNOWN, *, reserved=False):
        credit, reserved_bucket = 0, None
        current = self.current.get("attempt") if self.current else None
        for row in self.reservations():
            if row["status"] not in {"receipted", "refused"}:
                if reserved and row["id"] == current:
                    credit = FLAT_UNKNOWN
                    reserved_bucket = row["bucket"]
                else:
                    self.stop("Unknown provider outcome; no further cases dispatched")
        if (
            self.requests >= self.bounds["requests"]
            or time.monotonic() - self.started >= self.bounds["seconds"]
        ):
            self.stop("Finite request or time bound reached")
        if self.counted() - credit + required > self.bounds["counted_nanos"]:
            self.stop("Remaining finite allowance cannot cover the next request")
        state = self.registry.budget(timezone=self.timezone)
        day_credit = credit if reserved_bucket == "day:" + state["day"] else 0
        if state["spent"] - day_credit + required > state["limit"]:
            self.stop("Remaining Daily Budget cannot cover the next request")

    def admit(self):
        self.guard()

    async def prepare(self, packet):
        value = await super().prepare(packet)
        if not within_adopted_rates(self.rates):
            self.stop("Rate increase requires reviewing finite cost bounds")
        if "messages" in packet:
            bound = conservative_request_bound(
                instructions(ROLE, packet, self.registry.guidance()),
                packet,
                output_schema(ROLE, packet),
                self.rates,
            )
            self.current.update(bound)
            self.guard(bound["request_bound_nanos"])
        return value

    async def structured(self, system, packet, schema, *, role, on_text=None):
        if role != ROLE or on_text is None:
            self.stop("This check only dispatches streamed incoming rewrites")
        bound = conservative_request_bound(system, packet, schema, self.rates)
        # Recheck the exact dispatch guidance/input after the ordinary reservation.
        # Credit just this attempt's $0.01 estimate to avoid counting it twice.
        try:
            self.guard(bound["request_bound_nanos"], reserved=True)
        except Refused:
            # ModelWork has reserved, but no transport work has occurred.
            from enloq.inference.base import ProviderRefused

            raise ProviderRefused(self.stopped) from None
        self.current.update(bound)
        call = {
            "role": role,
            "guidance_version": packet["guidance"]["version"],
            "defaults_revision": packet["guidance"].get("defaults_revision"),
            "attached_images": len(packet.get("images", [])),
            "parts_min_items": schema["properties"]["parts"].get("minItems", 0),
            "packet": {
                k: v for k, v in packet.items() if k not in {"guidance", "images"}
            },
            **bound,
        }
        self.current["calls"].append(call)
        self.requests += 1
        started = time.monotonic()

        def show(value):
            if value.strip() and "first_text_seconds" not in call:
                call["first_text_seconds"] = time.monotonic() - started
            on_text(value)

        try:
            response = await super().structured(
                system, packet, schema, role=role, on_text=show
            )
            call["raw"] = response.content
            return response
        except BaseException as failure:
            receipt = getattr(failure, "receipt", None)
            call["raw"] = receipt.content if receipt else None
            raise
        finally:
            call["completion_seconds"] = time.monotonic() - started


class AuthoredSpeech:
    """Only marker bytes exist: no real audio or recognizer is involved."""

    async def transcribe(self, path):
        value = Path(path).read_bytes().decode()
        if not value.startswith("authored:"):
            raise Refused("The fixture only permits authored transcript bytes")
        return {"text": value[len("authored:") :]}


def picture():
    image = Image.new("RGB", (320, 192), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(
        [
            "AUTHORED SAMPLE BILL",
            "Paper: 40",
            "Delivery: 10",
            "Delivery: 10",
            "Total: 60",
        ]
    ):
        draw.text((12, 12 + 34 * index), line, fill="black", font_size=18)
    target = io.BytesIO()
    image.save(target, format="PNG")
    return target.getvalue()


async def observe(
    settings=None,
    *,
    root=ROOT,
    engine_factory=ObservedThreshold,
    requests=None,
    seconds=None,
    counted_nanos=None,
):
    bounds = dict(BOUNDS)
    for key, value in (
        ("requests", requests),
        ("seconds", seconds),
        ("counted_nanos", counted_nanos),
    ):
        if value is not None:
            if type(value) is not int or not 0 < value <= bounds[key]:
                raise ValueError("A check can only lower a recorded bound")
            bounds[key] = value
    settings = settings or Settings.load("personal")
    with OwnerLock(root / ".local/service.owner.lock"):
        registry = AccountRegistry(settings.data)
        initial = registry.budget(timezone=settings.timezone)
        evidence = Path(
            tempfile.mkdtemp(prefix="threshold-check-", dir=root / ".local")
        )
        report = {
            "started": time.time(),
            "bounds": bounds,
            "telegram_calls": 0,
            "initial_budget": initial,
            "defaults_revision": DEFAULTS_REVISION,
            "processing_path": "deliberate cache-only older processing",
            "saved_processing_threshold": registry.processing_threshold(),
            "results": [],
            "format": [],
        }
        engine = engine_factory(settings, registry, initial, bounds)

        def save():
            report["elapsed_seconds"] = time.monotonic() - engine.started
            report["requests"] = engine.requests
            report["final_budget"] = registry.budget(timezone=settings.timezone)
            report["counted_test_nanos"] = engine.counted()
            report["within_bounds"] = (
                engine.requests <= bounds["requests"]
                and report["counted_test_nanos"] <= bounds["counted_nanos"]
                and report["elapsed_seconds"] <= bounds["seconds"]
                and report["final_budget"]["spent"] <= report["final_budget"]["limit"]
            )
            completed = {
                r["name"]
                for r in report["results"]
                if r.get("calls")
                and r.get("outcome")
                and r["outcome"][0]["state"] == "done"
                and r.get("reservations")
                and all(row["status"] == "receipted" for row in r["reservations"])
            }
            report["unverified_cases"] = [
                case["name"] for case in CASES if case["name"] not in completed
            ]
            report["format_verified"] = (
                not report["unverified_cases"]
                and not engine.stopped
                and not report.get("stopped")
                and engine.requests == len(CASES)
                and report["within_bounds"]
                and all(r["outcome"][0]["state"] == "done" for r in report["results"])
                and len(report["format"]) == len(CASES)
                and all(f["ok"] for f in report["format"])
                and all(
                    r["reservations"][0]["status"] == "receipted"
                    for r in report["results"]
                )
            )
            private_write(
                evidence / "report.json",
                json.dumps(report, ensure_ascii=False, indent=2),
            )

        try:
            async with asyncio.timeout(bounds["seconds"]):
                # A fresh engine has no cached listing: this always refreshes it.
                await engine.prepare({})
                report["route"] = engine.rates
                engine.admit()
                save()
                with tempfile.TemporaryDirectory(
                    prefix="threshold-fixture-", dir=root / ".local"
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
                    app.memory.enabled = lambda: False
                    app.suggestions.enabled = lambda: False
                    app.emotion.schedule = lambda *args: None
                    work = app.rewriting.work
                    work.guidance, work.admit = registry.guidance, engine.admit
                    work.on_format = lambda role, ok: report["format"].append(
                        {"role": role, "ok": ok}
                    )
                    try:
                        for index, case in enumerate(CASES):
                            engine.admit()
                            current = engine.current = {**case, "calls": []}
                            report["results"].append(current)
                            peer = f"user:{770 + index}"
                            key = f"1/{peer}"
                            store.put_chat(
                                Chat(
                                    account_id=1,
                                    peer=peer,
                                    title="Authored threshold check",
                                    kind="private",
                                    ai_eligible=True,
                                ),
                                activity=time.time(),
                            )
                            start = time.time() - 60 - len(case["sources"])
                            if case.get("support"):
                                app.sync.ingest(
                                    Message(
                                        account_id=1,
                                        peer=peer,
                                        message_id=1,
                                        revision=1,
                                        date=start - 1,
                                        text=case["support"],
                                        outgoing=True,
                                    ),
                                    target=False,
                                )
                            for number, source in enumerate(case["sources"], 2):
                                fields = {}
                                if "voice" in source:
                                    transport.media[peer, number] = (
                                        "authored:" + source["voice"]
                                    ).encode()
                                    fields.update(
                                        media="audio", media_info=MediaInfo(voice=True)
                                    )
                                elif source.get("image"):
                                    transport.media[peer, number] = picture()
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
                            current["original_characters"] = sum(
                                len(s.get("text", "").strip()) for s in case["sources"]
                            )
                            assert app.rewriting.process_older(key) == 1, (
                                "One cached block per case"
                            )
                            jobs = store.rows(
                                "SELECT * FROM jobs WHERE role=? AND scope LIKE ? AND state='queued'",
                                (ROLE, key + "/%"),
                            )
                            assert len(jobs) == 1
                            job = jobs[0]
                            current["attempt"] = job["id"]
                            engine.attempts.append(job["id"])
                            assert app.rewriting.claim(job)
                            try:
                                await app.rewriting.incoming(job)
                            finally:
                                current["outcome"] = store.rows(
                                    "SELECT state,error FROM jobs WHERE id=?",
                                    (job["id"],),
                                )
                                current["reservations"] = store.rows(
                                    "SELECT status,actual FROM reservations WHERE id=?",
                                    (job["id"],),
                                )
                                current["receipts"] = store.rows(
                                    "SELECT model,provider,usage,error FROM receipts WHERE id=?",
                                    (job["id"],),
                                )
                                current["rendered"] = store.rows(
                                    "SELECT rendered,state FROM blocks WHERE id=?",
                                    (job["scope"],),
                                )
                                current["image_context"] = [
                                    json.loads(r["body"]).get("image_context")
                                    for r in store.rows(
                                        "SELECT body FROM derivatives WHERE source LIKE ?",
                                        (key + "/%",),
                                    )
                                ]
                                save()
                                print(
                                    json.dumps(
                                        {
                                            "case": case["name"],
                                            "state": current["outcome"][0]["state"],
                                            "requests": engine.requests,
                                            "counted_test_nanos": report[
                                                "counted_test_nanos"
                                            ],
                                        }
                                    ),
                                    flush=True,
                                )
                            if engine.stopped:
                                raise Refused(engine.stopped)
                            if any(
                                r["status"] not in {"receipted", "refused"}
                                for r in current["reservations"]
                            ):
                                engine.stop(
                                    "Unknown provider outcome; no further cases dispatched"
                                )
                            if any(
                                r["status"] == "receipted"
                                and r["actual"] > current["request_bound_nanos"]
                                for r in current["reservations"]
                            ):
                                engine.stop(
                                    "Receipt exceeded the conservative request bound; "
                                    "remaining cases unverified"
                                )
                            if not current["calls"]:
                                engine.stop(
                                    "Case did not dispatch; remaining cases unverified"
                                )
                        assert (
                            not transport.deliveries
                            and not transport.reaction_calls
                            and not transport.deletion_calls
                        )
                        assert not store.rows(
                            "SELECT id FROM jobs WHERE role IN ('emotion_classification','response_suggestions','chat_memory')"
                        )
                    finally:
                        tasks = list(app.tasks)
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        store.close()
        except TimeoutError:
            report["stopped"] = "Deadline reached; remaining cases unverified"
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
                        "format_verified": report["format_verified"],
                        "within_bounds": report["within_bounds"],
                    }
                ),
                flush=True,
            )
        return report


if __name__ == "__main__":
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int)
    parser.add_argument("--seconds", type=int)
    parser.add_argument("--counted-nanos", type=int)
    arguments = parser.parse_args()
    asyncio.run(
        observe(
            requests=arguments.requests,
            seconds=arguments.seconds,
            counted_nanos=arguments.counted_nanos,
        )
    )
