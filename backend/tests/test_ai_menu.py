"""ADR 62: a chat's AI Menu draws from its conversation snapshot, whose `ai`
object holds the chat's switches and Style, the owner's server-wide switches,
the account's AI and whether AI work can run there (Q14)."""

from enloq.storage.store import WINDOW

from backend.tests.test_rebuild import fixture
from backend.tests.test_retained_groups import group

KEY = "1/user:42"


def test_a_private_chat_carries_its_switches_and_style(tmp_path):
    app = fixture(tmp_path)
    # The switches as the cases before ADR 62 had them (conftest's
    # switches_on); test_ai_switches checks the shipped Off.
    assert app.conversation(KEY)["ai"] == {
        "account": True,
        "running": True,
        "suggestions": True,
        "style": "friendly",
        "emotion": True,
        "calming": True,
        "media": True,
        "remember": False,
        "server_suggestions": True,
        "server_memory": True,
    }
    app.suggestions.set_settings(KEY, False, "idioms")
    app.calming.set(KEY, False)
    app.memory.remember(KEY, True)
    ai = app.conversation(KEY)["ai"]
    assert (ai["suggestions"], ai["style"]) == (False, "idioms")
    assert ai["calming"] is False and ai["remember"] is True
    # No top-level copy of a switch remains beside it.
    assert "calming" not in app.conversation(KEY)


def test_the_server_wide_switches_and_the_account_show_over_the_chat(tmp_path):
    app = fixture(tmp_path)
    app.suggestions.enabled = lambda: False
    app.memory.enabled = lambda: False
    ai = app.conversation(KEY)["ai"]
    # The chat's own choices stay as they are under an Off server switch.
    assert ai["suggestions"] is True and ai["server_suggestions"] is False
    assert ai["server_memory"] is False and ai["running"] is True
    app.account_processing_enabled = lambda: False
    ai = app.conversation(KEY)["ai"]
    assert ai["account"] is False and ai["running"] is False


def test_a_group_runs_while_its_processing_does_and_a_retained_one_never(
    tmp_path,
):
    now = [2_000_000_000.0]
    app = fixture(tmp_path, clock=lambda: now[0])
    key = group(app)
    assert app.conversation(key)["ai"] == {
        "account": True,
        "running": False,
        "suggestions": None,
        "style": None,
        "emotion": None,
        "calming": None,
        "media": None,
        "remember": None,
        "server_suggestions": None,
        "server_memory": None,
    }
    app.set_group_processing(key, True)
    assert app.conversation(key)["ai"]["running"] is True
    app.account_processing_enabled = lambda: False
    assert app.conversation(key)["ai"] == {
        **app.conversation(key)["ai"],
        "account": False,
        "running": False,
    }
    app.account_processing_enabled = lambda: True
    # A saved draft retains the group once it leaves the recent window.
    app.store.mutate_draft(key, 0, lambda draft: draft.update(text="Kept draft"))
    now[0] += WINDOW + 1
    snapshot = app.conversation(key)
    assert snapshot["retained"] is True and snapshot["ai"]["running"] is False


def test_saved_messages_has_no_ai_menu(tmp_path):
    app = fixture(tmp_path)
    assert app.conversation(app.store.saved_key)["ai"] is None
