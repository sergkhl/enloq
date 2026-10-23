"""Readiness is content state, independent of alert admission and delivery."""

import pytest
from enloq.domain.models import ProcessingPaused

from backend.tests.test_local_viewing import ack, rows, see
from backend.tests.test_notifications import complete
from backend.tests.test_rebuild import fixture, message


@pytest.fixture
def rig(tmp_path):
    now = [2_000_000_000.0]
    return fixture(tmp_path, clock=lambda: now[0]), now


def advance(app):
    app.unseen.advance()
    app.notifications.advance()
    return [
        i
        for i, r in rows(app).items()
        if r["ready"] is not None and r["viewed"] is None
    ]


def test_quiet_extension_streams_then_validated_result(rig):
    app, now = rig
    app.sync.ingest(message(app, 1), live=True)
    now[0] += 20
    app.sync.ingest(message(app, 2), live=True)
    complete(app)
    now[0] += 29
    assert advance(app) == []
    with app.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='working',stage='rewriting',rendered='partial'"
        )
    now[0] += 2
    assert advance(app) == []
    complete(app)
    assert advance(app) == [1, 2]
    assert len(app.notifications.rows()) == 1


@pytest.mark.parametrize(
    "reason", ["off", "budget", "failed", "unknown", "nothing", "format", "original"]
)
def test_original_fallback_latches_once_and_keeps_early_view(rig, reason):
    app, now = rig
    for i in (1, 2):
        app.sync.ingest(
            message(
                app,
                i,
                text="" if reason == "nothing" else "Authored",
                media="document" if reason == "nothing" else None,
            ),
            live=True,
        )
    ack(app, see(app, 1))
    if reason == "off":

        def pause():
            raise ProcessingPaused("AI is Off")

        app.admit_ai = pause
    elif reason == "budget":
        app.notifications.unavailable = lambda: True
    elif reason == "format":
        app.rewriting.paused_roles = lambda: ["incoming_rewrite"]
    else:
        complete(app, state=reason)
    now[0] += 31
    assert advance(app) == [2]
    first = rows(app)[2]["ready"]
    complete(app)
    now[0] += 301
    assert advance(app) == [2]  # delivery expiry never expires unseen
    assert rows(app)[2]["ready"] == first
    ack(app, see(app, 2))
    assert advance(app) == []


@pytest.mark.parametrize("origin", ["muted", "catchup", "recovery", "no_alert"])
def test_alert_suppression_and_catchup_do_not_suppress_readiness(rig, origin):
    app, now = rig
    app.store.viewing.baseline("1/user:42", 0, 5)
    if origin == "muted":
        app.store.put_chat(
            app.store.chat("1/user:42").model_copy(update={"muted": True})
        )
    app.sync.ingest(
        message(app),
        live=origin == "muted",
        arrival=origin != "catchup",
        intake="initial" if origin == "catchup" else "arrival",
    )
    complete(app)
    now[0] += 31
    assert advance(app) == [1]
    assert app.notifications.rows() == []
    assert (
        not app.engine.calls
        and not app.transport.reads
        and not app.transport.deliveries
    )


def test_ready_survives_edits_retry_restart_but_new_appended_source_waits(
    rig, tmp_path
):
    app, now = rig
    app.sync.ingest(message(app))
    complete(app)
    now[0] += 31
    assert advance(app) == [1]
    app.sync.ingest(message(app, text="Edited"))
    app.sync.ingest(message(app, 2))
    assert advance(app) == [1]
    app = fixture(tmp_path, clock=lambda: now[0])
    assert advance(app) == [1]
    complete(app)
    now[0] += 31
    assert advance(app) == [1, 2]
    app.sync.deleted("user:42", [1])
    assert advance(app) == [2]


def test_two_blocks_wait_for_all_and_viewing_skipped_source_never_clears_it(rig):
    app, now = rig
    app.sync.ingest(message(app, 1))
    app.sync.ingest(message(app, 2, outgoing=True))
    app.sync.ingest(message(app, 3))
    with app.store.transaction() as db:
        db.execute(
            "UPDATE blocks SET state='ready',rendered='Done',rendered_revision=revision WHERE id='1/user:42/3'"
        )
    now[0] += 31
    assert advance(app) == []
    complete(app)
    assert advance(app) == [1, 3]
    ack(app, see(app, 3))
    assert advance(app) == [1]


def test_source_arriving_during_readiness_projection_cannot_be_latched(rig):
    app, now = rig
    app.sync.ingest(message(app))
    complete(app)
    now[0] += 31
    stages = app.stages

    def raced(key):
        result = stages(key)
        app.sync.ingest(message(app, 2))
        return result

    app.stages = raced
    app.unseen.advance()
    assert all(r["ready"] is None for r in rows(app).values())
