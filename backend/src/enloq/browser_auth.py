"""Durable browser and device trust and short-lived, approved pairing.

Only token hashes are persisted. Pairing state belongs to the one service
process, so restarting can never approve an interrupted connection. A browser
holds its credential in a cookie; a Trusted Device (ADR 38) sends its own as a
bearer header, and neither credential works in the other's place.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field

from .config import private_directory, private_write
from .domain.models import Refused
from .storage.events import commits
from .storage.members import OWNER

COOKIE_AGE = 400 * 24 * 3600
PAIRING_AGE = 120
# ADR 43 Q8, plan defaults: a code posted to the Owner Account's Saved
# Messages lasts two minutes, five wrong codes end its attempt, at most five
# code messages go out an hour, and an attempt ends ten minutes after it began.
CODE_AGE = 120
CODE_TRIES = 5
CODE_MESSAGES = 5
CODE_WINDOW = 3600
CODE_ATTEMPT_AGE = 600
# ADR 47 Q11: at most five sign-in pairings start an hour for one phone
# number, each asking Telegram for a login code. Since ADR 48 Q11 anyone may
# start one, so the limit is per number and per client address, under a
# ceiling for the server.
SIGN_IN_STARTS = 5
SIGN_IN_SERVER_STARTS = 30


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def browser_label(agent: str, *, remote: bool = False) -> str:
    place = "this Mac"
    if remote:
        # A VPS service is reached from several devices; name the platform.
        place = next(
            (
                name
                for marker, name in (
                    ("iPhone", "an iPhone"),
                    ("iPad", "an iPad"),
                    ("Android", "Android"),
                    ("Macintosh", "a Mac"),
                    ("Windows", "Windows"),
                    ("Linux", "Linux"),
                )
                if marker in agent
            ),
            "another device",
        )
    for marker, label in (
        ("Edg/", "Edge"),
        ("Firefox/", "Firefox"),
        ("Chrome/", "Chromium browser"),
        ("Safari/", "Safari"),
    ):
        if marker in agent:
            return f"{label} on {place}"
    return f"Browser on {place}"


def device_name(name: str) -> str:
    # The app names its own phone. The owner still compares the code, so the
    # name only tells attempts apart; keep it short and printable.
    return " ".join("".join(c if c.isprintable() else " " for c in name).split())[:60]


def device_label(name: str) -> str:
    name = device_name(name)
    return f"Enloq app on {name}" if name else "Enloq app"


@dataclass
class Pairing:
    id: str
    expires_at: float
    state: str = "waiting"
    code: str = ""
    label: str = ""
    kind: str = ""
    # The trusted browser or phone that started this pairing (ADR 38 Q24,
    # ADR 47 Q1), or empty when the Mac launcher or a fixture started it.
    owner: str = field(default="", repr=False)
    proof_hash: str = field(default="", repr=False)
    # The claimant's own name, for the note in Saved Messages (ADR 47 Q2).
    name: str = field(default="", repr=False)

    def public(self):
        return dict(
            id=self.id,
            expires_at=self.expires_at,
            state=self.state,
            code=self.code,
            label=self.label,
            kind=self.kind,
            approver="browser" if self.owner else "server",
        )


@dataclass
class CodePairing:
    """A pairing approved by the code the service posts to the Owner
    Account's Saved Messages: a phone's (ADR 43 Q8) or, since ADR 44 Q10, a
    browser's. It is never served by the pairing status routes, and only the
    code's hash is kept."""

    id: str
    label: str
    # The phone's own name or the browser's label, for the notes in Saved
    # Messages.
    name: str
    started: float
    proof_hash: str = field(repr=False)
    code_hash: str = field(default="", repr=False)
    expires_at: float = 0
    wrong: int = 0
    state: str = "pending"
    # What the code trusts: a device credential or a browser's cookie.
    kind: str = "device"


@dataclass
class SignInPairing:
    """A pairing that signing the Owner Account in with Telegram approves,
    while that account is signed out of the service (ADR 47 Q11, Q13). The
    login itself runs in the service's LoginManager; this attempt binds its
    steps to one phone or browser by proof."""

    id: str
    label: str
    # The phone's own name or the browser's label, for the note.
    name: str
    started: float
    proof_hash: str = field(repr=False)
    kind: str = "device"
    # The LoginManager flow this attempt drives.
    flow: str = ""
    state: str = "pending"
    # A digest of the number's digits: the same phone retrying the same
    # number ends its earlier attempt, another person's stays (ADR 48).
    number: str = field(default="", repr=False)


class BrowserAuth:
    def __init__(self, settings, *, clock=time.time):
        self.clock = clock
        self.remote = settings.remote
        # ADR 40: a VPS needs TLS for its cookies. Since ADR 47 Q1 nothing
        # pairs it from its terminal: browsers pair with the Saved Messages
        # code, or by signing in with Telegram, on their connection screen.
        self.secure = settings.remote
        self.connect_hint = (
            "Pair this browser on its connection screen"
            if settings.remote
            else "Connect this browser from the Mac launcher"
        )
        self.device_hint = "This device is no longer trusted. Pair it again."
        self.origin = settings.origin
        self.scope = f"{settings.mode}_{settings.port}"
        self.cookie = f"reply_browser_{self.scope}"
        self.pair_cookie = f"reply_pair_{self.scope}_"
        self.path = private_directory(settings.data) / "browsers.sqlite3"
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        os.fchmod(fd, 0o600)
        os.close(fd)
        with self._db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS browsers ("
                "id TEXT PRIMARY KEY, scope TEXT NOT NULL, token_hash TEXT UNIQUE NOT NULL,"
                "label TEXT NOT NULL, approved_at REAL NOT NULL, last_access REAL NOT NULL,"
                "revoked INTEGER NOT NULL DEFAULT 0, kind TEXT NOT NULL DEFAULT 'browser')"
            )
            columns = {row["name"] for row in db.execute("PRAGMA table_info(browsers)")}
            if "kind" not in columns:
                # Trusted Devices (ADR 38) share the table; earlier rows are browsers.
                db.execute(
                    "ALTER TABLE browsers ADD COLUMN kind TEXT NOT NULL DEFAULT 'browser'"
                )
            if "member" not in columns:
                # ADR 48: each client belongs to one member; every client from
                # before members existed is the owner's.
                db.execute(
                    f"ALTER TABLE browsers ADD COLUMN member TEXT NOT NULL DEFAULT '{OWNER}'"
                )
        self._control = secrets.token_urlsafe(32)
        private_write(settings.data / "bootstrap", self._control)
        self._pairings: dict[str, Pairing] = {}
        self._codes: dict[str, CodePairing] = {}
        # When each recent code message went out, for the hourly limit.
        self._code_messages: deque[float] = deque()
        self._sign_ins: dict[str, SignInPairing] = {}
        self._sign_in_starts: deque[float] = deque()
        # Each number's and each client address's recent starts, keyed by a
        # digest of the number's digits or by the address.
        self._sign_in_numbers: dict[str, deque[float]] = {}
        self._sign_in_addresses: dict[str, deque[float]] = {}

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
            if db.total_changes:
                commits.publish()
        finally:
            db.close()

    def control(self, secret: str):
        if not secrets.compare_digest(secret.encode(), self._control.encode()):
            raise Refused(
                "Server pairing approval required"
                if self.remote
                else "Local launcher approval required"
            )

    def authenticate(self, token: str, kind: str = "browser") -> str | None:
        client = self.client(token, kind)
        return client["id"] if client else None

    def client(self, token: str, kind: str = "browser") -> dict | None:
        """The trusted client a credential names, with its member (ADR 48)."""
        if not token or len(token) > 200:
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT id,last_access,member FROM browsers WHERE scope=? AND token_hash=? AND revoked=0 AND kind=?",
                (self.scope, digest(token), kind),
            ).fetchone()
            if not row:
                return None
            if self.clock() - row["last_access"] >= 60:
                db.execute(
                    "UPDATE browsers SET last_access=? WHERE id=?",
                    (self.clock(), row["id"]),
                )
            return {"id": row["id"], "member": row["member"]}

    def member_of(self, identity: str) -> str | None:
        """The member of a trusted client, revoked or not; None if unknown."""
        if not identity:
            return None
        with self._db() as db:
            row = db.execute(
                "SELECT member FROM browsers WHERE id=? AND scope=?",
                (identity, self.scope),
            ).fetchone()
        return row["member"] if row else None

    def member_clients(self, member: str) -> list[str]:
        """A member's trusted clients that are still trusted."""
        with self._db() as db:
            return [
                row["id"]
                for row in db.execute(
                    "SELECT id FROM browsers WHERE scope=? AND member=? AND revoked=0",
                    (self.scope, member),
                )
            ]

    def active(self, identity: str) -> bool:
        with self._db() as db:
            return (
                db.execute(
                    "SELECT 1 FROM browsers WHERE id=? AND scope=? AND revoked=0",
                    (identity, self.scope),
                ).fetchone()
                is not None
            )

    def browsers(self, current: str):
        """The current client's member's trusted browsers and devices."""
        member = self.member_of(current) or OWNER
        with self._db() as db:
            rows = db.execute(
                "SELECT id,label,approved_at,last_access,kind FROM browsers WHERE scope=? AND revoked=0 AND member=? ORDER BY approved_at",
                (self.scope, member),
            ).fetchall()
        return [dict(row) | {"current": row["id"] == current} for row in rows]

    def member_activity(self, member: str) -> float | None:
        """When any of a member's trusted clients was last used (People)."""
        with self._db() as db:
            row = db.execute(
                "SELECT max(last_access) FROM browsers WHERE member=? AND scope=? AND revoked=0",
                (member, self.scope),
            ).fetchone()
        return row[0]

    def revoke_member(self, member: str):
        """Forget every client of a member who left or was removed (ADR 48)."""
        with self._db() as db:
            db.execute(
                "UPDATE browsers SET revoked=1 WHERE member=? AND scope=?",
                (member, self.scope),
            )

    def revoke(self, identity: str, member: str | None = None):
        """Forget a client; with ``member``, only one of that member's."""
        if member is not None and self.member_of(identity) != member:
            raise Refused("This browser or device isn't on your trusted list")
        with self._db() as db:
            db.execute(
                "UPDATE browsers SET revoked=1 WHERE id=? AND scope=?",
                (identity, self.scope),
            )

    def create_pairing(self, owner: str = "") -> Pairing:
        self._pairings = {
            key: value
            for key, value in self._pairings.items()
            if value.expires_at > self.clock()
        }
        if (
            sum(
                p.state in {"waiting", "pending", "approved"}
                for p in self._pairings.values()
            )
            >= 8
        ):
            raise Refused(
                "Close pending connection attempts and try again in two minutes"
            )
        pairing = Pairing(
            secrets.token_hex(16), self.clock() + PAIRING_AGE, owner=owner
        )
        self._pairings[pairing.id] = pairing
        return pairing

    def owned(self, identity: str, owner: str) -> Pairing:
        pairing = self.pairing(identity)
        if not owner or pairing.owner != owner:
            raise Refused("This pairing was started in another browser")
        return pairing

    def pairing(self, identity: str) -> Pairing:
        pairing = self._pairings.get(identity)
        if not pairing:
            raise Refused(
                "Connection attempt ended. Start again with Pair a phone."
                if self.remote
                else "Connection attempt ended. Open Enloq from the Mac launcher again."
            )
        # A finished pairing keeps its outcome past the two minutes, so the
        # approving screen never reads a completed one as expired.
        if pairing.expires_at <= self.clock() and pairing.state in {
            "waiting",
            "pending",
            "approved",
        }:
            pairing.state = "expired"
        return pairing

    def claim(self, identity: str, proof: str, agent: str, current: str | None):
        pairing = self.pairing(identity)
        if pairing.owner:
            # ADR 47 Q10: a trusted client's QR code pairs the Enloq app only;
            # browsers pair on their own connection screen.
            raise Refused(
                "This QR code pairs the Enloq app. Open Enloq on the phone and scan it there."
            )
        if pairing.state != "waiting":
            self.check_proof(pairing, proof)
            return pairing, None
        token = secrets.token_urlsafe(32)
        pairing.proof_hash = digest(token)
        pairing.kind = "browser"
        pairing.label = browser_label(agent, remote=self.remote)
        pairing.name = pairing.label
        pairing.code = f"{secrets.randbelow(1_000_000):06d}"
        pairing.state = "already_trusted" if current else "pending"
        return pairing, token

    def claim_device(self, identity: str, name: str):
        # The proof travels in the response body once; the app keeps it in
        # memory for the status and completion requests.
        pairing = self.pairing(identity)
        if pairing.state != "waiting":
            raise Refused(
                "This pairing expired. Start a new one."
                if pairing.state == "expired"
                else "This pairing was already used. Start a new one."
            )
        token = secrets.token_urlsafe(32)
        pairing.proof_hash = digest(token)
        pairing.kind = "device"
        pairing.label = device_label(name)
        pairing.name = device_name(name) or "a phone"
        pairing.code = f"{secrets.randbelow(1_000_000):06d}"
        pairing.state = "pending"
        return pairing, token

    def check_proof(self, pairing: Pairing, proof: str):
        if (
            not proof
            or not pairing.proof_hash
            or not secrets.compare_digest(pairing.proof_hash, digest(proof))
        ):
            raise Refused(
                "This connection attempt belongs to another browser or device"
            )

    def decide(self, identity: str, action: str, code: str):
        pairing = self.pairing(identity)
        if action == "cancel" and pairing.state in {"waiting", "pending", "approved"}:
            pairing.state = "cancelled"
        elif action == "approve":
            if pairing.state != "pending" or not secrets.compare_digest(
                pairing.code.encode(), code.encode()
            ):
                raise Refused("The browser no longer matches this approval")
            pairing.state = "approved"
        return pairing

    def decide_owned(self, identity: str, owner: str, action: str, code: str):
        self.owned(identity, owner)
        return self.decide(identity, action, code)

    def complete(self, identity: str, proof: str, kind: str = "browser"):
        pairing = self.pairing(identity)
        self.check_proof(pairing, proof)
        if pairing.kind != kind:
            raise Refused(
                "This connection attempt belongs to another browser or device"
            )
        if pairing.state != "approved":
            raise Refused("This browser has not been approved or the attempt has ended")
        # A phone paired from a trusted client joins that client's member
        # (ADR 48 Q12); the launcher and fixtures pair the owner's.
        member = (self.member_of(pairing.owner) if pairing.owner else None) or OWNER
        token = self._issue(pairing.label, kind, member=member)
        pairing.state = "complete"
        return token

    def _issue(self, label: str, kind: str, *, member: str = OWNER) -> str:
        token, browser_id = secrets.token_urlsafe(32), secrets.token_hex(16)
        with self._db() as db:
            db.execute(
                "INSERT INTO browsers (id,scope,token_hash,label,approved_at,last_access,kind,member) VALUES (?,?,?,?,?,?,?,?)",
                (
                    browser_id,
                    self.scope,
                    digest(token),
                    label,
                    self.clock(),
                    self.clock(),
                    kind,
                    member,
                ),
            )
        return token

    def start_code(
        self, name: str, *, kind: str = "device", agent: str = ""
    ) -> tuple[CodePairing, str]:
        """A pairing that a code in the Owner Account's Saved Messages
        approves (ADR 43 Q8): a phone named by its app, or a browser named by
        its user agent (ADR 44 Q10). Each has one live attempt: starting again
        ends its earlier one. The proof travels in the response body once."""
        now = self.clock()
        self._codes = {
            key: value
            for key, value in self._codes.items()
            if value.state == "pending" and now - value.started < CODE_ATTEMPT_AGE
        }
        browser = kind == "browser"
        label = (
            browser_label(agent, remote=self.remote) if browser else device_label(name)
        )
        for attempt in self._codes.values():
            if attempt.label == label:
                attempt.state = "ended"
        if sum(a.state == "pending" for a in self._codes.values()) >= 8:
            raise Refused("Close pending connection attempts and try again later")
        proof = secrets.token_urlsafe(32)
        attempt = CodePairing(
            secrets.token_hex(16),
            label,
            label if browser else device_name(name) or "a phone",
            now,
            digest(proof),
            kind="browser" if browser else "device",
        )
        self._codes[attempt.id] = attempt
        return attempt, proof

    def code_attempt(
        self, identity: str, proof: str, kind: str | None = None
    ) -> CodePairing:
        attempt = self._codes.get(identity)
        if (
            not attempt
            or not proof
            or not secrets.compare_digest(attempt.proof_hash, digest(proof))
            or (kind is not None and attempt.kind != kind)
        ):
            raise Refused("This connection attempt belongs to another device")
        if attempt.state == "pending" and (
            self.clock() - attempt.started >= CODE_ATTEMPT_AGE
        ):
            attempt.state = "ended"
        if attempt.state != "pending":
            raise Refused("This pairing ended. Start again.")
        return attempt

    def issue_code(self, attempt: CodePairing) -> str:
        """A fresh two-minute code for the attempt's next message, within the
        hourly limit on code messages. The service posts it; only its hash
        stays here."""
        now = self.clock()
        while self._code_messages and now - self._code_messages[0] >= CODE_WINDOW:
            self._code_messages.popleft()
        if len(self._code_messages) >= CODE_MESSAGES:
            raise Refused(
                "Five pairing codes went out in the last hour. Try again later."
            )
        self._code_messages.append(now)
        code = f"{secrets.randbelow(1_000_000):06d}"
        attempt.code_hash = digest(attempt.id + code)
        attempt.expires_at = now + CODE_AGE
        return code

    def end_code(self, attempt: CodePairing):
        attempt.state = "ended"

    def submit_code(
        self, identity: str, proof: str, code: str, *, kind: str = "device"
    ) -> tuple[CodePairing, str]:
        """The typed code: a match issues the phone's device credential or the
        browser's trust token, and five wrong codes end the attempt."""
        attempt = self.code_attempt(identity, proof, kind)
        if attempt.expires_at <= self.clock():
            raise Refused("This code expired. Send a new one.")
        if not re.fullmatch(r"[0-9]{6}", code) or not secrets.compare_digest(
            attempt.code_hash, digest(attempt.id + code)
        ):
            attempt.wrong += 1
            if attempt.wrong >= CODE_TRIES:
                attempt.state = "ended"
                raise Refused("Too many wrong codes. Start again.")
            left = CODE_TRIES - attempt.wrong
            raise Refused(
                f"That code doesn't match. {left} {'try' if left == 1 else 'tries'} left."
            )
        attempt.state = "complete"
        return attempt, self._issue(attempt.label, attempt.kind)

    def start_sign_in(
        self,
        name: str,
        *,
        kind: str = "device",
        agent: str = "",
        phone: str = "",
        address: str | None = None,
    ) -> tuple[SignInPairing, str]:
        """A sign-in pairing for a phone named by its app or a browser named by
        its user agent (ADR 47 Q11), within the hourly limits for its number
        and for the server (ADR 48 Q11). The proof travels in the response
        body once."""
        now = self.clock()
        while self._sign_in_starts and now - self._sign_in_starts[0] >= CODE_WINDOW:
            self._sign_in_starts.popleft()
        number = digest("".join(c for c in phone if c.isdigit()))
        for recent in (self._sign_in_numbers, self._sign_in_addresses):
            for key, starts in tuple(recent.items()):
                while starts and now - starts[0] >= CODE_WINDOW:
                    starts.popleft()
                if not starts:
                    del recent[key]
        if len(self._sign_in_numbers.get(number, ())) >= SIGN_IN_STARTS or (
            address and len(self._sign_in_addresses.get(address, ())) >= SIGN_IN_STARTS
        ):
            raise Refused("Five sign-ins started in the last hour. Try again later.")
        if len(self._sign_in_starts) >= SIGN_IN_SERVER_STARTS:
            raise Refused("Too many people are signing in. Try again later.")
        self._sign_ins = {
            key: value
            for key, value in self._sign_ins.items()
            if value.state == "pending" and now - value.started < CODE_ATTEMPT_AGE
        }
        browser = kind == "browser"
        label = (
            browser_label(agent, remote=self.remote) if browser else device_label(name)
        )
        proof = secrets.token_urlsafe(32)
        attempt = SignInPairing(
            secrets.token_hex(16),
            label,
            label if browser else device_name(name) or "a phone",
            now,
            digest(proof),
            kind="browser" if browser else "device",
        )
        self._sign_in_starts.append(now)
        self._sign_in_numbers.setdefault(number, deque()).append(now)
        if address:
            self._sign_in_addresses.setdefault(address, deque()).append(now)
        attempt.number = number
        self._sign_ins[attempt.id] = attempt
        return attempt, proof

    def earlier_sign_ins(self, attempt: SignInPairing) -> list[SignInPairing]:
        """The same phone's or browser's live attempts for the same number,
        which a new one ends; a label alone may be another person's."""
        return [
            other
            for other in self._sign_ins.values()
            if other is not attempt
            and other.label == attempt.label
            and other.number == attempt.number
            and other.state == "pending"
        ]

    def sign_in_attempt(self, identity: str, proof: str) -> SignInPairing:
        attempt = self._sign_ins.get(identity)
        if (
            not attempt
            or not proof
            or not secrets.compare_digest(attempt.proof_hash, digest(proof))
        ):
            raise Refused("This connection attempt belongs to another device")
        if attempt.state == "pending" and (
            self.clock() - attempt.started >= CODE_ATTEMPT_AGE
        ):
            attempt.state = "ended"
        if attempt.state != "pending":
            raise Refused("This sign-in ended. Start again.")
        return attempt

    @staticmethod
    def login_owner(attempt: SignInPairing) -> str:
        # Who drives the attempt's Telegram login, beside trusted clients.
        return f"sign-in:{attempt.id}"

    def login_owner_active(self, owner: str) -> bool:
        """A Telegram login continues only while whoever started it may: a
        trusted client, or a live sign-in pairing."""
        if owner.startswith("sign-in:"):
            attempt = self._sign_ins.get(owner.removeprefix("sign-in:"))
            return bool(
                attempt
                and attempt.state == "pending"
                and self.clock() - attempt.started < CODE_ATTEMPT_AGE
            )
        return self.active(owner)

    def end_sign_in(self, attempt: SignInPairing):
        attempt.state = "ended"

    def finish_sign_in(self, attempt: SignInPairing, member: str = OWNER) -> str:
        """The sign-in proved or created a member (ADR 47 Q11, ADR 48 Q11):
        the phone's device credential or the browser's trust token, theirs."""
        attempt.state = "complete"
        return self._issue(attempt.label, attempt.kind, member=member)
