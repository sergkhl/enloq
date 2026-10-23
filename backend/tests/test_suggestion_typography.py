"""ADR 25: authored new-output typography, exact literals and cache boundaries.

Only fake engines/local stores. No provider or Telegram activity is required.
"""

import asyncio
import json

import pytest
from enloq.domain.suggestions import ResponseSuggestions, SuggestionOption
from enloq.inference.base import EngineResponse, InvalidOutput
from enloq.inference.suggestion_typography import normalize_suggestions
from enloq.inference.work import validate_output

from backend.tests.test_reaction_assessment import (
    conversation,
    settle,
)
from backend.tests.test_reaction_assessment import (
    rig as reaction_rig,
)
from backend.tests.test_reactions import snapshot
from backend.tests.test_rebuild import message
from backend.tests.test_suggestion_generation import (
    KEY,
    drain,
    incoming_only,
    rig,
)
from backend.tests.test_suggestion_state import prepare, publish


def body(*texts):
    return {"options": [{"purpose": "clarify", "text": text} for text in texts]}


def normalized(text, packet=None, *, serialized=False):
    content = body(text)
    if serialized:
        content = json.dumps(content, ensure_ascii=False)
    return (
        validate_output("response_suggestions", EngineResponse(content), packet or {})
        .options[0]
        .text
    )


@pytest.mark.parametrize("serialized", [False, True])
def test_only_approved_prose_characters_map_after_json_parsing(serialized):
    raw = ' I hear you — “later” / «tomorrow» – ‘yes’ and let’s. - "okay"\n'
    expected = ' I hear you - "later" / "tomorrow" - \'yes\' and let\'s. - "okay"\n'
    assert normalized(raw, serialized=serialized) == expected
    assert len(raw) == len(expected)
    # No compatibility normalization or blanket punctuation conversion.
    uncommon = "„low‟ ‹single› non‐ASCII‑hyphens −4 … / Ａ e\u0301"
    assert normalized(uncommon) == uncommon


@pytest.mark.parametrize(
    "literal",
    [
        "https://example.test/«part»—“id”",
        "www.example.test/a–b",
        "/tmp/«report»—“draft”.txt",
        "./reports/a—‘b’.txt",
        r"C:\reports\«draft»—‘id’.txt",
        "reports/a—‘b’.txt",
        "10–12",
        "10 — 12",
        "2026–10–10",
        "1/2",
        "–3",
        "– 5",
        "—0.25",
        "Jean–Luc",
        "alpha—beta",
        "`unknown “exact” — value`",
        "O’Neil",
    ],
)
def test_literal_syntax_is_preserved_without_guessing_a_new_value(literal):
    assert normalized(f"Check {literal} — is that right?") == (
        f"Check {literal} - is that right?"
    )


@pytest.mark.parametrize("ending", ["", ",", "?", ")."])
def test_address_quote_framing_maps_without_changing_internal_characters(ending):
    raw = f"«https://example.test/«part»—“id”»{ending} — okay?"
    assert normalized(raw) == f'"https://example.test/«part»—“id”"{ending} - okay?'


@pytest.mark.parametrize(
    "source",
    [
        {"text": "The identifier is abc“def”."},
        {"transcript": {"text": "The identifier is abc“def”."}},
        {"image_context": "The card shows abc“def”."},
        {"forwarded_from": "abc“def”"},
        {"forward_signature": "abc“def”"},
        {"links": [{"url": "abc“def”"}]},
        {"placeholder": {"file_name": "abc“def”"}},
    ],
)
@pytest.mark.parametrize("field", ["messages", "context"])
def test_copied_source_values_keep_exact_spelling_in_each_input(field, source):
    packet = {field: [source]}
    assert normalized("Can we check abc“def” — “later”?", packet) == (
        'Can we check abc“def” - "later"?'
    )


@pytest.mark.parametrize(
    "source,literal",
    [
        ("Name: Blue — Finch", "Blue — Finch"),
        ("Try Studio «North Wind».", "Studio «North Wind»"),
        ("The code A — B matters.", "A — B"),
        ("The key is q‘x‘q.", "q‘x‘q"),
        ("The title is «Blue — Finch».", "Blue — Finch"),
        ("The name is AB«CD».", "AB«CD»"),
        ("Please use code abc — def.", "abc — def"),
        ("Name is foo — bar.", "foo — bar"),
        ("The name is O’Malley’s.", "O’Malley’s"),
        ("Мой код аб — вг.", "аб — вг"),
        ("Название файла ро — ва.", "ро — ва"),
        ("The code is abc — def — ghi.", "abc — def — ghi"),
        ("Name is A — B — C.", "A — B — C"),
        ("Мой код аб — вг — де.", "аб — вг — де"),
        ("It is /tmp/my — folder/file.txt.", "/tmp/my — folder/file.txt"),
        ("The company name is O’s.", "O’s"),
        ("The code is don’t.", "don’t"),
        ("Please use code abc — def for launch.", "abc — def"),
        ("Please use /tmp/my — folder.", "/tmp/my — folder"),
        ("Copy /tmp/my — folder for launch.", "/tmp/my — folder"),
        ("Скопируй код аб — вг для запуска.", "аб — вг"),
        ("Открой /tmp/моя — папка для запуска.", "/tmp/моя — папка"),
    ],
)
def test_ambiguous_copied_values_and_meaningful_name_punctuation_survive(
    source, literal
):
    assert normalized(
        f"What about {literal} — is it right?", {"messages": [{"text": source}]}
    ) == (f"What about {literal} - is it right?")


def test_metadata_can_supply_a_multiword_exact_value_and_guidance_cannot():
    raw = "Try Blue — Finch — “later”?"
    packet = {
        "messages": [{"placeholder": {"file_name": "Blue — Finch"}}],
        "guidance": {"fields": {"suggestions": raw}},
        "memory": [{"text": raw}],
    }
    assert normalized(raw, packet) == 'Try Blue — Finch - "later"?'
    assert normalized(
        raw, {"guidance": packet["guidance"], "memory": packet["memory"]}
    ) == ('Try Blue - Finch - "later"?')


def test_generated_contractions_map_while_exact_names_remain():
    packet = {"messages": [{"text": "I don’t know O’Neil."}]}
    assert normalized("I’ll ask O’Neil — don’t worry.", packet) == (
        "I'll ask O’Neil - don't worry."
    )
    packet = {"messages": [{"text": "Value: don’t"}]}
    assert normalized("Use don’t — “okay”?", packet) == 'Use don’t - "okay"?'


def test_copying_ordinary_prose_does_not_exempt_sentence_typography():
    raw = "I hear you — maybe ‘later’ works."
    assert normalized(raw, {"messages": [{"text": raw}]}) == (
        "I hear you - maybe 'later' works."
    )
    raw = "Your code can wait — I don’t know."
    assert normalized(raw, {"messages": [{"text": raw}]}) == (
        "Your code can wait - I don't know."
    )
    assert (
        normalized(
            "The code failed — it sounds frustrating.",
            {"messages": [{"text": "My code failed — it is frustrating."}]},
        )
        == "The code failed - it sounds frustrating."
    )
    raw = "Visit https://example.test/id — shall we talk?"
    assert normalized(raw, {"messages": [{"text": raw}]}) == (
        "Visit https://example.test/id - shall we talk?"
    )


def test_transform_copies_options_without_mutating_provider_packet_or_payload():
    content = body("‘yes’ — perhaps?", "‘no’ — perhaps?")
    packet = {"messages": [{"text": "A harmless authored source."}]}
    before = json.dumps([content, packet], ensure_ascii=False)
    result = normalize_suggestions(content, packet)
    assert result == body("'yes' - perhaps?", "'no' - perhaps?")
    assert json.dumps([content, packet], ensure_ascii=False) == before


def test_large_bounded_source_keeps_prose_mapping_and_does_not_supply_a_literal():
    packet = {"messages": [{"text": "x" * 24000}]}
    assert normalized("I hear you — ‘later’ works.", packet) == (
        "I hear you - 'later' works."
    )


def test_final_contract_rejects_normalization_collisions():
    for content in (body("‘yes’", "'yes'"), body("Okay — thanks.", "Okay - thanks.")):
        with pytest.raises(InvalidOutput, match="schema"):
            validate_output("response_suggestions", EngineResponse(content), {})


@pytest.mark.parametrize(
    "content",
    [
        body(" "),
        body("x" * 280 + "—"),
        body("1", "2", "3", "4"),
        {"options": [{"purpose": "advice", "text": "‘hello’"}]},
        {"options": [{"purpose": "clarify", "text": ["‘hello’"]}]},
        {"options": body("‘hello’")["options"], "extra": True},
        body("\ud800"),
    ],
)
def test_normalizer_does_not_repair_an_invalid_output_contract(content):
    with pytest.raises(InvalidOutput, match="schema"):
        validate_output("response_suggestions", EngineResponse(content), {})


def test_280_character_bound_empty_result_and_original_parse_failures_remain():
    raw = "x" * 277 + " — "
    assert len(raw) == 280 and normalized(raw) == "x" * 277 + " - "
    assert (
        validate_output("response_suggestions", EngineResponse(body()), {}).options
        == []
    )
    for content in ('{"options":', '{"options": [], "options": []}'):
        with pytest.raises(InvalidOutput, match="invalid_json"):
            validate_output("response_suggestions", EngineResponse(content), {})
    with pytest.raises(InvalidOutput, match="incomplete_response"):
        validate_output(
            "response_suggestions",
            EngineResponse(body("‘yes’"), usage={"finish_reason": "length"}),
            {},
        )


@pytest.mark.parametrize(
    "source,raw,expected",
    [
        (
            "I wrote ab – cd on the envelope.",
            "Could you resend code “ab – cd”?",
            'Could you resend code "ab – cd"?',
        ),
        (
            "I wrote A ’ B.",
            "Could you resend “A ’ B”?",
            'Could you resend "A ’ B"?',
        ),
    ],
)
def test_output_quoted_copies_protect_uncertain_unquoted_source_values(
    source, raw, expected
):
    assert normalized(raw, {"messages": [{"text": source}]}) == expected


async def test_incoming_generated_stored_displayed_and_inserted_text_is_identical(
    tmp_path,
):
    app, _ = rig(tmp_path)
    raw = "I hear you — shall we call “later”? Let’s choose either/or."
    expected = 'I hear you - shall we call "later"? Let\'s choose either/or.'
    app.engine.options = body(raw)
    app.sync.ingest(message(app, text="Today was tiring."))
    app.store.mutate_draft(KEY, 0, lambda draft: draft.update(text="Authored ‘draft’."))
    await drain(app)
    view = app.suggestions.view(KEY)
    assert view.state == "ready" and view.options[0].text == expected
    assert app.suggestions.record(KEY)["options"][0]["text"] == expected
    assert app.conversation(KEY)["suggestions"]["options"][0]["text"] == expected
    draft = app.store.draft(KEY)
    assert draft["text"] == "Authored ‘draft’."
    inserted = app.suggestions.insert(KEY, draft["revision"], view.attempt, 0, 0)[
        "draft"
    ]
    assert inserted["text"] == expected + " Authored ‘draft’."
    assert inserted["undo"] == "Authored ‘draft’."
    assert app.engine.count() == 1


async def test_reaction_derived_generation_uses_the_same_normalization_boundary(
    tmp_path,
):
    app, now = reaction_rig(
        tmp_path, assessment={"emotion": "worried", "suggestions_useful": True}
    )
    await conversation(app, now)
    original = app.engine.generate

    async def authored(role, packet):
        if role == "response_suggestions":
            app.engine.calls.append((role, packet))
            return body("I hear you — what about ‘tomorrow’?")
        return await original(role, packet)

    app.engine.generate = authored
    app.sync.reactions(
        "user:42", 2, snapshot(peer=[("👎", now[0] + 1)], observed=now[0] + 1)
    )
    await settle(app, now, 35)
    record = app.suggestions.record(KEY)
    assert record["kind"] == "reaction"
    assert record["options"][0]["text"] == "I hear you - what about 'tomorrow'?"
    assert app.suggestions.view(KEY).options[0].text == record["options"][0]["text"]


async def test_collision_fails_once_without_filtering_retry_or_role_pause(tmp_path):
    app, registry = rig(tmp_path)
    app.engine.options = body("‘yes’", "'yes'")
    app.sync.ingest(message(app, text="An authored difficult day."))
    await drain(app)
    view = app.suggestions.view(KEY)
    assert (view.state, view.reason, view.options) == ("failed", "format", [])
    assert app.engine.count() == 1 and registry.paused_roles() == set()
    assert app.conversation(KEY)["blocks"][0]["state"] == "ready"
    restarted, _ = rig(tmp_path)
    restarted.store.recover()
    await drain(restarted)
    assert restarted.engine.count() == 0
    assert restarted.suggestions.view(KEY).reason == "format"


@pytest.mark.usefixtures("calming_off")  # ADR 30's order, calming off
async def test_normalized_late_output_cannot_revive_an_answered_source(tmp_path):
    app, _ = rig(tmp_path)
    app.engine.options = body("I hear you — ‘yes’?")
    app.sync.ingest(message(app, text="An authored tiring day."))
    await incoming_only(app)
    app.engine.gate = asyncio.Event()
    task = asyncio.create_task(drain(app))
    await asyncio.wait_for(app.engine.started.wait(), 5)
    app.sync.ingest(message(app, 2, outgoing=True, text="Authored answer."))
    app.engine.gate.set()
    await task
    assert app.suggestions.view(KEY).reason == "answered"
    assert app.suggestions.record(KEY).get("options") is None


async def test_cached_typography_reads_restart_and_insertion_are_unchanged(tmp_path):
    app, _ = rig(tmp_path)
    app.sync.ingest(message(app, text="An authored source."))
    record = await prepare(app)
    raw = "I hear you — ‘yes’ or ‘no’?"
    cached = [SuggestionOption(purpose="clarify", text=raw)]
    assert publish(app, record["attempt"], options=cached)
    assert ResponseSuggestions.model_validate(body(raw)).options[0].text == raw
    app.store.mutate_draft(
        KEY, 0, lambda draft: draft.update(text="Keep ‘my’ — draft.")
    )
    for _ in range(3):
        assert app.suggestions.view(KEY).options[0].text == raw
        assert app.conversation(KEY)["suggestions"]["options"][0]["text"] == raw
    restarted, _ = rig(tmp_path)
    restarted.store.recover()
    await drain(restarted)
    view = restarted.suggestions.view(KEY)
    assert view.options[0].text == raw and restarted.engine.count() == 0
    draft = restarted.store.draft(KEY)
    assert draft["text"] == "Keep ‘my’ — draft."
    inserted = restarted.suggestions.insert(KEY, draft["revision"], view.attempt, 0)[
        "draft"
    ]
    assert inserted["text"] == "Keep ‘my’ — draft. " + raw
    assert inserted["undo"] == "Keep ‘my’ — draft."
