"""Private account-library metadata, never GIF queries or ephemeral results."""

import json

DDL = """CREATE TABLE IF NOT EXISTS media_catalogs (
    scope TEXT PRIMARY KEY, body TEXT NOT NULL, revision TEXT NOT NULL,
    updated REAL, stale INTEGER NOT NULL DEFAULT 1, error TEXT
)"""


class CatalogStore:
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            db.execute(DDL)

    def get(self, scope):
        rows = self.store.rows("SELECT * FROM media_catalogs WHERE scope=?", (scope,))
        if not rows:
            return None
        return {**rows[0], "body": json.loads(rows[0]["body"])}

    def put(self, scope, value, revision):
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO media_catalogs VALUES (?,?,?,?,0,NULL) ON CONFLICT(scope) DO UPDATE SET body=excluded.body,revision=excluded.revision,updated=excluded.updated,stale=0,error=NULL",
                (scope, value.model_dump_json(), revision, self.store.clock()),
            )

    def replace(self, scope, value, revision):
        # Updating one lazy pack does not refresh unrelated list/entitlement age.
        with self.store.transaction() as db:
            db.execute(
                "UPDATE media_catalogs SET body=?,revision=? WHERE scope=?",
                (value.model_dump_json(), revision, scope),
            )

    def mark_stale(self):
        with self.store.transaction() as db:
            db.execute("UPDATE media_catalogs SET stale=1")

    def prune_packs(self, retained):
        with self.store.transaction() as db:
            for row in db.execute(
                "SELECT scope FROM media_catalogs WHERE scope LIKE 'pack:%'"
            ).fetchall():
                if row[0] not in retained:
                    db.execute("DELETE FROM media_catalogs WHERE scope=?", (row[0],))

    def failed(self, scope, value, revision):
        with self.store.transaction() as db:
            db.execute(
                "INSERT INTO media_catalogs VALUES (?,?,?,NULL,1,?) ON CONFLICT(scope) DO UPDATE SET stale=1,error=excluded.error",
                (
                    scope,
                    value.model_dump_json(),
                    revision,
                    "Library refresh unavailable; try again",
                ),
            )

    def clear(self):
        with self.store.transaction() as db:
            db.execute("DELETE FROM media_catalogs")
