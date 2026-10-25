"""Browser-owned delivery and durable channel claims, separate from AI admission."""

import asyncio
import json
import time
import uuid

from ..domain.models import Refused
from ..storage.members import OWNER
from ..transport.push import FakePushTransport, PushTransport, validate_subscription
from .notification_attention import NotificationAttention
from .notification_devices import DECISION_MODEL, NotificationDevices
from .notification_sounds import NotificationSounds
from .reminders import SUBJECT as REMINDER_SUBJECT


class NotificationDelivery:
    def __init__(
        self, manager, auth, *, clock=time.time, transport=None, device_transport=None
    ):
        self.manager, self.auth, self.registry, self.clock = (
            manager,
            auth,
            manager.registry,
            clock,
        )
        self.transport = transport or (
            FakePushTransport if manager.settings.mode == "offline" else PushTransport
        )(manager.settings)
        self.error = None
        with self.registry.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS notification_browsers (browser TEXT PRIMARY KEY, subscription TEXT, sound INTEGER NOT NULL DEFAULT 1, failure TEXT)"
            )
            db.execute(
                "CREATE TABLE IF NOT EXISTS notification_deliveries (id TEXT PRIMARY KEY, account_id INTEGER, peer TEXT, browser TEXT NOT NULL, channel TEXT NOT NULL, state TEXT NOT NULL, attempts INTEGER NOT NULL, retry REAL NOT NULL, expires REAL NOT NULL)"
            )
            columns = {
                r["name"]
                for r in db.execute("PRAGMA table_info(notification_deliveries)")
            }
            if "tab" not in columns:
                db.execute("ALTER TABLE notification_deliveries ADD COLUMN tab TEXT")
            if "subject" not in columns:
                # ADR 48 Q19: the waiting member a join-request push names.
                db.execute(
                    "ALTER TABLE notification_deliveries ADD COLUMN subject TEXT"
                )
        self.sounds = NotificationSounds(self)
        self.attention = NotificationAttention(self)
        self.devices = NotificationDevices(self, transport=device_transport)

    def browser(self, identity):
        if not self.auth.active(identity):
            raise Refused("Browser access was forgotten")
        with self.registry.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO notification_browsers(browser) VALUES (?)",
                (identity,),
            )
            return dict(
                db.execute(
                    "SELECT b.*,s.name AS sound_file_name,s.revision AS sound_revision FROM notification_browsers b LEFT JOIN notification_sounds s ON s.browser=b.browser WHERE b.browser=?",
                    (identity,),
                ).fetchone()
            )

    def member_of(self, identity):
        return self.auth.member_of(identity) or OWNER

    def event_member(self, event):
        """Whose phones and browsers an event alerts (ADR 48): a burst's
        account's member; a Test or a join request carries its own."""
        if event.get("member"):
            return event["member"]
        if event.get("account_id") is None:
            return OWNER
        return self.registry.member_of(event["account_id"]) or OWNER

    def active(self, member=OWNER):
        """The member's browser push destination (ADR 20, per member since
        ADR 48)."""
        return self.registry.member_setting(member, "push_destination")

    def status(self, identity):
        current = self.browser(identity)
        active = self.active(self.member_of(identity))
        labels = {b["id"]: b["label"] for b in self.auth.browsers(identity)}
        return {
            "browser": identity,
            "sound": bool(current["sound"]),
            "push": active == identity and bool(current["subscription"]),
            "destination": labels.get(active),
            "failure": current["failure"] or self.error,
            "public_key": self.transport.public_key(),
            **self.sounds.metadata(current),
        }

    def enable(self, identity, subscription):
        self.browser(identity)
        validate_subscription(subscription)
        with self.registry.transaction() as db:
            db.execute(
                "UPDATE notification_browsers SET subscription=?,failure=NULL WHERE browser=?",
                (json.dumps(subscription), identity),
            )
            self.registry.set_member_setting(
                self.member_of(identity), "push_destination", identity, db=db
            )
        return self.status(identity)

    def preferences(self, identity, *, sound=None, push=None, sound_preset=None):
        self.browser(identity)
        with self.registry.transaction() as db:
            if sound_preset is not None:
                self.sounds.select(db, identity, sound_preset)
            if sound is not None:
                db.execute(
                    "UPDATE notification_browsers SET sound=? WHERE browser=?",
                    (sound, identity),
                )
            if push is False:
                db.execute(
                    "UPDATE notification_browsers SET subscription=NULL WHERE browser=?",
                    (identity,),
                )
                self.registry.clear_member_setting(
                    self.member_of(identity), "push_destination", identity, db=db
                )
        return self.status(identity)

    def upload_sound(self, identity, name, data):
        self.sounds.upload(identity, name, data)
        return self.status(identity)

    def retire(self, identity, reason):
        with self.registry.transaction() as db:
            db.execute(
                "UPDATE notification_browsers SET subscription=NULL,failure=? WHERE browser=?",
                (reason, identity),
            )
            self.registry.clear_member_setting(
                self.member_of(identity), "push_destination", identity, db=db
            )

    def join_event(self, delivery):
        """A Join Request's alert for the owner (ADR 48 Q19), while it waits."""
        row = self.registry.member(delivery["subject"])
        if not row or row["state"] != "waiting":
            return None
        return {
            "id": delivery["id"],
            "kind": "join",
            "account_id": None,
            "peer": None,
            "sender": "Enloq",
            "account": "People",
            "count": 1,
            "text": f"{(row['name'] or 'Someone')[:100]} asks to join Enloq",
            "expires": delivery["expires"],
        }

    def subject_event(self, delivery):
        """A delivery that names a subject: the owner's decision-model push
        (ADR 49 Q14) or a Join Request (ADR 48 Q19)."""
        if delivery["subject"].startswith(REMINDER_SUBJECT):
            member = self.member_of(delivery.get("browser") or delivery.get("device"))
            return self.manager.reminders.event(
                delivery["subject"][len(REMINDER_SUBJECT) :], member
            )
        if delivery["subject"] == DECISION_MODEL:
            return self.decision_event(delivery)
        return self.join_event(delivery)

    def decision_event(self, delivery):
        """The owner's decision-model alert, only while the outage lasts."""
        if self.registry.decision_route() is None:
            return None
        return {
            "id": delivery["id"],
            "kind": "decider",
            "account_id": None,
            "peer": None,
            "sender": "Enloq",
            "account": "Settings",
            "count": 1,
            "text": "The decision model is unavailable. Emotion and suggestions "
            "wait until it answers again.",
            "expires": delivery["expires"],
        }

    def decision_outage(self):
        """Tell the owner's phones and browser destination once per outage
        (ADR 49 Q14), with the same opaque payload as a burst."""
        event = {
            "id": uuid.uuid4().hex,
            "account_id": None,
            "peer": None,
            "subject": DECISION_MODEL,
            "expires": self.clock() + 3600,
        }
        self.devices.fan_out(event, OWNER)
        active = self.active(OWNER)
        if active and self.auth.active(active) and self.browser(active)["subscription"]:
            self.claim(event, active, "push")

    def join_request(self, member):
        """Tell the owner's phones and browser destination about a new Join
        Request, with the same opaque payload as a burst."""
        event = {
            "id": uuid.uuid4().hex,
            "account_id": None,
            "peer": None,
            "subject": member,
            "expires": self.clock() + 3600,
        }
        self.devices.fan_out(event, OWNER)
        active = self.active(OWNER)
        if active and self.auth.active(active) and self.browser(active)["subscription"]:
            self.claim(event, active, "push")

    def event(self, delivery):
        if delivery["expires"] <= self.clock() or not self.auth.active(
            delivery["browser"]
        ):
            return None
        if delivery.get("subject"):
            return self.subject_event(delivery)
        if delivery["account_id"] is None:
            return {
                "id": delivery["id"],
                "account_id": None,
                "peer": None,
                "sender": "Enloq",
                "account": "Test",
                "count": 1,
                "text": "Notifications are ready on this browser.",
                "expires": delivery["expires"],
            }
        try:
            app = self.manager.get(delivery["account_id"])
            rows = app.store.rows(
                "SELECT * FROM incoming_notices WHERE id=?", (delivery["id"],)
            )
            value = (
                app.notifications.candidate(rows[0], delivered=True) if rows else None
            )
            if value:
                value["account"] = self.registry.get(delivery["account_id"])["name"][
                    :80
                ]
                value["sender"] = value["sender"][:100]
            return value
        except Refused:
            return None

    def claim(self, event, identity, channel, tab=None):
        self.browser(identity)
        with self.registry.transaction() as db:
            return bool(
                db.execute(
                    "INSERT OR IGNORE INTO notification_deliveries (id,account_id,peer,browser,channel,state,attempts,retry,expires,tab,subject) VALUES (?,?,?,?,?,'queued',0,0,?,?,?)",
                    (
                        event["id"],
                        event["account_id"],
                        event["peer"],
                        identity,
                        channel,
                        event["expires"],
                        tab,
                        event.get("subject"),
                    ),
                ).rowcount
            )

    def consume(self, delivery, state):
        with self.registry.transaction() as db:
            db.execute(
                "UPDATE notification_deliveries SET state=? WHERE id=?",
                (state, delivery["id"]),
            )
        if delivery["account_id"] is not None:
            try:
                app = self.manager.get(delivery["account_id"])
                app.notifications.finish(
                    delivery, "delivered" if state == "sent" else "cancelled"
                )
            except Refused:
                pass

    async def tick(self):
        self.manager.reminders.tick()
        for row in self.registry.rows(
            "SELECT id,member FROM reminder_notices WHERE expires>?", (self.clock(),)
        ):
            event = self.manager.reminders.event(row["id"], row["member"])
            if not event:
                continue
            self.devices.fan_out(event, row["member"])
            active = self.active(row["member"])
            if (
                active
                and self.auth.active(active)
                and self.browser(active)["subscription"]
            ):
                self.claim(event, active, "push")
        for row in self.registry.rows(
            "SELECT value FROM member_settings WHERE key='push_destination'"
        ):
            if not self.auth.active(row["value"]):
                self.retire(row["value"], "forgotten")
        for runtime in tuple(self.manager.runtimes.values()):
            app = runtime.assistant
            app.notifications.advance()
            for row in app.notifications.rows():
                event = app.notifications.candidate(row)
                if not event:
                    continue
                # Only the account's member hears about it (ADR 48).
                member = self.event_member(event)
                # Phones are additional destinations, quiet only while one of
                # the member's clients reads this chat (ADR 38 Q5).
                if not self.attention.reading(event, member):
                    self.devices.fan_out(event, member)
                selected = self.attention.choose(event, member)
                if selected and selected[0] == "chime" and event.get("silent"):
                    selected = ("silent", selected[1], selected[2])
                prior = self.registry.rows(
                    "SELECT * FROM notification_deliveries WHERE id=?", (event["id"],)
                )
                if (
                    prior
                    and prior[0]["state"] == "queued"
                    and prior[0]["channel"] == "chime"
                    and selected != ("chime", prior[0]["browser"], prior[0]["tab"])
                ):
                    with self.registry.transaction() as db:
                        db.execute(
                            "DELETE FROM notification_deliveries WHERE id=? AND state='queued' AND channel='chime'",
                            (event["id"],),
                        )
                if selected:
                    channel, browser, tab = selected
                    if self.claim(event, browser, channel, tab) and channel == "silent":
                        self.consume({**event, "browser": browser}, "sent")
        for row in self.registry.rows(
            "SELECT * FROM notification_deliveries WHERE state IN ('queued','retry','sending') AND channel='push' AND retry<=?",
            (self.clock(),),
        ):
            await self.dispatch(row)
        await self.devices.dispatch_due()
        with self.registry.transaction() as db:
            db.execute(
                "DELETE FROM notification_deliveries WHERE expires<?",
                (self.clock() - 86400,),
            )
            self.devices.cleanup(db)
            db.execute(
                "DELETE FROM reminder_notices WHERE expires<?", (self.clock() - 86400,)
            )
            for row in db.execute(
                "SELECT browser FROM notification_browsers UNION SELECT browser FROM notification_sounds"
            ).fetchall():
                if not self.auth.active(row[0]):
                    self.sounds.forget(db, row[0])
                    db.execute(
                        "DELETE FROM notification_browsers WHERE browser=?", (row[0],)
                    )

    async def dispatch(self, row):
        event = self.event(row)
        if not event or self.active(self.member_of(row["browser"])) != row["browser"]:
            self.consume(row, "cancelled")
            return
        browser = self.browser(row["browser"])
        if not browser["subscription"] or row["attempts"] >= 3:
            self.consume(row, "failed")
            return
        payload = {
            "id": event["id"],
            "expires": event["expires"],
            "silent": bool(event.get("silent")) or not bool(browser["sound"]),
        }
        # Persist claim before network I/O: a second tab/channel cannot also alert.
        with self.registry.transaction() as db:
            claimed = db.execute(
                "UPDATE notification_deliveries SET state='sending',attempts=attempts+1,retry=? WHERE id=? AND state IN ('queued','retry','sending') AND retry<=? AND attempts=?",
                (self.clock() + 15, row["id"], self.clock(), row["attempts"]),
            ).rowcount
        if not claimed:
            return
        result = await asyncio.to_thread(
            self.transport.send,
            json.loads(browser["subscription"]),
            payload,
            row["expires"] - self.clock(),
        )
        if result == "retry" and row["attempts"] < 2:
            with self.registry.transaction() as db:
                db.execute(
                    "UPDATE notification_deliveries SET state='retry',retry=? WHERE id=?",
                    (self.clock() + 15 * (row["attempts"] + 1), row["id"]),
                )
        else:
            if result == "expired":
                self.retire(row["browser"], "expired")
            elif result != "sent":
                with self.registry.transaction() as db:
                    db.execute(
                        "UPDATE notification_browsers SET failure='delivery_failed' WHERE browser=?",
                        (row["browser"],),
                    )
            self.consume(row, "sent" if result == "sent" else "failed")

    def validate(self, identity, event_id):
        self.browser(identity)
        rows = self.registry.rows(
            "SELECT * FROM notification_deliveries WHERE id=? AND browser=? AND channel='push' AND state IN ('sending','sent','retry')",
            (event_id, identity),
        )
        event = self.event(rows[0]) if rows else None
        if not event or self.active(self.member_of(identity)) != identity:
            raise Refused("Notification is no longer available")
        return {
            **event,
            "silent": bool(event.get("silent"))
            or not bool(self.browser(identity)["sound"]),
        }

    def target(self, identity, event_id):
        self.browser(identity)
        rows = self.registry.rows(
            "SELECT * FROM notification_deliveries WHERE id=? AND browser=?",
            (event_id, identity),
        )
        if not rows:
            # Unknown, removed or another browser's locator opens nothing.
            return {"kind": "unavailable"}
        row = rows[0]
        if row["subject"] and row["subject"].startswith(REMINDER_SUBJECT):
            return self.manager.reminders.target(
                row["subject"][len(REMINDER_SUBJECT) :], self.member_of(identity)
            )
        if row["subject"] == DECISION_MODEL:
            return {"kind": "ai"}  # Settings → AI shows the outage (ADR 49).
        if row["subject"]:
            return {"kind": "people"}  # A Join Request opens People (ADR 48).
        if row["account_id"] is None:
            # A Test notification deliberately names no conversation.
            return {"kind": "test"}
        app = self.manager.get(row["account_id"])
        app.store.chat(f"{row['account_id']}/{row['peer']}")
        return {
            "kind": "conversation",
            "account_id": row["account_id"],
            "peer": row["peer"],
        }

    async def test(self, identity):
        self.browser(identity)
        if self.active(self.member_of(identity)) != identity:
            raise Refused("Enable push in this browser first")
        event = {
            "id": uuid.uuid4().hex,
            "account_id": None,
            "peer": None,
            "expires": self.clock() + 300,
        }
        self.claim(event, identity, "push")
        row = self.registry.rows(
            "SELECT * FROM notification_deliveries WHERE id=?", (event["id"],)
        )[0]
        await self.dispatch(row)
        state = self.registry.rows(
            "SELECT state FROM notification_deliveries WHERE id=?", (event["id"],)
        )[0]["state"]
        return {"state": state}

    async def run(self):
        while True:
            try:
                await self.tick()
                self.error = None
            except asyncio.CancelledError:
                raise
            except Exception:
                # A content-free operational status, not exception/endpoint text.
                self.error = "delivery_unavailable"
            await asyncio.sleep(1)
