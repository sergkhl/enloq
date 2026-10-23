"""Traits (ADR 55): a chat's person under its chat key, the account's holder
under the subject "account". Kept until cleared, like memory."""

DDL = [
    # One value per trait: the decision model's, or the owner's own (a NULL
    # value is the owner's clear), which the model never changes.
    "CREATE TABLE IF NOT EXISTS trait_scales (subject TEXT NOT NULL, trait TEXT NOT NULL, value TEXT, "
    "probability REAL, evidence REAL, judged REAL NOT NULL, source TEXT NOT NULL, "
    "PRIMARY KEY (subject, trait))",
    # MiMo's cited notes and the owner's own.
    "CREATE TABLE IF NOT EXISTS trait_notes (id TEXT PRIMARY KEY, subject TEXT NOT NULL, text TEXT NOT NULL, "
    "citations TEXT NOT NULL, confidence REAL, source TEXT NOT NULL, created REAL NOT NULL, guidance INTEGER, "
    "defaults INTEGER)",
    "CREATE TABLE IF NOT EXISTS trait_state (subject TEXT PRIMARY KEY, revision INTEGER NOT NULL, "
    "scales_judged REAL, notes_judged REAL, error TEXT)",
]
TABLES = ("trait_scales", "trait_notes", "trait_state")
