"""ADR 56 Q9-Q11: Remember this chat Off by default, both ways, and a chat
Off only by default learning from its 30-day cache when switched on."""

from enloq.storage.store import RETENTION

from backend.tests.test_account_memory import fold_now
from backend.tests.test_memory import DAY, KEY, conversation
from backend.tests.test_rebuild import fixture, message


async def test_a_chat_without_its_own_choice_neither_learns_nor_reads(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now, start=True)
    assert app.memory.remember_view(KEY) == {"enabled": False}
    # Sent to a model, nothing is admitted; the store's start skips it too.
    app.memory.record(KEY, [f"{KEY}/1", f"{KEY}/2"])
    now[0] += 1800
    assert not app.memory.schedule(KEY)
    assert not app.memory.candidates(KEY)
    assert app.memory.for_suggestions(KEY) is None
    assert app.memory.for_target(KEY, 500, now[0]) is None
    assert not app.traits.usable(KEY)


async def test_what_memory_holds_stays_while_off_by_default(tmp_path, monkeypatch):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    # Learned while Remember this chat was On by default, before ADR 56.
    monkeypatch.setattr(type(app.memory), "default", True)
    app.memory.record(KEY, [f"{KEY}/1", f"{KEY}/2"])
    await fold_now(app, now)
    held = app.memory.view(KEY)["entries"]
    assert held
    monkeypatch.setattr(type(app.memory), "default", False)
    assert app.memory.view(KEY)["entries"] == held
    assert app.memory.for_suggestions(KEY) is None


async def test_switching_on_a_chat_off_by_default_learns_its_cached_month(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    app.sync.ingest(
        message(app, 50, date=now[0] - RETENTION - DAY, text="Older than a month."),
        target=False,
    )
    app.sync.ingest(message(app, 51, text="Arrived while Off by default."))
    app.memory.record(KEY, [f"{KEY}/51"])
    assert not app.memory.candidates(KEY)
    assert app.memory.remember(KEY, True) == {"enabled": True}
    learned = {r["message_id"] for r in app.memory.candidates(KEY)}
    assert learned == {1, 2, 3, 4, 51, 100}
    assert {r["origin"] for r in app.memory.candidates(KEY)} == {"start"}
    # They fold as any start does.
    await fold_now(app, now)


async def test_a_chat_switched_off_by_its_holder_never_learns_its_off_time(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    app.memory.remember(KEY, True)
    await fold_now(app, now)
    app.memory.remember(KEY, False)
    app.sync.ingest(message(app, 60, text="Said while switched Off."))
    app.memory.record(KEY, [f"{KEY}/60"])
    app.memory.remember(KEY, True)
    assert not app.memory.candidates(KEY)


async def test_switching_on_while_memory_is_off_starts_nothing(tmp_path):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    conversation(app, now)
    app.memory.enabled = lambda: False
    app.memory.remember(KEY, True)
    assert not app.memory.candidates(KEY)
