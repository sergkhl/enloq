"""ADR 59's Ask this group: retrieval over one group's cached originals, an
answer citing only what the writer read, and the citation check of each
statement. With tools (ADR 60) the writer searches and reads the same
originals itself. Nothing about a question or its answer is stored (Q9)."""

import asyncio
import dataclasses
import json
import sqlite3
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain import citation_check
from ..domain.citation_check import ANSWER_CHECK_ROLE, FLOOR
from ..domain.group_questions import (
    AROUND,
    CANDIDATES,
    EMBED_ROLE,
    LEXICAL,
    MAX_CALLS,
    MAX_SEARCHES,
    MAX_TURNS,
    NEIGHBOURS,
    QUESTION_ROLE,
    REPLIES,
    RERANK_ROLE,
    RETRIEVAL,
    SEARCH,
    SEARCHED,
    WRITTEN,
    GroupAnswer,
    match_expression,
    nearest,
    question_text,
    searchable,
)
from ..domain.models import Message, ProcessingPaused, Refused, reply_target
from ..inference.base import ProviderRefused
from ..storage import group_vectors
from ..storage.store import RETENTION
from . import group_context as context
from .group_briefings import checked_view

# Embedding and rerank refusals that did no work are tried again (_run).
RETRIED_TRIES, RETRIED_WAIT = 3, 5
# A long transcript is embedded by its beginning: the vector only ranks it.
EMBEDDED_CHARACTERS = 8000
BATCH, BATCH_BYTES = 64, 100_000


class Unchecked(Exception):
    """The citation check could not run: no answer shows (ADR 59 Q6)."""


@dataclasses.dataclass
class Reading:
    """What one question's writer has read, and its tool calls so far."""

    seen: set = dataclasses.field(default_factory=set)
    searches: int = 0
    calls: int = 0


class GroupQuestions:
    def __init__(self, app, *, embedder=None, reranker=None, writer=None):
        self.app, self.store = app, app.store
        self.embedder, self.reranker = embedder, reranker
        # ADR 59 Q5: gpt-6.1-sol on the owner's subscription; the account
        # manager attaches it, and without it the app's own engine writes.
        self.writer = writer
        # ADR 60 Q3: how the writer's messages are found, when it calls tools.
        self.retrieval = RETRIEVAL
        # ADR 59 Q8: one question at a time per account.
        self._busy = asyncio.Lock()

    def _admit(self, key):
        """Account AI, the group's AI and verified access (Q2)."""
        self.app.admit_ai()
        if not self.app.authorized():
            raise Refused("Account authorization changed")
        state = context.group_chat(self.store, key)
        if not state.processing_enabled or state.first_enabled_at is None:
            raise ProcessingPaused("Group AI processing is Off")
        if state.access != "available":
            raise Refused("Group access is not verified")
        return state

    def _day(self, timestamp):
        return datetime.fromtimestamp(timestamp, self._zone()).date().isoformat()

    def _since(self, state):
        """The searched range's first moment: the first opt-in day's start,
        within the 30-day cache."""
        start, _ = context.day_bounds(self.store, self._day(state.first_enabled_at))
        return max(start, self.store.clock() - RETENTION)

    def pool(self, key, since):
        """What may be searched: eligible current originals, without hidden
        or ignored bots and validated ads, as the briefing reads them."""
        entries = []
        for row in self.store.rows(
            "SELECT body FROM messages WHERE chat_key=? AND date>=? AND deleted=0 "
            "ORDER BY message_id",
            (key, since),
        ):
            message = Message.model_validate_json(row["body"])
            if not context.eligible(message) or not self.store.groups.current(message):
                continue
            if any(self.app.group_filtering.annotations(message).values()):
                continue
            projection = checked_view(self.store, message)
            text = searchable(projection)
            if text:
                entries.append((message, projection, text))
        return entries

    @staticmethod
    def lexical(entries, question, limit=LEXICAL):
        expression = match_expression(question)
        if not expression or not entries:
            return []
        db = sqlite3.connect(":memory:")
        try:
            db.execute("CREATE VIRTUAL TABLE pool USING fts5(body, tokenize='trigram')")
            db.executemany(
                "INSERT INTO pool(rowid, body) VALUES (?,?)",
                [(m.message_id, text) for m, _, text in entries],
            )
            return [
                row[0]
                for row in db.execute(
                    "SELECT rowid FROM pool WHERE pool MATCH ? ORDER BY bm25(pool) LIMIT ?",
                    (expression, limit),
                )
            ]
        finally:
            db.close()

    async def _run(self, engine, role, packet, key):
        """One request; an embedding or rerank refusal that did no work, such
        as a provider's per-second token quota, is tried again after the wait
        it names (a second by default), at most twice and five seconds each."""
        tries = RETRIED_TRIES if role in (EMBED_ROLE, RERANK_ROLE) else 1
        for attempt in range(tries):
            try:
                return await self._attempt(engine, role, packet, key)
            except ProviderRefused as refusal:
                wait = 1 if refusal.retry_after is None else refusal.retry_after
                if attempt + 1 == tries or wait > RETRIED_WAIT:
                    raise
                await asyncio.sleep(max(1, wait))

    async def _attempt(self, engine, role, packet, key):
        job = dict(
            id=uuid.uuid4().hex,
            scope=key,
            role=role,
            input_revision="question",
            state="claimed",
            due=self.store.clock(),
            payload=json.dumps({"class": "suggestions"}),
            error=None,
        )
        with self.store.transaction() as db:
            db.execute("INSERT INTO jobs VALUES (?,?,?,?,?,?,?,?)", tuple(job.values()))

        def valid():
            try:
                self._admit(key)
            except Refused:
                return False
            return True

        return await self.app.rewriting.work.run(
            engine, job, packet, valid, klass="suggestions"
        )

    async def embed(self, key, texts):
        """Vectors for texts, in order, in bounded requests."""
        vectors, batch, size = [], [], 0
        for text in texts:
            text = text[:EMBEDDED_CHARACTERS]
            if batch and (
                len(batch) == BATCH or size + len(text.encode()) > BATCH_BYTES
            ):
                vectors += await self._run(
                    self.embedder, EMBED_ROLE, {"texts": batch}, key
                )
                batch, size = [], 0
            batch.append(text)
            size += len(text.encode())
        if batch:
            vectors += await self._run(self.embedder, EMBED_ROLE, {"texts": batch}, key)
        return vectors

    async def vectors(self, key, entries):
        """Each original's vector, computed when first needed and kept with
        its revision (Q4)."""
        model = self.embedder.route.model
        revisions = {m.key: m.revision for m, _, _ in entries}
        with self.store.transaction() as db:
            found = group_vectors.cached(db, model, revisions)
        missing = [(m, text) for m, _, text in entries if m.key not in found]
        if missing:
            computed = await self.embed(key, [text for _, text in missing])
            fresh = {
                m.key: vector for (m, _), vector in zip(missing, computed, strict=True)
            }
            with self.store.transaction() as db:
                group_vectors.save(db, model, fresh, revisions, self.store.clock())
            found.update(fresh)
        return {m.message_id: found[m.key] for m, _, _ in entries}

    async def rank(self, key, query, entries):
        """The embeddings' ranking, its first 40 reranked when a reranker is
        adopted, best first. Keyword search ranked worse and lowered the fused
        ranking in the measurement (ADR 59 Q3), so it ranks only when no
        embedder is attached."""
        if self.embedder is None:
            return self.lexical(entries, query)
        vectors = await self.vectors(key, entries)
        route = self.embedder.route
        instructed = (
            f"{route.query_instruction}{query}"
            if getattr(route, "query_instruction", None)
            else query
        )
        [asked] = await self.embed(key, [instructed])
        ranked = nearest(asked, vectors, CANDIDATES)
        if self.reranker is not None and ranked:
            texts = {m.message_id: text for m, _, text in entries}
            try:
                scores = await self._run(
                    self.reranker,
                    RERANK_ROLE,
                    {"query": query, "documents": [texts[i] for i in ranked]},
                    key,
                )
            except Refused:
                # Reranking only improves the order: without it the question
                # is still answered from the embeddings' ranking.
                self._admit(key)
                return ranked
            ranked = [
                i for _, i in sorted(zip(scores, ranked), key=lambda p: (-p[0], p[1]))
            ]
        return ranked

    async def retrieve(self, key, question, entries):
        """The fixed pass: at most WRITTEN identities, best first."""
        return (await self.rank(key, question, entries))[:WRITTEN]

    def _read(self, reading, entries, ids):
        """The written originals for ids, in their order, now read."""
        by_id = {m.message_id: projection for m, projection, _ in entries}
        reading.seen.update(ids)
        return [self._written(by_id[i]) for i in ids]

    async def tool(self, key, entries, reading, call):
        """One tool call's result, from this group's searchable originals only
        (ADR 60 Q1); a call beyond the question's bounds gets an error to
        answer from, and nothing runs (Q2)."""
        if reading.calls == MAX_CALLS:
            return {"error": "No more tool calls: answer from the messages read."}
        reading.calls += 1
        if call.name == SEARCH:
            if reading.searches == MAX_SEARCHES:
                return {"error": "No more searches: answer from the messages read."}
            reading.searches += 1
            ranked = await self.rank(key, call.arguments["query"], entries)
            return {"messages": self._read(reading, entries, ranked[:SEARCHED])}
        index = {m.message_id: i for i, (m, _, _) in enumerate(entries)}
        identity = call.arguments["message_id"]
        if identity not in index:
            return {"error": f"Message {identity} is not among the searchable ones."}
        position = index[identity]
        if call.name == AROUND:
            near = entries[max(0, position - NEIGHBOURS) : position + NEIGHBOURS + 1]
            return {
                "messages": self._read(
                    reading, entries, [m.message_id for m, _, _ in near]
                )
            }
        target = reply_target(entries[position][0])
        replies = [m.message_id for m, _, _ in entries if reply_target(m) == identity]
        return {
            "message": self._read(reading, entries, [identity])[0],
            "replies_to": self._read(reading, entries, [target])[0]
            if target in index
            else None,
            "replies": self._read(reading, entries, replies[:REPLIES]),
        }

    async def write(self, key, question, today, entries):
        """The writer's answer, or None when the fixed pass found nothing. A
        writer that calls tools reads the fixed pass's messages, if this
        retrieval has them, and whatever its own calls return (ADR 60)."""
        writer = self.writer or self.app.engine
        retrieval = self.retrieval if getattr(writer, "tools", False) else "fixed"
        retrieved = []
        if retrieval != "tools":
            retrieved = await self.retrieve(key, question, entries)
            if not retrieved:
                return None
        packet = self.packet(question, today, entries, retrieved)
        if retrieval == "fixed":
            return await self._run(writer, QUESTION_ROLE, packet, key)
        reading = Reading(set(retrieved))
        packet.update(tools=True, rounds=[])
        for turn in range(MAX_TURNS):
            packet["final"] = turn == MAX_TURNS - 1
            replied = await self._run(writer, QUESTION_ROLE, packet, key)
            if isinstance(replied, GroupAnswer):
                return replied
            results = {
                call.id: await self.tool(key, entries, reading, call)
                for call in replied
            }
            packet["rounds"].append(
                {"calls": [call.model_dump() for call in replied], "results": results}
            )
            packet["retrieved_ids"] = sorted(reading.seen)
        raise Refused("The writer did not answer")

    def _written(self, projection):
        value = {
            "id": projection["id"],
            "speaker": projection["speaker"].get("name", "Unknown"),
            "date": datetime.fromtimestamp(projection["date"], self._zone()).isoformat(
                timespec="minutes"
            ),
        }
        for field in (
            "text",
            "transcript",
            "image_context",
            "forwarded_from",
            "reply_to",
        ):
            if projection.get(field):
                value[field] = (
                    projection[field]["text"]
                    if field == "transcript"
                    else projection[field]
                )
        return value

    def _zone(self):
        return ZoneInfo(self.store.timezone)

    async def check(self, key, statement):
        """ADR 34's citation check of one statement (ADR 59 Q6)."""
        topic = type(
            "Statement",
            (),
            {"title": None, "text": statement.text, "citations": statement.citations},
        )
        try:
            packet = self.app.briefings._check_packet(
                key, topic, role=ANSWER_CHECK_ROLE
            )
            answers = await self._run(self.app.decider, ANSWER_CHECK_ROLE, packet, key)
        except Refused as failure:
            raise Unchecked(str(failure)) from None
        return citation_check.answered(packet, answers)

    def packet(self, question, today, entries, retrieved):
        """What the writer reads: the retrieved originals in their order."""
        by_id = {m.message_id: projection for m, projection, _ in entries}
        chronological = sorted(retrieved)
        return {
            "question": question,
            "today": today,
            "messages": [self._written(by_id[i]) for i in chronological],
            "retrieved_ids": chronological,
        }

    async def checked(self, key, answer):
        """Each statement with the citations the check kept (ADR 59 Q6)."""
        statements = []
        for statement in answer.statements:
            checked = await self.check(key, statement)
            statements.append(
                {
                    "text": statement.text,
                    "citations": [
                        s.message_id for s in checked.support if s.probability >= FLOOR
                    ],
                    "unsupported": [
                        s.message_id for s in checked.support if s.probability < FLOOR
                    ],
                    "supported": checked.claim >= FLOOR,
                }
            )
        return statements

    async def ask(self, key, question):
        """One answer, an abstention naming the searched range, or nothing
        when the check cannot run."""
        question = question_text(question)
        if self._busy.locked():
            raise Refused("Another question is being answered")
        async with self._busy:
            state = self._admit(key)
            since = self._since(state)
            today = self._day(self.store.clock())
            searched = {"start": self._day(since), "end": today}
            nothing = {"status": "not_found", "searched": searched, "statements": []}
            entries = self.pool(key, since)
            if not entries:
                return nothing
            answer = await self.write(key, question, today, entries)
            if answer is None or answer.outcome == "not_found":
                return nothing
            try:
                statements = await self.checked(key, answer)
            except Unchecked:
                return {"status": "unchecked", "searched": searched, "statements": []}
            return {"status": "answer", "searched": searched, "statements": statements}
