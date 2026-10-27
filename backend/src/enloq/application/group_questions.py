"""ADR 59's Ask this group: retrieval over one group's cached originals, a
MiMo answer citing only what retrieval found, and the citation check of each
statement. Nothing about a question or its answer is stored (Q9)."""

import asyncio
import json
import sqlite3
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from ..domain import citation_check
from ..domain.citation_check import ANSWER_CHECK_ROLE, FLOOR
from ..domain.group_questions import (
    DENSE,
    EMBED_ROLE,
    FUSED,
    LEXICAL,
    QUESTION_ROLE,
    RERANK_ROLE,
    WRITTEN,
    fuse,
    match_expression,
    nearest,
    question_text,
    searchable,
)
from ..domain.models import Message, ProcessingPaused, Refused
from ..storage import group_vectors
from ..storage.store import RETENTION
from . import group_context as context
from .group_briefings import checked_view

# A long transcript is embedded by its beginning: the vector only ranks it.
EMBEDDED_CHARACTERS = 8000
BATCH, BATCH_BYTES = 64, 100_000


class Unchecked(Exception):
    """The citation check could not run: no answer shows (ADR 59 Q6)."""


class GroupQuestions:
    def __init__(self, app, *, embedder=None, reranker=None):
        self.app, self.store = app, app.store
        self.embedder, self.reranker = embedder, reranker
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

    async def retrieve(self, key, question, entries):
        """Keyword and vector rankings, fused, then reranked when a reranker is
        adopted; at most WRITTEN identities, best first."""
        ranked = [self.lexical(entries, question)]
        if self.embedder is not None:
            vectors = await self.vectors(key, entries)
            route = self.embedder.route
            query = (
                f"{route.query_instruction}{question}"
                if getattr(route, "query_instruction", None)
                else question
            )
            [asked] = await self.embed(key, [query])
            ranked.append(nearest(asked, vectors, DENSE))
        fused = fuse(*ranked)[:FUSED]
        if self.reranker is not None and fused:
            texts = {m.message_id: text for m, _, text in entries}
            scores = await self._run(
                self.reranker,
                RERANK_ROLE,
                {"query": question, "documents": [texts[i] for i in fused]},
                key,
            )
            fused = [
                i for _, i in sorted(zip(scores, fused), key=lambda p: (-p[0], p[1]))
            ]
        return fused[:WRITTEN]

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
            retrieved = await self.retrieve(key, question, entries)
            if not retrieved:
                return nothing
            packet = self.packet(question, today, entries, retrieved)
            answer = await self._run(self.app.engine, QUESTION_ROLE, packet, key)
            if answer.outcome == "not_found":
                return nothing
            try:
                statements = await self.checked(key, answer)
            except Unchecked:
                return {"status": "unchecked", "searched": searched, "statements": []}
            return {"status": "answer", "searched": searched, "statements": statements}
