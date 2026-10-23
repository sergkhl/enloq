#!/usr/bin/env python3
"""Finite first-attempt authored ADR 34 briefing qualification (GB10).

At most twelve provider requests, 1,200 seconds and $0.10 counted spending,
inside the shared Group Briefing Budget. The stopped service's OwnerLock is
held throughout. Refresh exact MiMo/Xiaomi availability and rates before any
dispatch, and require the remaining run/day allowance to cover the larger of
the conservative prepared-request/output cost and the ordinary unknown amount.
Stop at an unknown, failed first outcome or bound; never retry or resample.

Only an authorized, AI-enabled account ID is read from the shared registry.
Its session, account database and correspondence are never opened. A temporary
Store and FakeTransport run the real GroupBriefings preparation, image, chunk
and streamed-final path over wholly authored multi-speaker originals. Prepared
transcripts contain no audio; a small image is drawn locally. Guidance, switches,
threshold, budget settings and live format counters stay intact. Only this
pass's ordinary group-family shared reservations and receipts are retained.

This closed GB10 check qualifies the generative path. Its authored discussion
has explicit, exact cached non-ad premises through the current filtering
projection/reuse contract. No classifier requests are dispatched or credited to
the generative request counter; filtering/provider qualification has its own
separately sealed scope.

Private .local evidence contains first authored outputs, source/format findings
and separate manual semantic expectations. Terminal output contains only counts
and the report path. Bounds may only be lowered.
"""

import argparse
import asyncio
import hashlib
import io
import json
import math
import os
import tempfile
import time
from datetime import datetime, timedelta
from datetime import time as day_time
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from enloq.application.conversations import Conversations
from enloq.application.group_context import calendar_day
from enloq.config import ROOT, Settings, private_write
from enloq.domain.briefing_contract import GROUP_ROLES
from enloq.domain.group_filtering import AD_ROLE, FILTER_ROLES
from enloq.domain.models import Chat, MediaInfo, Message, Refused
from enloq.inference.audio import WHISPER
from enloq.inference.base import ProviderRefused
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

BOUNDS = {"requests": 12, "seconds": 1200, "counted_nanos": 100_000_000}
FAMILY = "group_briefing"
# Charge every complete UTF-8 request byte as a token, plus hidden framing and
# image expansion. The only drawn image is 320 x 192, covered by 8,192 tokens.
FRAMING_TOKENS, IMAGE_TOKENS = 2048, 8192


def authored_cases():
    discussion = [
        {
            "text": "The timetable photo is a proposal. Please check its two dates.",
            "image": True,
            "speaker": 0,
        },
        {
            "voice": "Ну, выставку предлагали во вторник, нет, в среду после "
            "обеда. Это пока предложение: зал ещё не подтвердили.",
            "speaker": 1,
        },
    ]
    # Distinct authored planning details deliberately exceed one real 24 KiB
    # chunk. They exercise bounded chunk reuse rather than an artificial hook.
    for number in range(1, 51):
        discussion.append(
            {
                "speaker": number % 3,
                "text": (
                    f"Workshop station {number}: the sample board is ready for "
                    "inspection, but the room booking is still a proposal. "
                    "Please compare the blue label with the printed catalogue "
                    "before moving any materials. If the label matches, leave "
                    "the sample beside the window; if it differs, keep it on the "
                    "shelf and describe the difference in the next discussion. "
                    "We have not agreed to buy replacement parts or invite "
                    "visitors. The volunteer's availability is Wednesday after "
                    "lunch, subject to the hall being confirmed. The red storage "
                    "box belongs to the shared display and should remain closed "
                    "during the label check. This is a planning note, not a "
                    "report that the inspection or purchase has happened."
                ),
            }
        )
    discussion.extend(
        [
            {
                "speaker": 2,
                "text": "The notice says the hall inspection may finish on "
                "Wednesday; it does not confirm our booking.",
                "forwarded": True,
            },
            {
                "speaker": 0,
                "text": "Yes, I mean the small blue board from that earlier "
                "note. I can check the label, but I cannot confirm a booking.",
                "reply_to": 1,
            },
        ]
    )
    return [
        {
            "name": "chunked_multispeaker_media_forward_reply",
            "sources": discussion,
            "support": "The earlier note identifies the small blue board; the "
            "large green board is a different sample.",
            "expectation": "Preserve the proposed Wednesday-afternoon correction "
            "and unconfirmed booking, conditional label check and locations, "
            "absence of a purchase agreement, and blue-board reply referent. "
            "Attribute the forwarded hall-inspection claim to its notice, not "
            "to the participant as firsthand fact. The drawn image has a "
            "Tuesday proposal and a Wednesday correction. Merge repeated "
            "planning topics without inventing completed work or commitments. "
            "Review topic citations against the exact covered originals.",
        },
        {
            "name": "short_greetings_no_substance",
            "sources": [
                {"speaker": 0, "text": "Привет всем! 🙂"},
                {"speaker": 1, "voice": "Спасибо, хорошего дня!"},
            ],
            "expectation": "These greetings contain no substantive update. "
            "Expect outcome no_substance and the adopted fixed wording. This "
            "semantic expectation is reviewed separately from format validity.",
        },
    ]


CASES = authored_cases()


def conservative_request_bound(system, packet, schema, rates, *, role, streamed):
    """Bound the exact role, prepared input, schema, image URLs and output cap."""
    images = packet.get("images", [])
    plain = {k: v for k, v in packet.items() if k not in {"images", "guidance"}}
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
    cap = output_tokens(role)
    payload = {
        "model": MODEL,
        "user": "x" * 128,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ],
        "max_tokens": cap,
        "extra_body": {"thinking": thinking(role)},
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "enloq",
                "strict": True,
                "schema": schema,
            },
        },
    }
    if streamed:
        payload.update(stream=True, stream_options={"include_usage": True})
    size = len(json.dumps(payload, ensure_ascii=False).encode())
    tokens = size + FRAMING_TOKENS + IMAGE_TOKENS * len(images)
    cost = math.ceil(
        (Decimal(rates["prompt"]) * tokens + Decimal(rates["completion"]) * cap)
        * 1_000_000_000
    )
    return dict(
        prepared_request_bytes=size,
        input_token_bound=tokens,
        output_token_limit=cap,
        conservative_cost_nanos=cost,
        request_bound_nanos=max(cost, FLAT_UNKNOWN),
    )


class ObservedBriefings(OpenRouterEngine):
    def __init__(self, settings, registry, initial, bounds, *, account_id, client=None):
        super().__init__(settings, user=registry.provider_user(), client=client)
        self.registry, self.initial, self.bounds = registry, initial, bounds
        self.account_id, self.timezone = account_id, settings.timezone
        self.started, self.requests = time.monotonic(), 0
        self.current, self.stopped, self.attempts = None, None, []

    def reservations(self):
        if not self.attempts:
            return []
        marks = ",".join("?" for _ in self.attempts)
        return self.registry.rows(
            "SELECT id,bucket,status,actual,family,role FROM model_reservations "
            f"WHERE account_id=? AND id IN ({marks})",
            (self.account_id, *self.attempts),
        )

    @staticmethod
    def known(row):
        return row["status"] == "refused" or (
            row["status"] == "receipted"
            and type(row["actual"]) is int
            and row["actual"] >= 0
        )

    def counted(self):
        # Retain run spending across dispatch days; the current day is checked
        # separately through the shared group-family ledger.
        return sum(
            row["actual"]
            if row["status"] == "receipted" and self.known(row)
            else 0
            if row["status"] == "refused"
            else FLAT_UNKNOWN
            for row in self.reservations()
        )

    def stop(self, reason):
        self.stopped = reason
        raise Refused(reason)

    def guard(self, required=FLAT_UNKNOWN, *, reserved=False):
        accounts = self.registry.rows(
            "SELECT state,ai_enabled FROM accounts WHERE account_id=?",
            (self.account_id,),
        )
        if not accounts or accounts[0] != {"state": "authorized", "ai_enabled": 1}:
            self.stop("No authorized AI-enabled account; cases remain unverified")
        credit, bucket = 0, None
        current = self.current["id"] if self.current else None
        for row in self.reservations():
            if row["family"] != FAMILY or row["role"] not in GROUP_ROLES:
                self.stop("Unexpected accounting family or model role")
            if not self.known(row):
                if (
                    reserved
                    and row["id"] == current
                    and row["status"] == "unknown"
                    and row["actual"] is None
                    and row["role"] == self.current["role"]
                    and not self.current["calls"]
                ):
                    credit, bucket = FLAT_UNKNOWN, row["bucket"]
                else:
                    self.stop("Unknown provider outcome; no further stages dispatched")
        if self.requests >= self.bounds["requests"]:
            self.stop("Finite request bound reached; remaining stages unverified")
        if time.monotonic() - self.started >= self.bounds["seconds"]:
            self.stop("Finite time bound reached; remaining stages unverified")
        if self.counted() - credit + required > self.bounds["counted_nanos"]:
            self.stop("Remaining finite allowance cannot cover the next request")
        state = self.registry.budget(timezone=self.timezone, family=FAMILY)
        day_credit = credit if bucket == "day:" + state["day"] else 0
        if state["spent"] - day_credit + required > state["limit"]:
            self.stop("Remaining Group Briefing Budget cannot cover the next request")

    def admit(self):
        self.guard()

    async def prepare(self, packet):
        value = await super().prepare(packet)
        if not within_adopted_rates(self.rates):
            self.stop("Rate increase requires reviewing finite cost bounds")
        if "messages" in packet:
            role = self.current["role"]
            bound = conservative_request_bound(
                instructions(role, packet, self.registry.guidance()),
                packet,
                output_schema(role, packet),
                self.rates,
                role=role,
                streamed=role == "group_briefing",
            )
            self.current.update(bound)
            self.guard(bound["request_bound_nanos"])
        return value

    async def structured(self, system, packet, schema, *, role, on_text=None):
        if (
            role not in GROUP_ROLES
            or role != self.current["role"]
            or (on_text is not None) != (role == "group_briefing")
        ):
            self.stop(
                "This check only dispatches real group roles with final streaming"
            )
        bound = conservative_request_bound(
            system, packet, schema, self.rates, role=role, streamed=on_text is not None
        )
        try:
            self.guard(bound["request_bound_nanos"], reserved=True)
        except Refused:
            # Ordinary ModelWork has reserved, but no HTTP work has started.
            raise ProviderRefused(self.stopped) from None
        call = {
            "role": role,
            "streamed": on_text is not None,
            "guidance_version": packet["guidance"]["version"],
            "defaults_revision": packet["guidance"].get("defaults_revision"),
            "attached_images": len(packet.get("images", [])),
            "packet": {
                k: v for k, v in packet.items() if k not in {"guidance", "images"}
            },
            "preview_updates": 0,
            **bound,
        }
        self.current.update(bound)
        self.current["calls"].append(call)
        self.requests += 1
        started = time.monotonic()

        def show(value):
            if value.strip():
                call.setdefault("first_text_seconds", time.monotonic() - started)
                call["preview_updates"] += 1
            on_text(value)

        try:
            response = await super().structured(
                system, packet, schema, role=role, on_text=show if on_text else None
            )
            call["raw"], call["actual_nanos"] = response.content, response.actual_nanos
            return response
        except BaseException as failure:
            receipt = getattr(failure, "receipt", None)
            call["raw"] = receipt.content if receipt else None
            raise
        finally:
            call["completion_seconds"] = time.monotonic() - started


class PreparedSpeechOnly:
    async def transcribe(self, _):
        raise Refused("Only prepared authored transcripts are permitted")


def picture():
    image = Image.new("RGB", (320, 192), "white")
    draw = ImageDraw.Draw(image)
    for index, line in enumerate(
        [
            "AUTHORED EXHIBITION PLAN",
            "Tuesday: proposal",
            "Wednesday: correction",
            "Room: unconfirmed",
        ]
    ):
        draw.text((12, 14 + 42 * index), line, fill="black", font_size=18)
    target = io.BytesIO()
    image.save(target, format="PNG")
    return target.getvalue()


def protected_state(registry):
    """A content-free equality proof for settings/counters, excluding day roll."""
    value = {
        "metadata": registry.rows(
            "SELECT key,value FROM metadata WHERE key NOT IN ('budget_day','provider_user') ORDER BY key"
        ),
        "accounts": registry.rows(
            "SELECT account_id,state,ai_enabled FROM accounts ORDER BY account_id"
        ),
        "guidance": registry.guidance(),
        "preferences": registry.preferences(),
        "format": registry.format_failures(),
        "threshold": registry.processing_threshold(),
        "budget_changes": registry.rows("SELECT * FROM budget_changes ORDER BY rowid"),
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def populate(app, case, index):
    store, transport = app.store, app.transport
    peer, account = f"channel:{880 + index}", store.account_id
    key = f"{account}/{peer}"
    now, zone = time.time(), ZoneInfo(store.timezone)
    day = datetime.fromtimestamp(now, zone).date() - timedelta(days=1)
    start = datetime.combine(day, day_time(12), zone).timestamp()
    # Opt-in starts on the authored source day, before its sources. Restore the
    # real clock before any claim/reservation so ledger dispatch days stay real.
    store.clock = lambda: start - 60
    store.put_chat(
        Chat(
            account_id=account, peer=peer, title="Authored briefing check", kind="group"
        ),
        activity=now,
    )
    store.groups.observe(key, access="available", reason="authored_native")
    app.set_group_processing(key, True)
    originals = []
    if case.get("support"):
        originals.append(
            Message(
                account_id=account,
                peer=peer,
                message_id=1,
                revision=1,
                date=start - 8 * 86400,
                text=case["support"],
                author="Authored Dana",
                author_peer="user:101",
                reply_to=999,
            )
        )
    for identity, source in enumerate(case["sources"], 2):
        speaker, fields = source["speaker"], {}
        if source.get("image"):
            fields.update(media="image", attachment_id="authored-timetable")
            transport.media[peer, identity] = picture()
        if "voice" in source:
            fields.update(
                media="audio",
                attachment_id=f"authored-speech-{identity}",
                media_info=MediaInfo(voice=True, duration=2),
            )
        if source.get("forwarded"):
            fields.update(
                forwarded=True,
                forwarded_from="Authored hall notice",
                forward_date=start - 3600,
                forward_signature="Authored coordinator",
            )
        message = Message(
            account_id=account,
            peer=peer,
            message_id=identity,
            revision=1,
            date=start + identity,
            text=source.get("text", ""),
            author=("Authored Dana", "Authored Eli", "Authored Noor")[speaker],
            author_peer=f"user:{101 + speaker}",
            reply_to=source.get("reply_to"),
            **fields,
        )
        originals.append(message)
    for message in originals:
        # Simulate the earlier observation of retained reply context while it
        # was recent; this never widens today's native intake window.
        store.clock = lambda message=message: min(start - 60, message.date)
        app.sync.ingest(message, group_epoch=store.groups.intake(key).epoch)
    for message, source in zip(originals[-len(case["sources"]) :], case["sources"]):
        if "voice" in source:
            transcript = dict(
                text=source["voice"],
                language="ru",
                model=WHISPER,
                machine_generated=True,
            )
            with store.transaction() as db:
                db.execute(
                    "INSERT INTO derivatives VALUES (?,?,?)",
                    (
                        message.key,
                        message.revision,
                        json.dumps({"transcript": transcript}),
                    ),
                )
    store.clock = time.time
    day = calendar_day(store, originals[-1])
    # These wholly authored fixtures already state their non-ad disposition.
    # Preserve the real evidence projection and exact-source decision reuse;
    # the historical generative allowance does not open classifier dispatch.
    sources = originals[-len(case["sources"]) :]
    filtering = app.briefings.filtering
    for request in filtering.plan(key, sources, day):
        if request["role"] != AD_ROLE:
            raise Refused("This check cannot qualify native bot author decisions")
        filtering.storage.record(
            request,
            {
                f"ad_{source.rsplit('/', 1)[1]}": 0.0
                for source in request["target_keys"]
            },
        )
    if any(filtering._source_decision(message) is None for message in sources):
        raise Refused("An exact authored non-ad premise could not be prepared")
    return key, day


def stage_evidence(app, step):
    store, identity = app.store, step["id"]
    step["outcome"] = store.rows("SELECT state,error FROM jobs WHERE id=?", (identity,))
    step["reservations"] = store.rows(
        "SELECT status,actual,family,bucket FROM reservations WHERE id=?", (identity,)
    )
    step["receipts"] = store.rows(
        "SELECT model,provider,usage,error,family FROM receipts WHERE id=?", (identity,)
    )
    payload = store.rows("SELECT payload FROM jobs WHERE id=?", (identity,))
    attempt = store.group_briefings.attempt(
        json.loads(payload[0]["payload"])["attempt"]
    )
    step["source_valid"] = bool(
        attempt and store.group_briefings.current(attempt.snapshot)
    )
    if step["calls"]:
        row = step["reservations"][0] if step["reservations"] else None
        step["format_valid"] = (
            step["outcome"][0]["state"] == "done"
            if row and ObservedBriefings.known(row) and row["status"] == "receipted"
            else None
        )


async def observe(
    settings=None,
    *,
    root=ROOT,
    engine_factory=ObservedBriefings,
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
        accounts = registry.rows(
            "SELECT account_id FROM accounts WHERE state='authorized' AND ai_enabled=1 ORDER BY account_id LIMIT 1"
        )
        account_id = accounts[0]["account_id"] if accounts else 1
        initial = registry.budget(timezone=settings.timezone, family=FAMILY)
        evidence = Path(
            tempfile.mkdtemp(prefix="group-briefing-check-", dir=root / ".local")
        )
        report = dict(
            started=time.time(),
            bounds=bounds,
            initial_budget=initial,
            budget_family=FAMILY,
            defaults_revision=DEFAULTS_REVISION,
            saved_processing_threshold=registry.processing_threshold(),
            processing_path="real GroupBriefings automatic authored fixture",
            filtering_path="exact cached authored non-ad premises; no classifier dispatch",
            telegram_calls=0,
            audio_files=0,
            semantic_review="pending manual review",
            results=[],
        )
        engine = engine_factory(
            settings, registry, initial, bounds, account_id=account_id
        )
        protected = protected_state(registry)

        def save():
            report.update(
                elapsed_seconds=time.monotonic() - engine.started,
                requests=engine.requests,
                generative_requests=engine.requests,
                classifier_requests=0,
                counted_test_nanos=engine.counted(),
                final_budget=registry.budget(timezone=settings.timezone, family=FAMILY),
                protected_owner_state_unchanged=protected_state(registry) == protected,
            )
            report["within_bounds"] = (
                engine.requests <= bounds["requests"]
                and report["elapsed_seconds"] <= bounds["seconds"]
                and report["counted_test_nanos"] <= bounds["counted_nanos"]
                and report["final_budget"]["spent"] <= report["final_budget"]["limit"]
            )
            completed = {
                r["name"] for r in report["results"] if r.get("format_source_verified")
            }
            report["unverified_cases"] = [
                c["name"] for c in CASES if c["name"] not in completed
            ]
            roles = {
                call["role"]
                for r in report["results"]
                for step in r["stages"]
                for call in step["calls"]
            }
            report["format_source_verified"] = (
                not report["unverified_cases"]
                and roles == GROUP_ROLES
                and report["within_bounds"]
                and report["protected_owner_state_unchanged"]
                and not engine.stopped
                and not report.get("stopped")
            )
            private_write(
                evidence / "report.json",
                json.dumps(report, ensure_ascii=False, indent=2),
            )

        try:
            async with asyncio.timeout(bounds["seconds"]):
                if not accounts:
                    engine.stop(
                        "No authorized AI-enabled account; cases remain unverified"
                    )
                await engine.prepare({})
                report["route"] = engine.rates
                engine.admit()
                save()
                with tempfile.TemporaryDirectory(
                    prefix="group-briefing-fixture-", dir=root / ".local"
                ) as directory:
                    store = Store(
                        Path(directory) / "rewriting.sqlite3",
                        account_id=account_id,
                        mode="personal",
                        timezone=settings.timezone,
                        shared_path=registry.path,
                    )
                    transport = FakeTransport()
                    transport.account_id = account_id
                    app = Conversations(store, transport, engine)
                    app.media.transcriber = PreparedSpeechOnly()
                    app.memory.enabled = app.suggestions.enabled = lambda: False
                    app.emotion.schedule = lambda *args: None
                    work = app.rewriting.work
                    work.guidance, work.admit = registry.guidance, engine.admit
                    work.on_format = lambda *args: None
                    try:
                        for index, case in enumerate(CASES):
                            engine.admit()
                            result = {
                                **case,
                                "stages": [],
                                "semantic_review": "pending manual review",
                            }
                            report["results"].append(result)
                            key, day = populate(app, case, index)
                            result["original_characters"] = sum(
                                len(s.get("text", "").strip()) for s in case["sources"]
                            )
                            result["intended_ids"] = list(
                                range(2, 2 + len(case["sources"]))
                            )
                            result["classified_non_ad_ids"] = list(
                                result["intended_ids"]
                            )
                            for _ in range(50):
                                state = store.group_briefings.state(key, day)
                                if state.final:
                                    result["final"] = json.loads(
                                        state.final.model_dump_json()
                                    )
                                    result["source_valid"] = (
                                        store.group_briefings.current(state.final)
                                    )
                                    result["format_source_verified"] = bool(
                                        result["source_valid"]
                                        and not state.uncovered_ids
                                        and all(
                                            s.get("format_valid") is True
                                            for s in result["stages"]
                                            if s["calls"]
                                        )
                                    )
                                    break
                                engine.admit()
                                job = app.briefings.next_job()
                                if job is None or not app.briefings.claim(job):
                                    engine.stop(
                                        "No runnable first-attempt stage; remaining work unverified"
                                    )
                                # A prospective job becomes its actual first
                                # stage during claim. Guard the concrete role
                                # before run can reserve or dispatch anything.
                                if job["role"] in FILTER_ROLES:
                                    engine.stop(
                                        "Unexpected classifier stage outside the closed generative check"
                                    )
                                payload = json.loads(job["payload"])
                                step = engine.current = dict(
                                    id=job["id"],
                                    role=job["role"],
                                    stage=payload["stage"],
                                    calls=[],
                                )
                                result["stages"].append(step)
                                engine.attempts.append(job["id"])
                                try:
                                    await app.briefings.run(job)
                                finally:
                                    stage_evidence(app, step)
                                    save()
                                if engine.stopped:
                                    raise Refused(engine.stopped)
                                rows = step["reservations"]
                                if any(not engine.known(row) for row in rows):
                                    engine.stop(
                                        "Unknown provider outcome; no further stages dispatched"
                                    )
                                if any(row["status"] == "refused" for row in rows):
                                    engine.stop(
                                        "First provider attempt refused; no retry or resampling"
                                    )
                                if step["calls"] and any(
                                    row["actual"] > step["request_bound_nanos"]
                                    for row in rows
                                ):
                                    engine.stop(
                                        "Receipt exceeded the conservative request bound"
                                    )
                                if step["outcome"][0]["state"] != "done":
                                    engine.stop(
                                        "First stage did not complete; no retry or resampling"
                                    )
                            else:
                                engine.stop("Finite local-stage limit reached")
                            save()
                    finally:
                        report["telegram_calls"] = sum(
                            len(getattr(transport, field))
                            for field in (
                                "deliveries",
                                "reaction_calls",
                                "deletion_calls",
                                "reads",
                                "exact_reads",
                                "avatar_reads",
                                "unread_reads",
                                "catalog_calls",
                                "typing_events",
                            )
                        )
                        report["authored_media_downloads"] = len(transport.downloads)
                        report["excluded_role_jobs"] = len(
                            store.rows(
                                "SELECT id FROM jobs WHERE role NOT IN ('group_briefing_image','group_briefing_chunk','group_briefing')"
                            )
                        )
                        tasks = list(app.tasks)
                        for task in tasks:
                            task.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                        store.close()
                    if report["telegram_calls"] or report["excluded_role_jobs"]:
                        engine.stop("Unexpected transport action or model role")
        except TimeoutError:
            report["stopped"] = "Deadline reached; remaining cases unverified"
        except Refused as failure:
            report["stopped"] = str(failure)
        finally:
            try:
                save()
            finally:
                await engine.client.aclose()
                registry.connections.close()
            print(
                json.dumps(
                    dict(
                        evidence=str(evidence / "report.json"),
                        requests=engine.requests,
                        counted_test_nanos=report["counted_test_nanos"],
                        format_source_verified=report["format_source_verified"],
                        within_bounds=report["within_bounds"],
                    )
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
