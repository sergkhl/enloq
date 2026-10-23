"""Structured incoming rewrites as ordered parts rendered into plain text."""

import json
import time

import httpx
import pytest
from enloq.config import Settings, private_write
from enloq.inference.base import EngineResponse
from enloq.inference.openrouter import MODEL, OpenRouterEngine, output_schema
from enloq.inference.streaming import PartsPreview, render
from enloq.inference.work import InvalidOutput, validate_output

from backend.tests.proxy_helpers import ENDPOINT

SOURCES = {"messages": [{"text": "Authored source."}]}


def part(kind, text):
    return {"kind": kind, "text": text}


@pytest.mark.parametrize(
    "parts,expected",
    [
        ([part("paragraph", "Running late, sorry!")], "Running late, sorry!"),
        (
            [part("paragraph", "Heater on Monday."), part("paragraph", "Package?")],
            "Heater on Monday.\n\nPackage?",
        ),
        (
            [
                part("paragraph", "Can you check:"),
                part("bullet", "towels"),
                part("bullet", "dogs"),
                part("bullet", "parking"),
            ],
            "Can you check:\n• towels\n• dogs\n• parking",
        ),
        (
            [
                part("numbered", "Book"),
                part("numbered", "Pay"),
                part("paragraph", "Then tell me."),
                part("numbered", "Again"),
            ],
            "1. Book\n2. Pay\n\nThen tell me.\n1. Again",
        ),
        (
            [part("bullet", "• already marked"), part("bullet", "2) numbered")],
            "• already marked\n• numbered",
        ),
        (
            [part("paragraph", "1990 was a year."), part("bullet", "x")],
            "1990 was a year.\n• x",
        ),
        (
            [part("bullet", "a"), part("numbered", "b"), part("paragraph", "c")],
            "• a\n\n1. b\n\nc",
        ),
        ([part("paragraph", "  "), part("unknown", "x"), part("bullet", "y")], "• y"),
    ],
    ids=[
        "single",
        "topics",
        "lead-in-list",
        "numbering-restarts",
        "markers-stripped",
        "paragraph-kept",
        "list-kinds",
        "invalid-skipped",
    ],
)
def test_parts_render_as_plain_structured_text(parts, expected):
    assert render(parts) == expected


def rewrite(body, packet=SOURCES):
    return validate_output("incoming_rewrite", EngineResponse(body), packet)


def test_validated_parts_become_the_rendered_text():
    result = rewrite(
        {
            "parts": [part("paragraph", "Lead:"), part("bullet", "one")],
            "image_context": [],
        }
    )
    assert result.text == "Lead:\n• one"
    assert rewrite({"text": "Legacy fixture.", "image_context": []}).text == (
        "Legacy fixture."
    )


@pytest.mark.parametrize(
    "body,code",
    [
        (
            {"parts": [part("paragraph", "x")], "text": "y", "image_context": []},
            "schema",
        ),
        ({"image_context": []}, "schema"),
        ({"parts": [part("paragraph", " ")], "image_context": []}, "schema"),
        ({"parts": [part("heading", "x")], "image_context": []}, "schema"),
        ({"parts": [], "image_context": []}, "omitted_target_text"),
        # ADR 30: emotion is a separate decision, outside this contract.
        (
            {"parts": [part("paragraph", "x")], "image_context": [], "emotion": "sad"},
            "schema",
        ),
    ],
    ids=["both", "neither", "blank-part", "unknown-kind", "omitted", "emotion"],
)
def test_malformed_parts_fail_with_fixed_codes(body, code):
    with pytest.raises(InvalidOutput) as raised:
        rewrite(body)
    assert str(raised.value) == code


def test_image_only_blocks_may_return_no_parts():
    image_only = {"messages": [{"text": ""}]}
    assert rewrite({"parts": [], "image_context": []}, image_only).text == ""


@pytest.mark.parametrize(
    "messages,context,images,minimum",
    [
        ([{"text": "  Authored target.\n"}], [], [], 1),
        (
            [{"text": " \n", "transcript": {"text": "  Prepared local speech.  "}}],
            [],
            [],
            1,
        ),
        ([{"text": ""}, {"text": "Second source has words."}], [], [], 1),
        (
            [{"text": "Image caption.", "media": "image"}],
            [],
            [{"message_id": 1}],
            1,
        ),
        ([{"text": " \n", "transcript": {"text": " \t"}}], [], [], 0),
        (
            [{"text": "", "media": "image"}],
            [{"text": "Text in Supporting Context."}],
            [{"message_id": 1}],
            0,
        ),
        (
            [{"text": "", "media": "image"}],
            [{"text": "", "transcript": {"text": "Supporting speech."}}],
            [{"message_id": 1}, {"message_id": 2}],
            0,
        ),
    ],
    ids=[
        "target-original",
        "target-transcript",
        "later-target-text",
        "target-caption",
        "blank-original-and-transcript",
        "image-only-with-textual-support",
        "image-only-with-transcribed-support",
    ],
)
def test_schema_and_final_validation_use_only_prepared_target_text(
    messages, context, images, minimum
):
    packet = {"messages": messages, "context": context, "images": images}
    schema = output_schema("incoming_rewrite", packet)
    assert schema["properties"]["parts"].get("minItems", 0) == minimum
    assert schema["properties"]["parts"]["maxItems"] == 80
    image_schema = schema["properties"]["image_context"]
    assert image_schema["minItems"] == image_schema["maxItems"] == len(images)
    identities = [image["message_id"] for image in images]
    if identities:
        assert (
            schema["$defs"]["ImageContext"]["properties"]["message_id"]["enum"]
            == identities
        )
    empty = {
        "parts": [],
        "image_context": [
            {"message_id": identity, "text": "Hidden authored image meaning."}
            for identity in identities
        ],
    }
    if minimum:
        # A provider ignoring its stricter request still fails the final check.
        with pytest.raises(InvalidOutput, match="^omitted_target_text$"):
            rewrite(empty, packet)
    else:
        assert rewrite(empty, packet).text == ""


def test_preview_renders_every_boundary_as_a_prefix_without_metadata():
    body = json.dumps(
        {
            "parts": [
                part("paragraph", "Для поездки проверь, пожалуйста:"),
                part("bullet", "есть ли полотенца"),
                part("bullet", 'можно ли с собакой "Рексом"'),
                part("bullet", "парковку 😀"),
                part("paragraph", "And the package?"),
            ],
            "image_context": [{"message_id": 1, "text": "HIDDEN IMAGE MEANING"}],
            "emotion": "neutral",
        },
        ensure_ascii=False,
    )
    final = render(json.loads(body)["parts"])
    preview, shown = PartsPreview(), []
    for character in body:
        value = preview.feed(character)
        if value is not None and (not shown or value != shown[-1]):
            shown.append(value)
    assert shown[-1] == final
    assert all(final.startswith(value) for value in shown)
    assert len(shown) > 10
    assert not any("HIDDEN" in value or '"kind"' in value for value in shown)


def test_preview_still_shows_a_legacy_root_text():
    preview = PartsPreview()
    assert preview.feed('{"text":"Legacy ') == "Legacy "
    assert preview.feed('words","image_context":[]}') == "Legacy words"


def test_preview_tolerates_a_raw_line_break_and_rejects_other_controls():
    preview = PartsPreview()
    assert preview.feed('{"parts":[{"kind":"paragraph","text":"One\nTwo"}') == (
        "One\nTwo"
    )
    with pytest.raises(ValueError):
        PartsPreview().feed('{"parts":[{"kind":"paragraph","text":"bad\x07"')


async def test_the_adapter_streams_rendered_parts_and_validates_them(tmp_path):
    content = json.dumps(
        {
            "parts": [part("paragraph", "Lead-in:"), part("bullet", "item")],
            "image_context": [],
        }
    )
    seen = []

    async def handler(request):
        seen.append(json.loads(request.content))
        events = []
        for index, start in enumerate(range(0, len(content), 9)):
            chunk = content[start : start + 9]
            last = start + 9 >= len(content)
            events.append(
                "data: "
                + json.dumps(
                    {
                        "id": "authored-parts",
                        "model": MODEL,
                        "provider": "Xiaomi",
                        "choices": [
                            {
                                "delta": {"content": chunk},
                                "finish_reason": "stop" if last else None,
                            }
                        ],
                        **({"usage": {"cost": 0.000001}} if last else {}),
                    }
                )
                + "\n\n"
            )
        return httpx.Response(200, content="".join(events) + "data: [DONE]\n\n")

    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-authored-placeholder")
    engine = OpenRouterEngine(
        Settings(
            mode="personal",
            data=tmp_path,
            refs={
                "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
                "MUSE_API_KEY_REF": str(tmp_path / "key"),
            },
        ),
        user="authored-provider-pseudonym",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        rates={
            "prompt": "0.00000014",
            "completion": "0.00000028",
            "verified_at": time.time(),
            "context_length": 1048576,
        },
    )
    shown = []
    packet = {"messages": [{"id": 1, "text": "Authored."}], "images": []}
    response = await engine.generate_stream("incoming_rewrite", packet, shown.append)
    schema = seen[0]["response_format"]["json_schema"]["schema"]
    assert list(schema["properties"]) == ["parts", "image_context"]
    assert schema["required"] == ["parts", "image_context"]
    assert schema["properties"]["parts"]["minItems"] == 1
    assert shown[-1] == "Lead-in:\n• item" and len(shown) > 2
    assert validate_output("incoming_rewrite", response, packet).text == shown[-1]
    await engine.client.aclose()
