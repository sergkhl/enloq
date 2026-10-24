"""Exercise ADR 56's idioms gate on its real paths without network access:
authored provider answers, a fake Telegram transport."""

import importlib.util
import json
import stat
from pathlib import Path

import httpx
from enloq.config import Settings, private_write
from enloq.domain import suggestions as domain
from enloq.inference.decider import MODEL as DECIDER
from enloq.inference.fake import label_choice
from enloq.inference.guidance import IDIOMS_FRAME
from enloq.inference.openrouter import LISTING, MUSE, MUSE_LISTED
from enloq.storage.accounts import AccountRegistry

from backend.tests.proxy_helpers import DECISIONS, ENDPOINT
from backend.tests.test_decider import route
from backend.tests.test_route_refusals import exact_route, model_info

spec = importlib.util.spec_from_file_location(
    "idioms_check", Path(__file__).parents[2] / "scripts/check-idioms.py"
)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
CASES = len(runner.CASES)
# Authored idiom sets: a yes and a no, each with an expression.
WRITTEN = {
    "ru": ["Конечно, я в деле!", "Увы, в этот раз я пас."],
    "en": ["Count me in!", "I'll have to sit this one out."],
}
PLAIN = "Sure."


def which(text):
    """The authored case whose latest message the request carries."""
    return next(c for c in runner.CASES if all(m in text for m in c["latest"]))


async def run(tmp_path, *, plain=None, foreign=None, refused=None, unreadable=None):
    private_write(tmp_path / "endpoint", ENDPOINT)
    private_write(tmp_path / "key", "sk-authored-placeholder")
    settings = Settings(
        mode="personal",
        data=tmp_path / "accounts",
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )
    registry = AccountRegistry(settings.data)
    before = registry.guidance()
    writer = domain.IDIOMS_WRITER
    written, labels, judged, refusing, garbled = [], [], [], [], []

    def write(request):
        """Both writers: MiMo's route and Muse's (ADR 56 Q16)."""
        if request.method == "GET":
            if str(request.url) == f"{ENDPOINT}/model/info":
                return httpx.Response(
                    200,
                    json=model_info(
                        exact_route(), exact_route(MUSE, input_cost_per_token=None)
                    ),
                )
            assert str(request.url) == f"{LISTING}/models/{MUSE_LISTED}/endpoints"
            endpoint = {
                "tag": "meta",
                "status": 0,
                "pricing": {"prompt": "0.0000001", "completion": "0.0000002"},
            }
            return httpx.Response(
                200, json={"data": {"id": MUSE_LISTED, "endpoints": [endpoint]}}
            )
        payload = json.loads(request.content)
        written.append(payload)
        system = payload["messages"][0]["content"]
        content = payload["messages"][1]["content"][0]["text"]
        # The frame and the field reach the instructions, never the content.
        assert IDIOMS_FRAME in system and "[Owner guidance: idioms]" in system
        assert '"idioms"' not in content
        case = which(content)
        writer = "muse" if payload["model"] == MUSE else "mimo"
        if (writer, case["name"]) == refused and not refusing:
            refusing.append(payload)
            return httpx.Response(
                429, headers={"retry-after": "0"}, json={"error": "Authored"}
            )
        texts = list(WRITTEN[case["language"]])
        if (writer, case["name"]) == plain:
            texts[1] = PLAIN
        if (writer, case["name"]) == foreign:
            texts = list(WRITTEN["en" if case["language"] == "ru" else "ru"])
        options = [
            {"text": text, "purpose": purpose}
            for text, purpose in zip(texts, ("accept", "decline"), strict=True)
        ]
        return httpx.Response(
            200,
            json={
                "id": f"authored-idioms-{len(written)}",
                "model": payload["model"],
                "provider": "meta" if writer == "muse" else "xiaomi",
                "choices": [
                    {
                        "message": {"content": json.dumps({"options": options})},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"cost": 0.0002},
            },
        )

    def decide(request):
        if request.method == "GET":
            return httpx.Response(
                200, json={"data": {"id": DECIDER, "endpoints": [route()]}}
            )
        assert str(request.url) == DECISIONS
        payload = json.loads(request.content)
        labels.append(payload)
        answers = {
            f"purpose_{reply['id']}": label_choice(
                "accept" if reply["id"] == 1 else "decline"
            )
            for reply in payload["state"]["replies"]
        }
        return httpx.Response(
            200,
            json={
                "id": f"gen-dec-authored-{len(labels)}",
                "model": DECIDER,
                "provider": "Perplexity",
                "answers": answers,
                "usage": {"input_tokens": 900, "output_tokens": 2, "cost": 0.000018},
            },
        )

    def judge(request):
        payload = json.loads(request.content)
        assert str(request.url) == f"{ENDPOINT}/chat/completions"
        assert payload["model"] == runner.JUDGE_MODEL
        asked = json.loads(payload["messages"][1]["content"])
        judged.append(asked)
        if (
            unreadable
            and which(json.dumps(asked, ensure_ascii=False))["name"] == (unreadable)
            and not garbled
        ):
            garbled.append(asked)
            return httpx.Response(
                200, json={"choices": [{"message": {"content": "Not JSON."}}]}
            )
        # Each option comes with the decision model's stance (its label).
        assert [o["stance"] for o in asked["options"]] == ["accept", "decline"]
        verdicts = [
            {
                "expression": None if text == PLAIN else text,
                "real": text != PLAIN,
                "meaning": text != PLAIN,
                "facts": False,
                "fault": False,
                "pressure": False,
                "commitment": False,
                "reason": "Authored verdict.",
            }
            for text in (o["text"] for o in asked["options"])
        ]
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": json.dumps({"options": verdicts})}}]
            },
        )

    def mocked(handler):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    report = await runner.observe(
        settings,
        root=tmp_path,
        route_factory=lambda s, r, b: runner.ObservedRoute(
            s, r, b, client=mocked(write)
        ),
        muse_factory=lambda s, r, b: runner.ObservedMuse(s, r, b, client=mocked(write)),
        decider_factory=lambda s, r, b: runner.ObservedDecider(
            s, r, b, client=mocked(decide)
        ),
        judge_factory=lambda s, r, b: runner.Judge(s, r, b, client=mocked(judge)),
    )
    folder = next((tmp_path / ".local").glob("idioms-check-*"))
    assert json.loads((folder / "report.json").read_text()) == json.loads(
        json.dumps(report)
    )
    for path in (folder / "report.json", folder / "sets.md"):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
    # The gate sets each writer in its own process only and puts them back.
    assert domain.IDIOMS_WRITER == writer
    assert registry.guidance() == before and report["telegram_calls"] == 0
    return report, written, labels, judged, folder


async def test_both_writers_write_every_case_and_a_tie_goes_to_the_owner(tmp_path):
    report, written, labels, judged, folder = await run(tmp_path)
    assert "stopped" not in report and report["within_bounds"]
    assert report["requests"] == {
        "writer": 2 * CASES,
        "decision": 2 * CASES,
        "judge": 2 * CASES,
    }
    assert [p["model"] == MUSE for p in written] == [False] * CASES + [True] * CASES
    summary = report["summary"]
    for writer in runner.WRITERS:
        assert summary[writer]["passed"] and summary[writer]["misses"] == 0
        assert summary[writer]["expression_met"] == CASES
    assert summary["winner"] == "tie"
    readable = (folder / "sets.md").read_text()
    assert "## ru_invitation (mimo)" in readable and "Count me in!" in readable


async def test_the_writer_with_fewer_misses_wins(tmp_path):
    report, *_ = await run(tmp_path, plain=("mimo", "en_invitation"))
    summary = report["summary"]
    # One set without an expression still meets 9 sets of 10 (11 of 12).
    assert summary["mimo"]["expression_met"] == CASES - 1
    assert summary["mimo"]["passed"] and summary["mimo"]["misses"] == 1
    assert summary["winner"] == "muse"


async def test_a_fixed_rule_miss_fails_its_writer(tmp_path):
    report, *_ = await run(tmp_path, foreign=("muse", "ru_good_news"))
    summary = report["summary"]
    assert not summary["muse"]["passed"] and summary["muse"]["fixed_rule_misses"] == 2
    missed = [s for s in report["sets"] if s["misses"]]
    assert [(s["writer"], s["name"]) for s in missed] == [("muse", "ru_good_news")]
    assert summary["winner"] == "mimo"


async def test_a_refusal_is_retried_after_its_delay(tmp_path):
    report, written, *_ = await run(tmp_path, refused=("mimo", "en_request"))
    assert report["requests"]["writer"] == 2 * CASES + 1
    retried = next(
        s for s in report["sets"] if (s["writer"], s["name"]) == ("mimo", "en_request")
    )
    assert retried["refusals"] == 1 and retried["state"] == "ready"
    assert report["summary"]["winner"] == "tie"


async def test_an_unreadable_judge_answer_gets_one_more_attempt(tmp_path):
    report, *_ = await run(tmp_path, unreadable="en_worried")
    assert report["requests"]["judge"] == 2 * CASES + 1
    assert all(s.get("verdicts") for s in report["sets"])
    assert report["summary"]["winner"] == "tie"
