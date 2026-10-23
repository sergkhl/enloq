"""Short-lived trusted tab presence and one sound/push channel per event."""

from ..domain.models import Refused
from ..storage.members import UNAVAILABLE

PRESENCE_TTL = 12


class NotificationAttention:
    def __init__(self, delivery):
        self.delivery = delivery
        with delivery.registry.transaction() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS notification_presence (browser TEXT NOT NULL, tab TEXT NOT NULL, account_id INTEGER, peer TEXT, focused INTEGER NOT NULL, audio INTEGER NOT NULL, seen REAL NOT NULL, PRIMARY KEY(browser,tab))"
            )

    def update(
        self, browser, tab, account_id, peer, focused, audio, push_permission=None
    ):
        self.delivery.browser(browser)
        member = self.delivery.member_of(browser)
        if (
            push_permission is not None
            and push_permission != "granted"
            and self.delivery.active(member) == browser
        ):
            self.delivery.retire(browser, "permission")
        if account_id is not None:
            # A tab reports presence only in its own member's accounts (ADR 48).
            if self.delivery.registry.member_of(account_id) != member:
                raise Refused(UNAVAILABLE)
            app = self.delivery.manager.get(account_id)
            if peer:
                app.store.chat(f"{account_id}/{peer}")
        with self.delivery.registry.transaction() as db:
            db.execute(
                "INSERT OR REPLACE INTO notification_presence VALUES (?,?,?,?,?,?,?)",
                (browser, tab, account_id, peer, focused, audio, self.delivery.clock()),
            )
            db.execute(
                "DELETE FROM notification_presence WHERE seen<?",
                (self.delivery.clock() - PRESENCE_TTL,),
            )
        return self.pending(browser, tab)

    def reading(self, event, member=None):
        """Whether one of the member's trusted clients is focused on this
        event's chat now."""
        d = self.delivery
        member = member or d.event_member(event)
        own = set(d.auth.member_clients(member))
        return any(
            r["focused"]
            and r["account_id"] == event["account_id"]
            and r["peer"] == event["peer"]
            and r["browser"] in own
            for r in d.registry.rows(
                "SELECT * FROM notification_presence WHERE seen>?",
                (d.clock() - PRESENCE_TTL,),
            )
        )

    def choose(self, event, member=None):
        """One channel among the event's member's own tabs and push
        destination (ADR 48): another member's tab never alerts."""
        d = self.delivery
        member = member or d.event_member(event)
        own = set(d.auth.member_clients(member))
        rows = [
            r
            for r in d.registry.rows(
                "SELECT * FROM notification_presence WHERE seen>? ORDER BY seen DESC, browser, tab",
                (d.clock() - PRESENCE_TTL,),
            )
            if r["browser"] in own
        ]
        foreground = [r for r in rows if r["focused"]]
        matching = [
            r
            for r in foreground
            if r["account_id"] == event["account_id"] and r["peer"] == event["peer"]
        ]
        if matching:
            return ("silent", matching[0]["browser"], matching[0]["tab"])
        if foreground:
            row = foreground[0]
            if not d.browser(row["browser"])["sound"]:
                return ("silent", row["browser"], row["tab"])
            return ("chime", row["browser"], row["tab"]) if row["audio"] else None
        active = d.active(member)
        if active and d.auth.active(active) and d.browser(active)["subscription"]:
            return ("push", active, None)
        audible = [r for r in rows if r["audio"] and d.browser(r["browser"])["sound"]]
        return ("chime", audible[0]["browser"], audible[0]["tab"]) if audible else None

    def pending(self, browser, tab):
        # Polling returns only opaque IDs. Claiming rechecks attention and source.
        current = self.delivery.browser(browser)
        return {
            "events": [
                r["id"]
                for r in self.delivery.registry.rows(
                    "SELECT id FROM notification_deliveries WHERE browser=? AND tab=? AND channel='chime' AND state='queued' AND expires>?",
                    (browser, tab, self.delivery.clock()),
                )
            ],
            "sound": bool(current["sound"]),
            **self.delivery.sounds.metadata(current),
        }

    def claim(self, browser, tab, identity):
        d = self.delivery
        d.browser(browser)
        rows = d.registry.rows(
            "SELECT * FROM notification_deliveries WHERE id=? AND browser=? AND tab=? AND channel='chime' AND state='queued'",
            (identity, browser, tab),
        )
        if not rows:
            return None
        row = rows[0]
        event = d.event(row)
        if not event:
            d.consume(row, "cancelled")
            return None
        if self.choose(event) != ("chime", browser, tab):
            # Nothing was emitted: release this unclaimed channel for the next tick.
            with d.registry.transaction() as db:
                db.execute(
                    "DELETE FROM notification_deliveries WHERE id=? AND state='queued' AND channel='chime'",
                    (identity,),
                )
            return None
        with d.registry.transaction() as db:
            claimed = db.execute(
                "UPDATE notification_deliveries SET state='sent' WHERE id=? AND state='queued'",
                (identity,),
            ).rowcount
        if not claimed:
            return None
        d.consume(row, "sent")
        return {
            "id": identity,
            "account_id": event["account_id"],
            "peer": event["peer"],
        }
