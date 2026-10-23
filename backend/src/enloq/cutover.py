"""One-time local content retirement. Never opens a Telegram client or secret file."""

import argparse
import json
import os
import shutil
import sqlite3
from contextlib import ExitStack

from .config import ROOT, Settings, private_write
from .domain.models import Chat, Refused
from .storage.accounts import AccountRegistry
from .storage.lock import OwnerLock
from .storage.store import Store

QUALITY_TABLES = (
    "quality_qualifications",
    "quality_deliveries",
    "quality_writes",
    "quality_attempts",
    "quality_cases",
    "quality_rounds",
    "quality_candidates",
    "quality_campaigns",
)
RECEIPT_FIELDS = {
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cost",
    "cost_details",
    "receipt_id",
    "finish_reason",
}


def retired_directories(settings, registry):
    paths = {registry.path_for(e["directory"]) / "media" for e in registry.accounts()}
    paths.update(
        settings.data / name
        for name in ("quality-authored", "quality-media", "codex-runs")
    )
    if settings.data == ROOT / ".local/personal":
        paths.update(
            ROOT / ".local" / name
            for name in (
                "quality",
                "campaigns",
                "classification-evaluation",
                "combined-evaluation",
            )
        )
    existing = sorted(p for p in paths if p.exists())
    for path in existing:
        for node in (path, *path.rglob("*")):
            if node.is_symlink() or ".session" in node.name or node.suffix == ".env":
                raise Refused(
                    "Unexpected authorization reference in retired content; directory retained"
                )
    return existing


def inventory(settings):
    root = settings.data
    path = root / "accounts.sqlite3"
    if not path.is_file():
        return {
            "accounts": [],
            "legacy_stores": int((root / "assistant.sqlite3").is_file()),
            "unregistered_legacy": (root / "assistant.sqlite3").is_file(),
        }
    records = []
    with sqlite3.connect(f"file:{path}?mode=ro", uri=True) as registry:
        for identity, directory, session, state in registry.execute(
            "SELECT account_id,directory,session,state FROM accounts"
        ):
            source = root / directory / "assistant.sqlite3"
            record = {
                "directory": directory,
                "state": state,
                "session_present": (root / session).is_file(),
                "legacy_store": source.is_file(),
            }
            if source.is_file():
                with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as db:
                    record["content_counts"] = {
                        table: db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                        for table in ("messages", "results", "composers")
                    }
                    record["unresolved_sends"] = db.execute(
                        "SELECT count(*) FROM operations WHERE state IN ('dispatching','unknown')"
                    ).fetchone()[0]
            records.append(record)
    return {
        "accounts": records,
        "legacy_stores": sum(r["legacy_store"] for r in records),
    }


def retire(settings, lock_path):
    with ExitStack() as locks:
        locks.enter_context(OwnerLock(lock_path))
        registry = AccountRegistry(settings.data)
        legacy_root = settings.data / "assistant.sqlite3"
        if not registry.accounts() and legacy_root.exists():
            if settings.mode != "offline":
                raise Refused(
                    "Register the existing app session before personal content retirement"
                )
            with sqlite3.connect(f"file:{legacy_root}?mode=ro", uri=True) as db:
                identity = dict(
                    db.execute(
                        "SELECT key,value FROM metadata WHERE key IN ('account_id','mode')"
                    )
                )
            if identity.get("mode") != "offline":
                raise Refused("The unregistered content is not an offline fixture")
            registry.register(
                int(identity["account_id"]),
                "synthetic.session",
                directory=".",
                name="Demo account",
            )
        entries = registry.accounts()
        if registry.rows("SELECT id FROM pending_logins"):
            raise Refused(
                "Finish or cancel the pending login before content retirement"
            )
        tables = {
            r["name"]
            for r in registry.rows("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "quality_deliveries" in tables and registry.rows(
            "SELECT 1 FROM quality_deliveries WHERE state NOT IN ('confirmed','refused','purged') LIMIT 1"
        ):
            raise Refused(
                "Unresolved historical campaign delivery; qualification records retained"
            )
        for entry in entries:
            session = registry.path_for(entry["session"])
            locks.enter_context(
                OwnerLock(session.with_name(session.name + ".owner.lock"))
            )
        before = inventory(settings)
        directories = retired_directories(settings, registry)
        migrated = 0
        for entry in entries:
            directory = registry.path_for(entry["directory"])
            legacy = registry.path_for(entry["directory"] + "/assistant.sqlite3")
            if not legacy.exists():
                continue
            with sqlite3.connect(f"file:{legacy}?mode=ro", uri=True) as src:
                src.row_factory = sqlite3.Row
                identity = dict(
                    src.execute(
                        "SELECT key,value FROM metadata WHERE key IN ('account_id','mode','timezone')"
                    )
                )
                if identity != {
                    "account_id": str(entry["account_id"]),
                    "mode": settings.mode,
                    "timezone": settings.timezone,
                }:
                    raise Refused(
                        "Legacy identity or accounting timezone mismatch; store retained"
                    )
                store = Store(
                    directory / "rewriting.sqlite3",
                    account_id=entry["account_id"],
                    mode=settings.mode,
                    timezone=settings.timezone,
                    shared_path=registry.path,
                )
                if any(
                    store.rows(f"SELECT 1 FROM {table} LIMIT 1")
                    for table in ("messages", "drafts", "jobs")
                ):
                    raise Refused(
                        "New content already exists; legacy store retained for explicit reconciliation"
                    )
                with store.transaction() as db:
                    # Transfer accounting only. Retired content/results never enter the new store.
                    for row in src.execute(
                        "SELECT r.*,j.scope,j.role,j.input_revision FROM reservations r JOIN jobs j ON j.id=r.id"
                    ):
                        db.execute(
                            "INSERT OR IGNORE INTO shared.model_reservations (account_id,id,scope,role,input_revision,bucket,engine,reserved,actual,status,created) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                            (
                                entry["account_id"],
                                row["id"],
                                row["scope"],
                                row["role"],
                                row["input_revision"],
                                row["bucket"],
                                row["engine"],
                                row["reserved"],
                                row["actual"],
                                row["status"],
                                row["created"],
                            ),
                        )
                        saved = db.execute(
                            "SELECT reserved,actual,status,family FROM shared.model_reservations WHERE account_id=? AND id=?",
                            (entry["account_id"], row["id"]),
                        ).fetchone()
                        if (
                            not saved
                            or saved["family"] != "daily"
                            or saved["reserved"] != row["reserved"]
                            or (
                                row["actual"] is not None
                                and saved["actual"] != row["actual"]
                            )
                        ):
                            raise Refused("Accounting differs; legacy content retained")
                    day = src.execute(
                        "SELECT value FROM metadata WHERE key='budget_day'"
                    ).fetchone()
                    if day:
                        db.execute(
                            "INSERT INTO shared.metadata VALUES ('budget_day',?) ON CONFLICT(key) DO UPDATE SET value=max(value,excluded.value)",
                            (day[0],),
                        )
                    for row in src.execute("SELECT id,body FROM results"):
                        result = json.loads(row["body"])
                        usage = {
                            k: v
                            for k, v in result.get("usage", {}).items()
                            if k in RECEIPT_FIELDS
                        }
                        if usage or result.get("engine") == "codex":
                            db.execute(
                                "INSERT OR IGNORE INTO shared.model_receipts (account_id,id,role,model,provider,usage,error) VALUES (?,?,?,?,?,?,?)",
                                (
                                    entry["account_id"],
                                    row["id"],
                                    result["role"],
                                    result["model"],
                                    result["provider"],
                                    json.dumps(usage),
                                    "Historical receipt; content retired",
                                ),
                            )
                            saved = db.execute(
                                "SELECT family FROM shared.model_receipts WHERE account_id=? AND id=?",
                                (entry["account_id"], row["id"]),
                            ).fetchone()
                            if not saved or saved["family"] != "daily":
                                raise Refused(
                                    "Accounting differs; legacy content retained"
                                )
                    # Preserve explicit per-chat eligibility and all pending send identities.
                    for row in src.execute("SELECT body FROM chats"):
                        chat = Chat.model_validate_json(row["body"])
                        if chat.kind == "private":
                            db.execute(
                                "INSERT OR IGNORE INTO chats VALUES (?,?,0)",
                                (chat.key, chat.model_dump_json()),
                            )
                    for row in src.execute(
                        "SELECT body FROM operations WHERE state IN ('prepared','dispatching','unknown')"
                    ):
                        old = json.loads(row["body"])
                        migrated += store.send_operations.import_retired(db, old)
                    for row in src.execute(
                        "SELECT key,value FROM cursors WHERE key LIKE 'receipt:%' OR key LIKE 'refusal:%'"
                    ):
                        store.send_operations.import_retired_evidence(db, *row)
                    db.execute(
                        "INSERT OR REPLACE INTO metadata VALUES ('legacy_content_retired','1')"
                    )
            # All minimal safeguards are durable before removing the retired store.
            for suffix in ("", "-journal", "-wal", "-shm"):
                legacy.with_name(legacy.name + suffix).unlink(missing_ok=True)
        with registry.transaction() as db:
            # Refuse to erase a possibly delivered campaign send that is absent
            # from ordinary operations. The old campaign itself is never reopened.
            tables = {
                r[0]
                for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if (
                "quality_deliveries" in tables
                and db.execute(
                    "SELECT 1 FROM quality_deliveries WHERE state NOT IN ('confirmed','refused','purged') LIMIT 1"
                ).fetchone()
            ):
                raise Refused(
                    "Unresolved historical campaign delivery; qualification records retained"
                )
            for table in QUALITY_TABLES:
                db.execute(f"DROP TABLE IF EXISTS {table}")
            db.execute(
                "INSERT OR REPLACE INTO metadata VALUES ('rewriting_cutover','complete')"
            )
        for directory in directories:
            shutil.rmtree(directory)
        report = {
            "before": before,
            "after": inventory(settings),
            "unresolved_sends_preserved": migrated,
            "accounting": registry.usage(),
            "sessions_and_browser_trust_preserved": True,
            "retired_directories": [
                str(p.relative_to(settings.data.parent)) for p in directories
            ],
        }
        return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offline", "personal"], required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    settings = Settings.load(args.mode)
    result = (
        retire(settings, ROOT / ".local/service.owner.lock")
        if args.apply
        else inventory(settings)
    )
    if args.apply:
        private_write(
            ROOT / f".local/rebuild-cutover-{args.mode}.json",
            json.dumps(result, indent=2),
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
