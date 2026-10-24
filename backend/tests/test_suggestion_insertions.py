"""Authored full-draft outputs become additions without changing owner text."""

import json

import pytest
from enloq.application.suggestions import splice
from enloq.inference.base import EngineResponse, InvalidOutput
from enloq.inference.work import validate_output

from backend.tests.test_manual_suggestions import finish
from backend.tests.test_suggestion_generation import rig


def read(text, packet):
    return (
        validate_output(
            "response_suggestions",
            EngineResponse(
                json.dumps({"options": [{"purpose": "clarify", "text": text}]})
            ),
            packet,
        )
        .options[0]
        .text
    )


@pytest.mark.parametrize(
    "draft,cursor,completed,addition,inserted",
    [
        (
            "Monday does not work. ",
            None,
            "Monday does not work. Would Tuesday suit you?",
            "Would Tuesday suit you?",
            "Monday does not work. Would Tuesday suit you?",
        ),
        (
            "Monday does not work",
            999,
            "Monday does not work. Would Tuesday suit you?",
            ". Would Tuesday suit you?",
            "Monday does not work. Would Tuesday suit you?",
        ),
        (
            "Please at noon.",
            7,
            "Please send the file at noon.",
            "send the file",
            "Please send the file at noon.",
        ),
        (
            "at noon.",
            0,
            "Please send the file at noon.",
            "Please send the file",
            "Please send the file at noon.",
        ),
        (
            "😀 at noon.",
            3,
            "😀 Please send the file at noon.",
            "Please send the file",
            "😀 Please send the file at noon.",
        ),
        (
            "😀",
            1,
            "😀 Could you explain?",
            "Could you explain?",
            "😀 Could you explain?",
        ),
        (
            "Use code A—B. ",
            None,
            "Use code A—B. Is A—B still valid?",
            "Is A—B still valid?",
            "Use code A—B. Is A—B still valid?",
        ),
    ],
)
def test_exact_completed_draft_exposes_only_its_new_insertion(
    draft, cursor, completed, addition, inserted
):
    option = read(completed, {"manual": True, "draft": draft, "cursor": cursor})
    assert option == addition
    assert splice(draft, option, cursor)[0] == inserted


@pytest.mark.parametrize(
    "draft,cursor,fragment",
    [
        ("Monday does not work. ", None, "Would Tuesday suit you?"),
        ("Please at noon.", 7, "send the file"),
        ("I", None, "In addition, could you explain?"),
        ("Please at noon.", 7, "Please check tomorrow."),
    ],
)
def test_fragments_and_changed_wrappers_are_never_treated_as_draft_replacements(
    draft, cursor, fragment
):
    assert (
        read(fragment, {"manual": True, "draft": draft, "cursor": cursor}) == fragment
    )


@pytest.mark.parametrize("cursor", [None, 0, 7])
def test_draft_echo_with_no_addition_fails_once_with_a_content_free_code(cursor):
    with pytest.raises(InvalidOutput, match="^invalid_insertion$"):
        read(
            "Please at noon.",
            {"manual": True, "draft": "Please at noon. ", "cursor": cursor},
        )


@pytest.mark.parametrize(
    "packet",
    [{}, {"draft": "Hello."}, {"manual": True, "draft": " \n"}],
)
def test_automatic_and_blank_draft_outputs_remain_complete_messages(packet):
    assert read("Hello. Could you explain?", packet) == "Hello. Could you explain?"


async def test_full_draft_output_is_labeled_and_inserted_once_with_authored_undo(
    tmp_path,
):
    app, _ = rig(tmp_path)
    app.suggestions.set_settings("1/user:42", False, "professional")
    app.engine.options = {
        "options": [
            {
                "purpose": "clarify",
                "text": "Monday does not work. Would Tuesday suit you?",
            }
        ]
    }
    app.store.mutate_draft(
        "1/user:42", 0, lambda draft: draft.update(text="Monday does not work. ")
    )
    before = app.store.draft("1/user:42")
    app.suggestions.generate("1/user:42", before["revision"])
    await finish(app)
    shown = app.suggestions.view("1/user:42")
    assert shown.state == "ready"
    assert [option.text for option in shown.options] == ["Would Tuesday suit you?"]
    assert app.store.draft("1/user:42") == before
    inserted = app.suggestions.insert("1/user:42", before["revision"], shown.attempt, 0)
    assert inserted["draft"]["text"] == "Monday does not work. Would Tuesday suit you?"
    assert inserted["draft"]["undo"] == before["text"]
    assert not app.store.rows("SELECT id FROM operations")


async def test_echo_only_output_fails_without_another_call_or_a_draft_change(tmp_path):
    app, _ = rig(tmp_path)
    app.engine.options = {
        "options": [{"purpose": "clarify", "text": "Keep this authored intent."}]
    }
    app.store.mutate_draft(
        "1/user:42", 0, lambda draft: draft.update(text="Keep this authored intent.")
    )
    before = app.store.draft("1/user:42")
    app.suggestions.generate("1/user:42", before["revision"])
    await finish(app)
    view = app.suggestions.view("1/user:42")
    assert view.state == "failed" and view.reason == "format"
    assert app.engine.count() == 1
    assert not app.decider.calls
    assert app.store.draft("1/user:42") == before
