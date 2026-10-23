"""Cached automatic eligibility, outside the runnable AI queue (ADR 35)."""

DDL = """CREATE TABLE IF NOT EXISTS incoming_deferred (
    scope TEXT PRIMARY KEY REFERENCES blocks(id) ON DELETE CASCADE,
    input_revision TEXT NOT NULL, due REAL NOT NULL, payload TEXT NOT NULL
)"""
