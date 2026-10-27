"""ADR 59 Q4: a message's vector, kept per revision and embedding model with
the 30-day content cache, and dropped when the message changes or goes."""

import array

DDL = (
    "CREATE TABLE IF NOT EXISTS group_vectors (source TEXT NOT NULL,"
    "revision INTEGER NOT NULL,model TEXT NOT NULL,created REAL NOT NULL,"
    "vector BLOB NOT NULL,PRIMARY KEY(source,revision,model))"
)


def pack(vector):
    return array.array("f", vector).tobytes()


def unpack(blob):
    value = array.array("f")
    value.frombytes(blob)
    return list(value)


def cached(db, model, revisions):
    """Stored vectors for the given {source: revision}, by source."""
    found = {}
    for source, revision in revisions.items():
        row = db.execute(
            "SELECT vector FROM group_vectors WHERE source=? AND revision=? AND model=?",
            (source, revision, model),
        ).fetchone()
        if row:
            found[source] = unpack(row[0])
    return found


def save(db, model, vectors, revisions, now):
    for source, vector in vectors.items():
        db.execute(
            "INSERT OR REPLACE INTO group_vectors VALUES (?,?,?,?,?)",
            (source, revisions[source], model, now, pack(vector)),
        )


def forget(db, source):
    db.execute("DELETE FROM group_vectors WHERE source=?", (source,))


def expire(db, cutoff):
    db.execute(
        "DELETE FROM group_vectors WHERE source NOT IN (SELECT key FROM messages) "
        "OR source IN (SELECT key FROM messages WHERE date<? OR deleted=1)",
        (cutoff,),
    )
