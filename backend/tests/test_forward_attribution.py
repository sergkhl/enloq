"""ADR 27 attributed rewriting: new work receives forward details; existing
results are neither invalidated nor regenerated. No prompt-wording checks."""

import json

from enloq.application.context import current, incoming, projection
from enloq.application.suggestions import prepared

from backend.tests.test_rebuild import fixture, message

KEY = "1/user:42"
DETAILS = {
    "forwarded": True,
    "forwarded_from": "Authored channel",
    "forward_date": 1_709_564_640.0,
    "forward_signature": "Authored author",
    "imported": False,
}


def test_projection_carries_forward_details_only_where_present(tmp_path):
    app = fixture(tmp_path)
    forwarded = projection(message(app, 1, **DETAILS))
    assert {k: forwarded[k] for k in DETAILS if k != "imported"} == {
        k: v for k, v in DETAILS.items() if k != "imported"
    }
    assert "imported" not in forwarded
    # Imported history is the sender's own message, dated by its original.
    imported = projection(
        message(
            app,
            2,
            forwarded=True,
            forwarded_from="Imported name",
            forward_date=1_700_000_000.0,
            imported=True,
        )
    )
    assert imported["forwarded"] is False and imported["forwarded_from"] is None
    assert imported["imported"] is True
    assert imported["forward_date"] == 1_700_000_000.0
    assert "forward_signature" not in imported
    # Ordinary and legacy messages keep their earlier, compact projection.
    for attrs in ({}, {"forwarded": False}):
        plain = projection(message(app, 3, **attrs))
        assert not {"forward_date", "forward_signature", "imported"} & set(plain)


async def test_new_packets_and_suggestion_inputs_carry_the_details(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1, text="Earlier context", **DETAILS))
    app.sync.ingest(message(app, 2, text="Current target", **DETAILS))
    blocks = app.store.rows("SELECT * FROM blocks ORDER BY date")
    packet = await incoming(app, blocks[-1])
    target = packet["messages"][-1]
    assert target["forward_date"] == DETAILS["forward_date"]
    assert target["forward_signature"] == DETAILS["forward_signature"]
    for item in packet["messages"] + packet["context"]:
        kept = prepared(item, {})
        assert kept["forwarded_from"] == "Authored channel"
        assert kept["forward_date"] == DETAILS["forward_date"]
        assert kept["forward_signature"] == DETAILS["forward_signature"]


async def test_existing_results_stay_current_without_new_work(tmp_path):
    app = fixture(tmp_path)
    app.sync.ingest(message(app, 1, forwarded=True))
    block = app.store.rows("SELECT * FROM blocks")[0]
    packet = await incoming(app, block)
    with app.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='ready',rendered=?,rendered_revision=revision WHERE id=?",
            ("Authored earlier rendering", block["id"]),
        )
        db.execute("UPDATE jobs SET state='done'")
    before = {
        table: app.store.rows(f"SELECT * FROM {table}")
        for table in ("blocks", "jobs", "reservations")
    }
    # Learning the details for new work regenerates nothing already rendered.
    app.store.learn_forwarding(message(app, 1, **DETAILS))
    app.sync.ingest(message(app, 1, **DETAILS))
    assert current(app.store, packet)
    assert {
        table: app.store.rows(f"SELECT * FROM {table}") for table in before
    } == before
    stored = json.loads(
        app.store.rows("SELECT body FROM messages WHERE key=?", (f"{KEY}/1",))[0][
            "body"
        ]
    )
    assert stored["forward_signature"] == "Authored author"
