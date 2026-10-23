"""Speech-to-text through the LiteLLM proxy (ADR 39), with authored audio bytes
and fake proxy, listing and ffmpeg/ffprobe adapters."""

import base64
import json

import httpx
import pytest
from enloq.config import Settings, private_write
from enloq.domain.models import Refused, Unavailable
from enloq.inference import audio
from enloq.inference.audio import (
    HOSTED_WHISPER,
    LISTING,
    PROXY_REVISION,
    PROXY_WHISPER,
    ProxyWhisper,
)
from enloq.inference.base import ProviderRefused
from enloq.inference.work import FLAT_UNKNOWN, PaidSpeech
from enloq.storage.accounts import AccountRegistry
from enloq.storage.store import Store

from backend.tests.proxy_helpers import ELSEWHERE, ENDPOINT, HOSTED

KEY = "sk-authored-placeholder"
OPUS = b"OggS authored opus"
SPEECH = f"{ENDPOINT}/audio/transcriptions"


def settings(tmp_path, key=KEY, endpoint=ENDPOINT):
    private_write(tmp_path / "endpoint", endpoint)
    private_write(tmp_path / "key", key)
    return Settings(
        mode="personal",
        data=tmp_path,
        refs={
            "MUSE_ENDPOINT_REF": str(tmp_path / "endpoint"),
            "MUSE_API_KEY_REF": str(tmp_path / "key"),
        },
    )


@pytest.fixture
def note(tmp_path, monkeypatch):
    """An authored note whose measured length the test chooses."""
    path = tmp_path / "note.bin"
    path.write_bytes(b"authored voice")
    length = {"seconds": 9.2}
    calls = []

    async def process(argv, **kwargs):
        calls.append(argv[0])
        if "ffprobe" in argv[0]:
            return json.dumps(
                {"format": {"duration": str(length["seconds"])}}
            ).encode(), b""
        assert argv[-1] == "pipe:1" and "libopus" in argv
        return OPUS, b""

    monkeypatch.setattr(audio, "bounded_process", process)
    return path, length, calls


def listing(rate="0.00003055555555555", status=0):
    return {
        "data": {
            "id": HOSTED_WHISPER,
            "endpoints": [
                {"tag": "elevenlabs", "status": status, "pricing": {"prompt": rate}}
            ],
        }
    }


class Proxy:
    """The proxy's speech route, its pass-through and OpenRouter's listing."""

    def __init__(self, *, local=200, hosted=200, rate="0.00003055555555555", urls=None):
        self.local, self.hosted, self.rate = local, hosted, rate
        self.speech, self.scribe = urls or (SPEECH, HOSTED)
        self.seen = []

    def __call__(self, request):
        url = str(request.url)
        self.seen.append(url)
        if url == f"{LISTING}/models/{HOSTED_WHISPER}/endpoints":
            assert "authorization" not in request.headers
            return httpx.Response(200, json=listing(self.rate))
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.headers["x-litellm-enable-message-redaction"] == "true"
        if url == self.speech:
            body = request.content
            assert b'name="model"\r\n\r\n' + PROXY_WHISPER.encode() in body
            assert b"authored voice" in body
            if isinstance(self.local, Exception):
                raise self.local
            return httpx.Response(
                self.local,
                json={"text": "Authored words.", "language": "en", "duration": 9.2},
            )
        if url == self.scribe:
            body = json.loads(request.content)
            assert body["model"] == HOSTED_WHISPER
            assert body["input_audio"] == {
                "data": base64.b64encode(OPUS).decode(),
                "format": "ogg",
            }
            return httpx.Response(
                self.hosted,
                json={
                    "text": "Авторские слова.",
                    "language": "rus",
                    "usage": {"seconds": 9.2, "cost": 0.00028},
                },
            )
        raise AssertionError(url)


class Ledger:
    def __init__(self, refuse=None):
        self.refuse, self.events = refuse, []

    def reserve(self):
        if self.refuse:
            raise self.refuse
        self.events.append("reserve")

    def settle(self, model, provider, usage, *, refused=False, error=None):
        self.events.append((model, provider, usage, refused, error))


def whisper(tmp_path, proxy):
    client = httpx.AsyncClient(transport=httpx.MockTransport(proxy))
    return ProxyWhisper(settings(tmp_path), client=client)


async def test_a_short_note_stays_on_the_vps_and_costs_nothing(tmp_path, note):
    path, _, calls = note
    proxy, ledger, started = Proxy(), Ledger(), []
    result = await whisper(tmp_path, proxy).transcribe(
        path, on_start=started.append, ledger=ledger
    )
    assert result == {
        "text": "Authored words.",
        "language": "en",
        "model": PROXY_WHISPER,
        "model_revision": PROXY_REVISION,
        "duration_seconds": 9.2,
    }
    assert started == [9.2] and ledger.events == []
    assert proxy.seen == [SPEECH]
    assert not any("ffmpeg" in c for c in calls), "the VPS decodes the original"


@pytest.mark.parametrize("outage", [503, httpx.ConnectError("refused")])
async def test_a_vps_outage_falls_back_to_the_receipted_hosted_model(
    tmp_path, note, outage
):
    path = note[0]
    proxy = Proxy(local=outage)
    ledger = Ledger()
    result = await whisper(tmp_path, proxy).transcribe(path, ledger=ledger)
    assert result["model"] == HOSTED_WHISPER and result["model_revision"] is None
    assert result["text"] == "Авторские слова." and result["language"] == "ru"
    assert ledger.events == [
        "reserve",
        (HOSTED_WHISPER, "elevenlabs", {"seconds": 9.2, "cost": 0.00028}, False, None),
    ]


async def test_a_vps_refusal_of_the_audio_is_not_an_outage(tmp_path, note):
    ledger = Ledger()
    with pytest.raises(Unavailable, match="HTTP 400"):
        await whisper(tmp_path, Proxy(local=400)).transcribe(note[0], ledger=ledger)
    assert ledger.events == []


async def test_without_a_ledger_nothing_is_sent_to_the_paid_model(tmp_path, note):
    path, length, _ = note
    length["seconds"] = 400
    proxy = Proxy(local=503)
    with pytest.raises(Unavailable, match="HTTP 503"):
        await whisper(tmp_path, proxy).transcribe(path)
    assert HOSTED not in proxy.seen


async def test_a_long_note_goes_hosted_first(tmp_path, note):
    path, length, _ = note
    length["seconds"] = 121
    proxy, ledger = Proxy(), Ledger()
    result = await whisper(tmp_path, proxy).transcribe(path, ledger=ledger)
    assert result["model"] == HOSTED_WHISPER
    assert SPEECH not in proxy.seen


@pytest.mark.parametrize(
    "case",
    ["budget", "rate", "credit"],
)
async def test_a_long_note_the_hosted_model_cannot_take_stays_on_the_vps(
    tmp_path, note, case
):
    path, length, _ = note
    length["seconds"] = 300
    proxy = Proxy(
        rate="0.0001" if case == "rate" else "0.00003055555555555",
        hosted=402 if case == "credit" else 200,
    )
    ledger = Ledger(
        refuse=Refused("Daily budget reached") if case == "budget" else None
    )
    result = await whisper(tmp_path, proxy).transcribe(path, ledger=ledger)
    assert result["model"] == PROXY_WHISPER and result["duration_seconds"] == 300
    if case == "credit":
        # A provider refusal did no work: it is settled as refused, counting nothing.
        assert ledger.events[0] == "reserve"
        assert ledger.events[1][3] is True
    else:
        assert ledger.events == [], "no reservation without admission"


async def test_hosted_rate_above_the_adopted_rate_sends_no_audio(tmp_path, note):
    proxy, ledger = Proxy(local=503, rate="0.0000612"), Ledger()
    with pytest.raises(ProviderRefused, match="adopted rate"):
        await whisper(tmp_path, proxy).transcribe(note[0], ledger=ledger)
    assert HOSTED not in proxy.seen and ledger.events == []


async def test_an_unanswered_hosted_call_keeps_its_flat_estimate(tmp_path, note):
    proxy, ledger = Proxy(local=503, hosted=500), Ledger()
    with pytest.raises(Unavailable, match="Hosted speech HTTP 500"):
        await whisper(tmp_path, proxy).transcribe(note[0], ledger=ledger)
    assert ledger.events == [
        "reserve",
        (HOSTED_WHISPER, "elevenlabs", {}, False, "Hosted speech HTTP 500"),
    ]


async def test_speech_goes_to_the_configured_proxy_on_another_host(tmp_path, note):
    urls = (
        f"{ELSEWHERE}/audio/transcriptions",
        "https://models.example.test/litellm/openrouter/api/v1/audio/transcriptions",
    )
    proxy, ledger = Proxy(local=503, urls=urls), Ledger()
    client = httpx.AsyncClient(transport=httpx.MockTransport(proxy))
    worker = ProxyWhisper(settings(tmp_path, endpoint=ELSEWHERE), client=client)
    result = await worker.transcribe(note[0], ledger=ledger)
    assert result["model"] == HOSTED_WHISPER
    listed = f"{LISTING}/models/{HOSTED_WHISPER}/endpoints"
    assert proxy.seen == [urls[0], listed, urls[1]]


async def test_the_key_must_be_the_proxy_callers(tmp_path, note):
    client = httpx.AsyncClient(transport=httpx.MockTransport(Proxy()))
    worker = ProxyWhisper(
        settings(tmp_path, "sk-or-authored-placeholder"), client=client
    )
    with pytest.raises(Refused, match="LiteLLM proxy"):
        await worker.transcribe(note[0])


def test_paid_speech_is_admitted_reserved_and_receipted_in_the_daily_budget(tmp_path):
    registry = AccountRegistry(tmp_path)
    store = Store(
        tmp_path / "1/rewriting.sqlite3", account_id=1, shared_path=registry.path
    )

    def ledger(identity):
        return PaidSpeech(store, identity, "1/user:42/7", "3")

    def row(identity):
        return registry.rows(
            "SELECT engine,role,reserved,actual,status,family FROM model_reservations WHERE id=?",
            (identity,),
        )[0]

    paid = ledger("receipted")
    paid.reserve()
    assert row("receipted") == {
        "engine": "speech",
        "role": "transcript",
        "reserved": FLAT_UNKNOWN,
        "actual": None,
        "status": "unknown",
        "family": "daily",
    }
    paid.settle(HOSTED_WHISPER, "elevenlabs", {"seconds": 9.2, "cost": 0.00028})
    assert row("receipted")["status"] == "receipted"
    assert row("receipted")["actual"] == 280_000
    assert store.rows("SELECT role,model,provider FROM receipts") == [
        {"role": "transcript", "model": HOSTED_WHISPER, "provider": "elevenlabs"}
    ]

    refused = ledger("refused")
    refused.reserve()
    refused.settle(HOSTED_WHISPER, "elevenlabs", {}, refused=True, error="HTTP 402")
    assert row("refused")["status"] == "refused" and row("refused")["actual"] == 0

    unknown = ledger("unknown")
    unknown.reserve()
    unknown.settle(HOSTED_WHISPER, "elevenlabs", {}, error="unreachable")
    assert row("unknown")["status"] == "unknown"

    registry.set_budget(0)
    with pytest.raises(Refused, match="Daily budget reached"):
        ledger("over").reserve()
    assert not registry.rows("SELECT id FROM model_reservations WHERE id='over'")
