"""GB4 strict group contracts over authored identities and summaries only."""

import pytest
from enloq.domain.briefing_contract import (
    NO_SUBSTANCE,
    BriefingError,
    BriefingOutput,
    covered_source_ids,
    render_briefing,
    validate_briefing,
)
from enloq.inference.base import EngineResponse, InvalidOutput
from enloq.inference.openrouter import output_schema
from enloq.inference.work import validate_output

GROUP = "group_briefing"
TARGETS = {
    "messages": [{"id": 11, "text": "Alice confirmed Friday."}],
    "context": [{"id": 99, "text": "Earlier supporting discussion."}],
    "covered_ids": [11],
}


def briefing(citations=None, text="Alice confirmed Friday."):
    return {
        "outcome": "briefing",
        "topics": [
            {"text": text, "citations": [11] if citations is None else citations}
        ],
    }


def validated(output, *, role=GROUP, packet=None, finish="stop"):
    return validate_output(
        role, EngineResponse(output, {"finish_reason": finish}), packet or TARGETS
    )


def test_topics_render_separately_and_no_substance_has_exact_wording():
    output = briefing()
    output["topics"].append(
        {"text": "Bob's date remains uncertain.", "citations": [11]}
    )
    result = validated(output)
    assert isinstance(result, BriefingOutput)
    assert result.text == "Alice confirmed Friday.\n\nBob's date remains uncertain."
    assert render_briefing(result.model_dump()) == result.text
    empty = validated({"outcome": "no_substance", "topics": []})
    assert (
        empty.text == NO_SUBSTANCE == "No substantive updates in the covered messages."
    )


@pytest.mark.parametrize("role", ["group_briefing_chunk", GROUP])
@pytest.mark.parametrize(
    "citations", [[99], [12], [0], [-1], [True], ["11"], [11.0], [11, 11], []]
)
def test_every_topic_requires_unique_strict_target_citations(role, citations):
    with pytest.raises(InvalidOutput) as failed:
        validated(briefing(citations), role=role)
    assert str(failed.value) in {"briefing_schema", "briefing_citations"}
    assert "Alice" not in str(failed.value)


@pytest.mark.parametrize(
    "output,code",
    [
        ({"outcome": "briefing", "topics": []}, "briefing_outcome"),
        (
            {"outcome": "no_substance", "topics": briefing()["topics"]},
            "briefing_outcome",
        ),
        (briefing(text=" \t\n"), "briefing_text"),
        (briefing(text="\ud800"), "briefing_schema"),
        (briefing(text="x" * 8001), "briefing_schema"),
        ({"outcome": "briefing"}, "briefing_schema"),
        (
            {"outcome": "no_substance", "topics": [], "text": "hidden"},
            "briefing_schema",
        ),
        (
            {
                "outcome": "briefing",
                "topics": [{"text": "x", "citations": [11], "emotion": "sad"}],
            },
            "briefing_schema",
        ),
        ('{"outcome":', "invalid_json"),
        ('{"outcome":"no_substance","outcome":"briefing","topics":[]}', "invalid_json"),
        (
            '{"outcome":"briefing","topics":[{"text":"Bad\u0000","citations":[11]}]}',
            "invalid_json",
        ),
    ],
)
def test_blank_truncated_extra_and_duplicate_output_fails_with_content_free_codes(
    output, code
):
    with pytest.raises(InvalidOutput) as failed:
        validated(output)
    assert str(failed.value) == code


@pytest.mark.parametrize("finish", ["length", "content_filter", "tool_calls"])
def test_truncated_or_refused_shape_never_becomes_a_completed_briefing(finish):
    with pytest.raises(InvalidOutput, match="^incomplete_response$"):
        validated(briefing(), finish=finish)


def test_total_topic_text_retains_the_existing_response_bound():
    output = {
        "outcome": "briefing",
        "topics": [briefing(text="x" * 8000)["topics"][0]] * 9,
    }
    with pytest.raises(InvalidOutput, match="^briefing_text$"):
        validated(output)


def chunk(ids, citations):
    return {"covered_ids": ids, **briefing(citations, "Earlier covered topic.")}


def test_final_cites_originals_represented_by_validated_chunks_and_not_supporting_context():
    packet = {**TARGETS, "chunks": [chunk([1, 2], [1, 2])], "covered_ids": [1, 2, 11]}
    assert covered_source_ids(packet) == {1, 2, 11}
    assert validated(briefing([1, 11]), packet=packet).topics[0].citations == [1, 11]
    schema = output_schema(GROUP, packet)
    citations = schema["$defs"]["BriefingTopic"]["properties"]["citations"]
    assert citations["items"]["enum"] == [1, 2, 11]
    assert citations["minItems"] == 1 and citations["uniqueItems"] is True
    assert citations["maxItems"] == 3
    with pytest.raises(InvalidOutput, match="^briefing_citations$"):
        validated(briefing([99]), packet=packet)


@pytest.mark.parametrize("covered", [[11, 99], [11, 11], [True], [0], ["11"], [], None])
def test_packet_cannot_expand_coverage_or_coerce_source_identities(covered):
    with pytest.raises(BriefingError, match="^briefing_coverage$"):
        covered_source_ids({**TARGETS, "covered_ids": covered})


@pytest.mark.parametrize(
    "cached",
    [
        chunk([1, 2], [99]),
        chunk([1, 1], [1]),
        chunk([True], [1]),
        {
            "covered_ids": [1],
            "outcome": "no_substance",
            "topics": briefing([1])["topics"],
        },
    ],
)
def test_invalid_chunks_never_admit_their_references_into_a_final(cached):
    with pytest.raises(BriefingError):
        covered_source_ids({**TARGETS, "chunks": [cached], "covered_ids": [1, 2, 11]})


def test_storage_allowlist_validation_returns_only_content_free_errors():
    private_marker = "Authored sentinel that must not enter an error."
    with pytest.raises(BriefingError) as failed:
        validate_briefing(briefing([99], private_marker), {11})
    assert failed.value.code == str(failed.value) == "briefing_citations"
    assert private_marker not in str(failed.value)


IMAGES = {
    "messages": [{"id": 11, "text": ""}],
    "context": [{"id": 99, "text": "support"}],
    "images": [{"message_id": 11}, {"message_id": 99}],
    "covered_ids": [11],
}


def test_image_contract_uses_exact_attached_ids_including_labelled_supporting_images():
    output = {
        "image_context": [
            {"message_id": identity, "text": "Visible labelled chart."}
            for identity in (99, 11)
        ]
    }
    result = validated(output, role="group_briefing_image", packet=IMAGES)
    assert [image.message_id for image in result.image_context] == [99, 11]
    schema = output_schema("group_briefing_image", IMAGES)
    assert schema["properties"]["image_context"]["minItems"] == 2
    assert schema["properties"]["image_context"]["maxItems"] == 2
    assert schema["$defs"]["BriefingImageContext"]["properties"]["message_id"][
        "enum"
    ] == [11, 99]


@pytest.mark.parametrize(
    "images",
    [
        [],
        [{"message_id": 11, "text": "x"}],
        [{"message_id": 11, "text": "x"}] * 2,
        [{"message_id": 11, "text": "x"}, {"message_id": 12, "text": "x"}],
        [{"message_id": True, "text": "x"}, {"message_id": 99, "text": "x"}],
        [{"message_id": 11, "text": " "}, {"message_id": 99, "text": "x"}],
        [{"message_id": 11, "text": "\udfff"}, {"message_id": 99, "text": "x"}],
    ],
)
def test_missing_duplicate_unknown_boolean_and_blank_image_meanings_fail(images):
    with pytest.raises(InvalidOutput):
        validated({"image_context": images}, role="group_briefing_image", packet=IMAGES)


def test_image_only_schema_is_separate_from_topics_and_private_output():
    with pytest.raises(InvalidOutput, match="^briefing_schema$"):
        validated(
            {"image_context": [], "text": "Unsolicited description."},
            role="group_briefing_image",
            packet={"images": []},
        )
    assert (
        validated(
            {"image_context": []}, role="group_briefing_image", packet={"images": []}
        ).image_context
        == []
    )


def test_image_schema_cannot_weaken_the_existing_four_image_request_bound():
    with pytest.raises(BriefingError, match="^briefing_images$"):
        output_schema(
            "group_briefing_image",
            {"images": [{"message_id": identity} for identity in range(1, 6)]},
        )
