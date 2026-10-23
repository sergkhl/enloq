"""P5: durable unseen-reaction dots, readiness, opening and focused viewing
(ADR 26). Authored reactions; no push, sound, Telegram read or model call
belongs to a reaction dot."""

import pytest
from enloq.domain.emotion import LIFETIME
from enloq.domain.models import Conflict
from enloq.storage.store import RETENTION
from enloq.storage.viewing import ViewedContent, ViewedSources

from backend.tests.test_reaction_assessment import calls, conversation, rig, settle
from backend.tests.test_reactions import snapshot
from backend.tests.test_rebuild import message

PEER = "user:42"
KEY = "1/user:42"


def row(app):
    [value] = [r for r in app.previews.recent() if r.key == KEY]
    return value


def ready(app):
    return [
        c
        for c in app.store.viewing.ready_sources(KEY)["content"]
        if c["kind"] == "reaction"
    ]


def view_all(app):
    content = app.store.viewing.ready_sources(KEY)["content"]
    return app.store.viewing.acknowledge(
        KEY, ViewedSources(content=[ViewedContent(**c) for c in content])
    )


async def viewed_conversation(app, now):
    """The conversation with every incoming source already viewed locally."""
    await conversation(app, now)
    app.unseen.advance()
    content = app.store.viewing.ready_sources(KEY)["content"]
    if content:
        view_all(app)
    assert not row(app).unseen


async def test_a_new_reaction_dot_follows_quiet_assessment_and_opens_at_its_target(
    tmp_path,
):
    app, now = rig(
        tmp_path, assessment={"emotion": "positive", "suggestions_useful": True}
    )
    await viewed_conversation(app, now)
    notifications = app.notifications.rows()
    # The owner's outgoing message 2 gets the correspondent's reaction.
    app.sync.reactions(
        PEER, 2, snapshot(peer=[("👍", now[0] + 1)], observed=now[0] + 1)
    )
    app.unseen.advance()
    assert not ready(app) and not row(app).unseen
    # Quiet passes and the assessment settles; suggestions are not awaited.
    now[0] += 31
    app.unseen.advance()
    assert not ready(app)  # The assessment is still queued.
    await app.process_due()
    assert app.store.rows(
        "SELECT 1 FROM jobs WHERE role='response_suggestions' AND state='queued'"
    )
    app.unseen.advance()
    assert len(ready(app)) == 1
    value = row(app)
    assert value.unseen and value.opening_source == 2
    assert app.previews.opening(KEY) == 2
    # Opening goes to the reaction target; reactions place no separator.
    assert app.conversation(KEY)["unread_sources"] == []
    # No reaction-only push or sound.
    app.notifications.advance()
    assert app.notifications.rows() == notifications
    # One focused acknowledgement clears it for every trusted browser.
    assert view_all(app)["viewed"] == 1
    assert not row(app).unseen and not ready(app)
    # Repeated observations never recreate it.
    app.sync.reactions(PEER, 2, snapshot(peer=[("👍", now[0] - 30)], observed=now[0]))
    now[0] += 60
    app.unseen.advance()
    assert not row(app).unseen


async def test_off_budget_ineligible_and_failed_assessments_fall_back_to_the_raw_dot(
    tmp_path,
):
    from enloq.domain.models import ProcessingPaused

    app, now = rig(tmp_path)
    await viewed_conversation(app, now)

    def off():
        raise ProcessingPaused("AI processing is Off for this account")

    app.admit_ai = off
    app.notifications.unavailable = lambda: True
    app.sync.reactions(
        PEER, 1, snapshot(peer=[("👎", now[0] + 1)], observed=now[0] + 1)
    )
    now[0] += 31
    await app.process_due()
    assert app.assessment.pending(KEY) and not calls(app)
    app.unseen.advance()
    assert len(ready(app)) == 1 and row(app).opening_source == 1


async def test_baseline_own_other_and_removed_reactions_never_show_a_dot(tmp_path):
    app, now = rig(tmp_path)
    await viewed_conversation(app, now)
    start = now[0]
    app.sync.reactions(PEER, 1, snapshot(own=["👍"], observed=start + 1))
    app.sync.reactions(PEER, 2, snapshot(peer=[("❤️", start + 1)], observed=start + 1))
    app.sync.ingest(message(app, 3, text="Before", date=start - 600))
    app.sync.reactions(
        PEER, 3, snapshot(peer=[("👍", app.store.clock() - 10**6)], observed=start + 1)
    )
    # Added and withdrawn within the quiet window: no dot.
    app.sync.ingest(message(app, 4, text="Later", date=start + 2), target=False)
    app.sync.reactions(PEER, 4, snapshot(peer=[("👌", start + 3)], observed=start + 3))
    app.sync.reactions(PEER, 4, snapshot(observed=start + 4))
    await settle(app, now, 120)
    app.unseen.advance()
    assert not ready(app)


async def test_a_dot_outlives_ai_expiry_until_removal_deletion_or_cache_expiry(
    tmp_path,
):
    app, now = rig(
        tmp_path, assessment={"emotion": "positive", "suggestions_useful": False}
    )
    await viewed_conversation(app, now)
    added = now[0] + 1
    app.sync.reactions(PEER, 2, snapshot(peer=[("🤝", added)], observed=added))
    await settle(app, now, 35)
    app.unseen.advance()
    assert len(ready(app)) == 1
    now[0] = added + LIFETIME + 60
    assert app.emotion.view(KEY).reason == "expired"
    assert row(app).unseen and len(ready(app)) == 1
    # Withdrawing the reaction withdraws only its contribution.
    app.sync.reactions(PEER, 2, snapshot(observed=now[0]))
    assert not ready(app) and not row(app).unseen
    # A re-addition later is a new event with its own dot.
    again = now[0] + 1
    app.sync.reactions(PEER, 2, snapshot(peer=[("🤝", again)], observed=again))
    await settle(app, now, 35)
    app.unseen.advance()
    assert len(ready(app)) == 1
    # Deleting the target withdraws it; cache expiry removes it.
    app.sync.deleted(PEER, [2])
    assert not ready(app) and not row(app).unseen
    now[0] += RETENTION
    app.store.expire()
    assert not app.store.rows("SELECT 1 FROM reaction_events")


async def test_an_earlier_view_of_the_target_never_acknowledges_a_later_reaction(
    tmp_path,
):
    app, now = rig(tmp_path)
    await viewed_conversation(app, now)
    target = app.store.viewing.original(app.store.message(KEY, 1))
    app.sync.reactions(
        PEER, 1, snapshot(peer=[("👍", now[0] + 1)], observed=now[0] + 1)
    )
    await settle(app, now, 35)
    app.unseen.advance()
    assert len(ready(app)) == 1
    app.store.viewing.acknowledge(KEY, ViewedSources(content=[target]))
    assert len(ready(app)) == 1 and row(app).unseen
    # Stale or foreign tokens are refused without clearing anything.
    [token] = ready(app)
    forged = ViewedContent(**{**token, "token": "0" * 64})
    with pytest.raises(Conflict):
        app.store.viewing.acknowledge(KEY, ViewedSources(content=[forged]))
    other = ViewedContent(**{**token, "identity": "1/user:42/9/👍/1.0"})
    with pytest.raises(Conflict):
        app.store.viewing.acknowledge(KEY, ViewedSources(content=[other]))
    assert len(ready(app)) == 1


async def test_opening_takes_the_earliest_ready_unseen_position(tmp_path):
    app, now = rig(tmp_path)
    await viewed_conversation(app, now)
    # A new incoming message (3) and a reaction on the earlier message (1).
    app.sync.ingest(message(app, 3, text="New request", date=now[0] + 1), live=True)
    app.sync.reactions(
        PEER, 1, snapshot(peer=[("👌", now[0] + 2)], observed=now[0] + 2)
    )
    await settle(app, now, 35)
    app.unseen.advance()
    assert app.previews.opening(KEY) == 1
    # Viewing everything ready clears the dot in one acknowledgement.
    assert view_all(app)["viewed"] == 2
    assert not row(app).unseen and app.previews.opening(KEY) is None
