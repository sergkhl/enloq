"""Phone destinations (ADR 38 Q5): each Trusted
Device running the Enloq app may add its own FCM registration token beside the
single browser destination. Every ready burst gets one copy per phone unless some trusted
client is reading that chat; a phone's copy outlives the browser channel's
outcome and is rechecked against the same sources before it goes out."""

import asyncio
import uuid

from ..domain.models import Refused
from ..transport.fcm import FakeFcmTransport, FcmTransport, validate_token

TEST_TEXT = "Notifications are ready on this phone."
# The subject of the owner's decision-model push (ADR 49 Q14); a Join
# Request's subject is the waiting member's ID.
DECISION_MODEL = "decision-model"


class NotificationDevices:
    def __init__(self, delivery, *, transport=None):
        self.delivery = delivery
        self.manager, self.auth, self.registry = (
            delivery.manager,
            delivery.auth,
            delivery.registry,
        )
        settings = self.manager.settings
        self.settings = settings
        self.transport = transport or (
            FakeFcmTransport if settings.mode == "offline" else FcmTransport
        )(settings)
        with self.registry.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS notification_devices (device TEXT PRIMARY KEY, token TEXT, sound INTEGER NOT NULL DEFAULT 1, failure TEXT)"
            )
            columns = {
                row[1] for row in db.execute("PRAGMA table_info(notification_devices)")
            }
            if "endpoint" in columns:
                # UnifiedPush endpoints are no FCM tokens; phones register again.
                db.execute(
                    "ALTER TABLE notification_devices RENAME COLUMN endpoint TO token"
                )
                db.execute("UPDATE notification_devices SET token=NULL")
            db.execute(
                "CREATE TABLE IF NOT EXISTS device_deliveries (id TEXT NOT NULL, device TEXT NOT NULL, account_id INTEGER, peer TEXT, state TEXT NOT NULL, attempts INTEGER NOT NULL, retry REAL NOT NULL, expires REAL NOT NULL, PRIMARY KEY(id, device))"
            )
            if "subject" not in {
                row[1] for row in db.execute("PRAGMA table_info(device_deliveries)")
            }:
                # ADR 48 Q19: the waiting member a join-request push names.
                db.execute("ALTER TABLE device_deliveries ADD COLUMN subject TEXT")

    def clock(self):
        return self.delivery.clock()

    def device(self, identity):
        if not self.auth.active(identity):
            raise Refused("Device access was forgotten")
        with self.registry.transaction() as db:
            db.execute(
                "INSERT OR IGNORE INTO notification_devices(device) VALUES (?)",
                (identity,),
            )
            return dict(
                db.execute(
                    "SELECT * FROM notification_devices WHERE device=?", (identity,)
                ).fetchone()
            )

    def status(self, identity):
        current = self.device(identity)
        return {
            "push": bool(current["token"]),
            "sound": bool(current["sound"]),
            "available": bool(self.settings.fcm_project),
            "failure": current["failure"],
        }

    def enable(self, identity, token):
        self.device(identity)
        token = validate_token(self.settings, token)
        with self.registry.transaction() as db:
            db.execute(
                "UPDATE notification_devices SET token=?,failure=NULL WHERE device=?",
                (token, identity),
            )
        return self.status(identity)

    def preferences(self, identity, *, push=None, sound=None):
        self.device(identity)
        with self.registry.transaction() as db:
            if push is False:
                db.execute(
                    "UPDATE notification_devices SET token=NULL WHERE device=?",
                    (identity,),
                )
            if sound is not None:
                db.execute(
                    "UPDATE notification_devices SET sound=? WHERE device=?",
                    (sound, identity),
                )
        return self.status(identity)

    def retire(self, identity, reason):
        with self.registry.transaction() as db:
            db.execute(
                "UPDATE notification_devices SET token=NULL,failure=? WHERE device=?",
                (reason, identity),
            )

    def fan_out(self, event, member):
        """One queued copy per phone of the event's member with a token
        (ADR 48); repeats claim nothing."""
        own = set(self.auth.member_clients(member))
        devices = [
            row["device"]
            for row in self.registry.rows(
                "SELECT device FROM notification_devices WHERE token IS NOT NULL"
            )
            if row["device"] in own
        ]
        with self.registry.transaction() as db:
            for device in devices:
                db.execute(
                    "INSERT OR IGNORE INTO device_deliveries (id,device,account_id,peer,state,attempts,retry,expires,subject) VALUES (?,?,?,?,'queued',0,0,?,?)",
                    (
                        event["id"],
                        device,
                        event["account_id"],
                        event["peer"],
                        event["expires"],
                        event.get("subject"),
                    ),
                )

    def event(self, delivery):
        if delivery["expires"] <= self.clock() or not self.auth.active(
            delivery["device"]
        ):
            return None
        if delivery.get("subject"):
            return self.delivery.subject_event(delivery)
        if delivery["account_id"] is None:
            return {
                "id": delivery["id"],
                "account_id": None,
                "peer": None,
                "sender": "Enloq",
                "account": "Test",
                "count": 1,
                "text": TEST_TEXT,
                "expires": delivery["expires"],
            }
        try:
            app = self.manager.get(delivery["account_id"])
            rows = app.store.rows(
                "SELECT * FROM incoming_notices WHERE id=?", (delivery["id"],)
            )
            value = app.notifications.candidate(rows[0], settled=True) if rows else None
            if value:
                value["account"] = self.registry.get(delivery["account_id"])["name"][
                    :80
                ]
                value["sender"] = value["sender"][:100]
            return value
        except Refused:
            return None

    def finish(self, delivery, state):
        with self.registry.transaction() as db:
            db.execute(
                "UPDATE device_deliveries SET state=? WHERE id=? AND device=?",
                (state, delivery["id"], delivery["device"]),
            )
        if state != "sent" or delivery["account_id"] is None:
            return
        # A phone that alerted closes a still-open burst, so no browser
        # channel alerts for it again later (ADR 20's one alert per burst).
        try:
            app = self.manager.get(delivery["account_id"])
            rows = app.store.rows(
                "SELECT * FROM incoming_notices WHERE id=?", (delivery["id"],)
            )
            if rows:
                app.notifications.finish(rows[0], "delivered")
        except Refused:
            pass

    async def dispatch_due(self):
        for row in self.registry.rows(
            "SELECT * FROM device_deliveries WHERE state IN ('queued','retry','sending') AND retry<=?",
            (self.clock(),),
        ):
            await self.dispatch(row)

    async def dispatch(self, row):
        event = self.event(row)
        current = (
            self.registry.rows(
                "SELECT * FROM notification_devices WHERE device=?", (row["device"],)
            )
            if event
            else []
        )
        if not event or not current or not current[0]["token"]:
            self.finish(row, "cancelled")
            return
        if row["attempts"] >= 3:
            self.finish(row, "failed")
            return
        payload = {
            "id": event["id"],
            "expires": event["expires"],
            "silent": bool(event.get("silent")) or not bool(current[0]["sound"]),
        }
        # Persist the claim before network I/O, as the browser channel does.
        with self.registry.transaction() as db:
            claimed = db.execute(
                "UPDATE device_deliveries SET state='sending',attempts=attempts+1,retry=? WHERE id=? AND device=? AND state IN ('queued','retry','sending') AND retry<=? AND attempts=?",
                (
                    self.clock() + 15,
                    row["id"],
                    row["device"],
                    self.clock(),
                    row["attempts"],
                ),
            ).rowcount
        if not claimed:
            return
        result = await asyncio.to_thread(
            self.transport.send,
            current[0]["token"],
            payload,
            row["expires"] - self.clock(),
        )
        if result == "retry" and row["attempts"] < 2:
            with self.registry.transaction() as db:
                db.execute(
                    "UPDATE device_deliveries SET state='retry',retry=? WHERE id=? AND device=?",
                    (
                        self.clock() + 15 * (row["attempts"] + 1),
                        row["id"],
                        row["device"],
                    ),
                )
            return
        if result == "expired":
            self.retire(row["device"], "expired")
        elif result != "sent":
            with self.registry.transaction() as db:
                db.execute(
                    "UPDATE notification_devices SET failure='delivery_failed' WHERE device=?",
                    (row["device"],),
                )
        self.finish(row, "sent" if result == "sent" else "failed")

    def validate(self, identity, event_id):
        current = self.device(identity)
        rows = self.registry.rows(
            "SELECT * FROM device_deliveries WHERE id=? AND device=? AND state IN ('sending','sent','retry')",
            (event_id, identity),
        )
        event = self.event(rows[0]) if rows else None
        if not event:
            raise Refused("Notification is no longer available")
        return {
            **event,
            "silent": bool(event.get("silent")) or not bool(current["sound"]),
        }

    def target(self, identity, event_id):
        self.device(identity)
        rows = self.registry.rows(
            "SELECT * FROM device_deliveries WHERE id=? AND device=?",
            (event_id, identity),
        )
        if not rows:
            return {"kind": "unavailable"}
        row = rows[0]
        if row["subject"] == DECISION_MODEL:
            return {"kind": "ai"}  # Settings → AI shows the outage (ADR 49).
        if row["subject"]:
            return {"kind": "people"}  # A Join Request opens People (ADR 48).
        if row["account_id"] is None:
            return {"kind": "test"}
        app = self.manager.get(row["account_id"])
        app.store.chat(f"{row['account_id']}/{row['peer']}")
        return {
            "kind": "conversation",
            "account_id": row["account_id"],
            "peer": row["peer"],
        }

    async def test(self, identity):
        if not self.device(identity)["token"]:
            raise Refused("Turn on notifications on this phone first")
        event = {
            "id": uuid.uuid4().hex,
            "account_id": None,
            "peer": None,
            "expires": self.clock() + 300,
        }
        with self.registry.transaction() as db:
            db.execute(
                "INSERT INTO device_deliveries (id,device,account_id,peer,state,attempts,retry,expires) VALUES (?,?,NULL,NULL,'queued',0,0,?)",
                (event["id"], identity, event["expires"]),
            )
        row = self.registry.rows(
            "SELECT * FROM device_deliveries WHERE id=? AND device=?",
            (event["id"], identity),
        )[0]
        await self.dispatch(row)
        state = self.registry.rows(
            "SELECT state FROM device_deliveries WHERE id=? AND device=?",
            (event["id"], identity),
        )[0]["state"]
        return {"state": state}

    def cleanup(self, db):
        db.execute(
            "DELETE FROM device_deliveries WHERE expires<?", (self.clock() - 86400,)
        )
        for (device,) in db.execute(
            "SELECT device FROM notification_devices"
        ).fetchall():
            if not self.auth.active(device):
                db.execute("DELETE FROM notification_devices WHERE device=?", (device,))
                db.execute(
                    "DELETE FROM device_deliveries WHERE device=? AND state IN ('queued','retry')",
                    (device,),
                )
