"""Shared parts of the finite group checks (ADR 34's citation check, ADR 59):
their bounds, the authored groups' population and the subscription judge."""

import asyncio
import json
import re
import time

import httpx
from enloq.application.group_context import calendar_day
from enloq.domain.group_filtering import AD_ROLE
from enloq.domain.models import Chat, MediaInfo, Message, Refused
from enloq.inference.audio import WHISPER
from enloq.inference.openrouter import proxy_endpoint, proxy_key
from enloq.inference.work import FLAT_UNKNOWN

FAMILY = "group_briefing"
# The second grader, as in the calming and memory checks: the proxy's
# subscription route, which spends nothing. Each check bounds its requests.
JUDGE_MODEL = "chatgpt/gpt-6.1-sol"
# The caller key allows eight parallel requests; the service is stopped.
PARALLEL = 4


class Bounds:
    """One allowance across a check's request kinds, its seconds and its
    counted Group Briefing spending."""

    def __init__(self, registry, timezone, limits, *, seconds, max_nanos):
        self.registry, self.timezone = registry, timezone
        self.limits, self.seconds, self.max_nanos = limits, seconds, max_nanos
        self.initial = registry.budget(timezone=timezone, family=FAMILY)
        self.started = time.monotonic()
        self.requests = {kind: 0 for kind in limits}

    def spent(self):
        state = self.registry.budget(timezone=self.timezone, family=FAMILY)
        return state, state["spent"] - self.initial["spent"]

    def admit(self):
        state, spent = self.spent()
        if (
            time.monotonic() - self.started >= self.seconds
            or spent + FLAT_UNKNOWN > self.max_nanos
            or state["reached"]
        ):
            raise Refused("Finite check or shared daily bound reached")

    def take(self, kind):
        if self.requests[kind] >= self.limits[kind]:
            raise Refused(f"The {kind} request bound is reached")
        self.requests[kind] += 1


class Judge:
    """The second grader on the proxy's subscription route. One first attempt
    per item, at most PARALLEL at once; an unreadable answer grades nothing."""

    def __init__(self, settings, registry, bounds, *, client=None):
        self.endpoint, self.key = proxy_endpoint(settings), proxy_key(settings)
        self.bounds, self.gate = bounds, asyncio.Semaphore(PARALLEL)
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(300, connect=30),
            follow_redirects=False,
            trust_env=False,
        )

    async def ask(self, system, content, *, kind="judge"):
        """The model's JSON object and the seconds it took, or None. ``kind``
        names the bound it counts against: the same route also writes the
        comparison answers in the Ask check."""
        self.bounds.take(kind)
        async with self.gate:
            started = time.monotonic()
            try:
                response = await self.client.post(
                    f"{self.endpoint}/chat/completions",
                    headers={"Authorization": f"Bearer {self.key}"},
                    json={
                        "model": JUDGE_MODEL,
                        "messages": [
                            {"role": "system", "content": system},
                            {"role": "user", "content": content},
                        ],
                    },
                )
                response.raise_for_status()
                text = response.json()["choices"][0]["message"]["content"] or ""
                found = re.search(r"\{.*\}", text, re.S)
                answer = json.loads(found.group(0)) if found else None
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
                answer = None
            return answer, round(time.monotonic() - started, 3)


class PreparedSpeechOnly:
    async def transcribe(self, _):
        raise Refused("Only prepared authored transcripts are permitted")


def authored_message(group, account, peer, message, date):
    fields = {}
    if "voice" in message:
        fields.update(
            media="audio",
            attachment_id=f"authored-voice-{message['id']}",
            media_info=MediaInfo(voice=True, duration=12),
        )
    elif "image" in message:
        fields.update(media="image", attachment_id=f"authored-photo-{message['id']}")
    if message.get("forwarded"):
        fields.update(
            forwarded=True,
            forwarded_from=message["forwarded"],
            forward_date=date - 3600,
        )
    speaker = message["speaker"]
    return Message(
        account_id=account,
        peer=peer,
        message_id=message["id"],
        revision=1,
        date=date,
        text=message.get("text", ""),
        author=group["speakers"][speaker],
        author_peer=f"user:{501 + speaker}",
        reply_to=message.get("reply_to"),
        **fields,
    )


def populate(app, group, peer, days, *, start_of, processing=True):
    """Ingest authored days into one group chat, with their prepared
    transcripts and image meanings, as if observed while recent."""
    store = app.store
    account = store.account_id
    key = f"{account}/{peer}"
    first = start_of(days[0])
    store.clock = lambda: first - 60
    store.put_chat(
        Chat(account_id=account, peer=peer, title=group["title"], kind="group"),
        activity=time.time(),
    )
    store.groups.observe(key, access="available", reason="authored_native")
    if processing:
        app.set_group_processing(key, True)
    originals = []
    for day in days:
        for message in day["messages"]:
            date = start_of(day) + message["minute"] * 60
            value = authored_message(group, account, peer, message, date)
            store.clock = lambda date=date: date
            app.sync.ingest(value, group_epoch=store.groups.intake(key).epoch)
            derived = {}
            if "voice" in message:
                derived["transcript"] = dict(
                    text=message["voice"],
                    language="ru"
                    if re.search("[а-яё]", message["voice"], re.I)
                    else "en",
                    model=WHISPER,
                    machine_generated=True,
                )
            if "image" in message:
                derived["image_context"] = message["image"]
            if derived:
                with store.transaction() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO derivatives VALUES (?,?,?)",
                        (value.key, value.revision, json.dumps(derived)),
                    )
            originals.append(value)
    store.clock = time.time
    return key, originals


def non_ads(app, key, originals):
    """These wholly authored days state their non-ad disposition, so the
    briefing reuses exact decisions and no classifier request is made."""
    filtering = app.briefings.filtering
    for day in sorted({calendar_day(app.store, m) for m in originals}):
        sources = [m for m in originals if calendar_day(app.store, m) == day]
        for request in filtering.plan(key, sources, day):
            if request["role"] != AD_ROLE:
                raise Refused("This check cannot qualify native bot author decisions")
            filtering.storage.record(
                request,
                {f"ad_{s.rsplit('/', 1)[1]}": 0.0 for s in request["target_keys"]},
            )
    if any(app.briefings.filtering._source_decision(m) is None for m in originals):
        raise Refused("An exact authored non-ad premise could not be prepared")
