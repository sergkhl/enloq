"""Members of the shared service (ADR 48).

A member holds their own Telegram accounts, trusted clients, preferences,
budgets and notifications; the owner is the first member, `owner`. The rows
live in the registry beside the accounts they own, and an account store reads
its member's settings through the attached registry (`shared.`).
"""

OWNER = "owner"

# The answer an unknown or signed-out account gets, which another member's
# account gets too, so nothing confirms it exists.
UNAVAILABLE = "Sign in to this account to access its workspace"

# Settings each member keeps for themselves (ADR 48 Q20). The AI settings stay
# in the registry's metadata, server-wide and the owner's.
# ADR 53 Q1: Adaptive left.
TONES = {"neutral", "professional", "friendly"}
READING_VIEWS = {"ai", "originals"}

DDL = (
    "CREATE TABLE IF NOT EXISTS members (id TEXT PRIMARY KEY, role TEXT NOT NULL,"
    " state TEXT NOT NULL, home_account INTEGER UNIQUE, name TEXT NOT NULL DEFAULT '',"
    " username TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',"
    " agreed_version TEXT, agreed_at REAL, requested_at REAL, approved_at REAL)",
    "CREATE TABLE IF NOT EXISTS member_settings (member TEXT NOT NULL,"
    " key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(member,key))",
)

# The owner's preferences, budgets and provider pseudonym before members
# existed, moved once from metadata.
MIGRATED = (
    "tone",
    "reading_view",
    "hide_bots",
    "push_destination",
    "daily_budget",
    "group_briefing_budget",
    "provider_user",
)


def account_member(db, account_id, *, shared=True):
    """The member holding an account, inside an account store's transaction;
    an account the registry doesn't hold counts as the owner's."""
    prefix = "shared." if shared else ""
    row = db.execute(
        f"SELECT member FROM {prefix}accounts WHERE account_id=?", (account_id,)
    ).fetchone()
    return row[0] if row else OWNER


def account_setting(db, key, account_id, *, shared=True):
    """One setting of an account's member, read inside an account store's
    transaction; an account the registry doesn't hold reads the owner's."""
    prefix = "shared." if shared else ""
    row = db.execute(
        f"SELECT value FROM {prefix}member_settings WHERE key=? AND member="
        f"coalesce((SELECT member FROM {prefix}accounts WHERE account_id=?),?)",
        (key, account_id, OWNER),
    ).fetchone()
    return row[0] if row else None
