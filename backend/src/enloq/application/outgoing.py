"""Service-owned outgoing attempts; browsers read snapshots or stop application."""

import asyncio
import base64
import json
import uuid
from pathlib import Path

from ..domain.models import Conflict, ProcessingPaused, Refused
from ..domain.selected_media import sticker_selected
from .context import TARGET_BYTES


class Outgoing:
    def __init__(self, rewriting):
        self.rewriting = rewriting
        self.app, self.store = rewriting.app, rewriting.store
        self.tasks = {}

    def view(self, key):
        draft = self.store.draft(key)
        if draft.get("state") == "rewriting":
            rows = self.store.rows(
                "SELECT text FROM rewrite_streams WHERE id=? AND state='active' AND draft_revision=?",
                (draft.get("attempt"), draft["revision"]),
            )
            if rows and rows[0]["text"] is not None:
                return {**draft, "text": rows[0]["text"], "partial": True}
        return draft

    def unlocked(self, key):
        if self.store.draft(key).get("state") == "rewriting":
            raise Conflict("Stop or undo this rewrite before editing or sending")

    def group_access(self, key):
        state = self.store.groups.get(key)
        if (
            not state.supported
            or state.access != "available"
            or not any(row["key"] == key for row in self.store.recent_chats("group"))
        ):
            raise Refused("Native group access unavailable for rewriting")
        return state.access_revision

    def target_current(self, key, group_revision):
        try:
            chat = self.store.chat(key)
            return chat.ai_eligible and (
                chat.kind == "private"
                if group_revision is None
                else chat.kind == "group" and self.group_access(key) == group_revision
            )
        except Refused:
            return False

    def start(self, key, revision):
        self.app.admit_ai()
        chat = self.store.chat(key)
        group_revision = self.group_access(key) if chat.kind == "group" else None
        draft = self.store.draft(key)
        if sticker_selected(draft):
            raise Refused("Remove the sticker before rewriting the kept draft")
        if (draft.get("selected_media") or {}).get("status") == "preparing":
            raise Refused("Wait for selected media preparation before rewriting")
        if draft.get("state") == "rewriting":
            jobs = self.store.rows(
                "SELECT input_revision FROM jobs WHERE id=?", (draft.get("attempt"),)
            )
            if jobs and revision in (draft["revision"], int(jobs[0]["input_revision"])):
                return self.view(key)
        if draft["revision"] != revision:
            raise Conflict("Draft changed before rewriting")
        if not draft["text"].strip():
            raise Refused("Write your own message before rewriting")
        chat = self.store.chat(key)
        if not chat.ai_eligible or chat.kind not in {"private", "group"}:
            raise Refused("This conversation is not eligible for provider processing")
        if len(draft["text"].encode()) > TARGET_BYTES:
            raise Refused(
                "Draft exceeds the rewrite input bound; your text is retained"
            )
        tone = self.rewriting.preferences()
        images = self.app.attachments.snapshot(key, draft["attachments"])
        if len(images) > 4 or sum(i["size"] for i in images) > 20_000_000:
            raise Refused(
                "Caption rewriting supports up to four images and 20 MB; selection retained"
            )
        identity = uuid.uuid4().hex
        locked = {
            **draft,
            "revision": revision + 1,
            "undo": draft["text"],
            "state": "rewriting",
            "attempt": identity,
            "error": None,
        }
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT body FROM drafts WHERE chat_key=?", (key,)
            ).fetchone()
            current = json.loads(row[0]) if row else draft
            if current["revision"] != revision:
                raise Conflict("Draft changed before rewriting")
            if sticker_selected(current):
                raise Refused("Remove the sticker before rewriting the kept draft")
            if (current.get("selected_media") or {}).get("status") == "preparing":
                raise Refused("Wait for selected media preparation before rewriting")
            if db.execute(
                "SELECT id FROM jobs WHERE scope=? AND role='outgoing_rewrite' AND state IN ('queued','claimed','running')",
                (key,),
            ).fetchone():
                raise Refused("This draft already has pending work")
            db.execute(
                "INSERT INTO jobs VALUES (?,?,?,?,'queued',?,?,NULL)",
                (
                    identity,
                    key,
                    "outgoing_rewrite",
                    str(revision),
                    self.store.clock(),
                    json.dumps(
                        {
                            "class": "outgoing",
                            **(
                                {"group_access_revision": group_revision}
                                if group_revision is not None
                                else {}
                            ),
                        }
                    ),
                ),
            )
            db.execute(
                "INSERT INTO rewrite_streams VALUES (?,?,NULL,?,'active',?)",
                (identity, key, json.dumps(draft), locked["revision"]),
            )
            db.execute(
                "INSERT OR REPLACE INTO drafts VALUES (?,?)", (key, json.dumps(locked))
            )
        task = asyncio.create_task(
            self.run(identity, key, draft, locked, tone, images, group_revision)
        )
        self.tasks[identity] = task
        self.app.tasks.add(task)

        def done(task):
            self.tasks.pop(identity, None)
            self.app.tasks.discard(task)
            if task.cancelled():
                self.restore(identity, asyncio.CancelledError())
                self.store.streams.finish(identity)
            if not task.cancelled():
                task.exception()  # Failure is persisted for every browser.

        task.add_done_callback(done)
        return self.view(key)

    async def run(
        self, identity, key, original, locked, tone, images, group_revision=None
    ):
        job = self.store.rows("SELECT * FROM jobs WHERE id=?", (identity,))[0]

        def valid():
            draft = self.store.draft(key)
            return (
                self.app.authorized()
                and draft["revision"] == locked["revision"]
                and draft.get("attempt") == identity
                and draft["state"] == "rewriting"
                and self.rewriting.preferences() == tone
                and self.target_current(key, group_revision)
                and bool(
                    self.store.rows(
                        "SELECT id FROM rewrite_streams WHERE id=? AND state='active'",
                        (identity,),
                    )
                )
            )

        def publish(text):
            if valid():
                self.store.streams.publish(identity, text)

        try:
            packet = {
                "images": [
                    {
                        "message_id": index + 1,
                        "sha256": i["sha256"],
                        "data_url": f"data:{i['mime']};base64,"
                        + base64.b64encode(Path(i["path"]).read_bytes()).decode(),
                    }
                    for index, i in enumerate(images)
                ],
                "draft": {"text": original["text"]},
                "tone": tone["tone"],
                "inputs": {},
            }
            result = await self.rewriting.work.run(
                self.app.engine,
                job,
                packet,
                valid,
                klass="outgoing",
                on_text=publish,
            )
            if not valid():
                raise Conflict(
                    "Draft, tone or account authorization changed; late rewrite was not applied"
                )
            return self.store.mutate_draft(
                key,
                locked["revision"],
                lambda d: d.update(
                    text=result.text,
                    undo=original["text"],
                    state="rewritten",
                    attempt=identity,
                    error=None,
                    guidance_version=packet["guidance"]["version"],
                    defaults_revision=packet["guidance"].get("defaults_revision"),
                ),
            )
        except BaseException as error:
            self.restore(identity, error)
            raise
        finally:
            self.store.streams.finish(identity)

    def revalidate(self):
        """Restore a group attempt whose native access changed. A private
        attempt reads nothing a change can withdraw since Adaptive left
        (ADR 53)."""
        for identity, task in list(self.tasks.items()):
            rows = self.store.rows(
                "SELECT scope,payload FROM jobs WHERE id=? AND state IN ('queued','claimed','running')",
                (identity,),
            )
            if not rows or self.store.chat(rows[0]["scope"]).kind != "group":
                continue
            if not self.target_current(
                rows[0]["scope"],
                json.loads(rows[0]["payload"]).get("group_access_revision"),
            ):
                self.restore(
                    identity, Conflict("Group access changed; original draft restored")
                )
                self.store.streams.finish(identity)
                task.get_loop().call_soon_threadsafe(task.cancel)

    def restore(self, identity, error):
        reason = (
            str(error)
            if isinstance(error, (Refused, Conflict))
            else "Rewrite interrupted; use Rewrite to retry"
        )
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT * FROM rewrite_streams WHERE id=? AND state='active'",
                (identity,),
            ).fetchone()
            if row:
                current = db.execute(
                    "SELECT body FROM drafts WHERE chat_key=?", (row["chat_key"],)
                ).fetchone()
                draft = json.loads(current[0]) if current else {}
                if (
                    draft.get("attempt") == identity
                    and draft.get("revision") == row["draft_revision"]
                ):
                    base = json.loads(row["base"])
                    base.update(
                        revision=draft["revision"] + 1, state="failed", error=reason
                    )
                    db.execute(
                        "UPDATE drafts SET body=? WHERE chat_key=?",
                        (json.dumps(base), row["chat_key"]),
                    )
            db.execute(
                "UPDATE jobs SET state=?,error=? WHERE id=? AND state IN ('queued','claimed','running')",
                (
                    "paused" if isinstance(error, ProcessingPaused) else "failed",
                    reason,
                    identity,
                ),
            )

    def stop(self, key, revision, identity, *, undo=False, visible=None):
        with self.store.transaction() as db:
            row = db.execute(
                "SELECT body FROM drafts WHERE chat_key=?", (key,)
            ).fetchone()
            draft = json.loads(row[0]) if row else {}
            stream = db.execute(
                "SELECT * FROM rewrite_streams WHERE id=? AND chat_key=? AND state='active'",
                (identity, key),
            ).fetchone()
            # Completion may land between the browser's last snapshot and its
            # click. Only that exact attempt's one final revision may be crossed;
            # an intervening owner edit still conflicts.
            if (
                not stream
                and draft.get("attempt") == identity
                and draft.get("state") == "rewritten"
                and draft.get("revision") == revision + 1
            ):
                stream = {
                    "base": json.dumps({"text": draft["undo"]}),
                    "text": draft["text"],
                }
                revision = draft["revision"]
            if (
                not stream
                or draft.get("attempt") != identity
                or draft.get("revision") != revision
            ):
                raise Conflict("This rewrite already changed; reload the draft")
            base = json.loads(stream["base"])
            # The browser sends the prefix it actually displayed, not an unseen
            # newer snapshot. None means no text arrived in that browser yet.
            if visible is not None and not (stream["text"] or "").startswith(visible):
                raise Conflict("Displayed rewrite changed; reload the draft")
            text = base["text"] if undo or visible is None else visible
            draft.update(
                text=text,
                undo=None if undo else base["text"],
                state="idle" if undo else "stopped",
                attempt=None,
                error=None,
                revision=revision + 1,
            )
            db.execute(
                "UPDATE drafts SET body=? WHERE chat_key=?", (json.dumps(draft), key)
            )
            db.execute(
                "UPDATE rewrite_streams SET state='cancelled',text=NULL,base=NULL WHERE id=?",
                (identity,),
            )
            db.execute(
                "UPDATE jobs SET state='cancelled',error='Stopped by owner' WHERE id=?",
                (identity,),
            )
        if task := self.tasks.get(identity):
            task.get_loop().call_soon_threadsafe(task.cancel)
        return draft
