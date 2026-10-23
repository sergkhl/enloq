import json
from types import SimpleNamespace

import pytest
from enloq import setup, setup_login
from enloq.config import Settings, private_write
from enloq.domain.models import Refused
from telethon import errors
from telethon.tl import functions, types

PHONE = "+1 (202) 555-0123"
CODE = "synthetic-login-code"
FIRST_HASH = "synthetic-first-code-hash"
SECOND_HASH = "synthetic-second-code-hash"


def sent_code(delivery=None, *, next_type=None, timeout=None, code_hash=FIRST_HASH):
    return types.auth.SentCode(
        type=delivery or types.auth.SentCodeTypeApp(length=5),
        phone_code_hash=code_hash,
        next_type=next_type,
        timeout=timeout,
    )


class Client:
    def __init__(self, first, second=None):
        self.first, self.second = first, second
        self.sends, self.resends, self.sign_ins = [], [], []
        self.disconnected = False
        self.authorized = False
        self.identity = 7

    async def send_code_request(self, phone):
        self.sends.append(phone)
        return self.first

    async def __call__(self, request):
        assert isinstance(request, functions.auth.ResendCodeRequest)
        self.resends.append(request)
        if isinstance(self.second, Exception):
            raise self.second
        return self.second

    async def connect(self):
        pass

    async def disconnect(self):
        self.disconnected = True

    async def is_user_authorized(self):
        return self.authorized

    async def sign_in(self, **kwargs):
        self.sign_ins.append(kwargs)

    async def get_me(self):
        return SimpleNamespace(id=self.identity, bot=False)


def inputs(monkeypatch, *steps):
    """Each hidden answer advances a fake monotonic clock; no real waits or calls."""
    clock = [0]
    answers = iter(steps)

    def read(_prompt):
        advance, answer = next(answers)
        clock[0] += advance
        return answer

    monkeypatch.setattr(setup_login.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(setup_login.getpass, "getpass", read)


@pytest.mark.parametrize(
    "delivery,expected",
    [
        (types.auth.SentCodeTypeApp(5), "telegram_app"),
        (types.auth.SentCodeTypeSms(5), "sms"),
        (types.auth.SentCodeTypeCall(5), "voice_call"),
        (types.auth.SentCodeTypeFlashCall("synthetic-pattern"), "flash_call"),
        (types.auth.SentCodeTypeMissedCall("synthetic-prefix", 5), "missed_call"),
        (types.auth.SentCodeTypeSmsWord("synthetic-word-prefix"), "sms_word"),
        (types.auth.SentCodeTypeSmsPhrase("synthetic-phrase-prefix"), "sms_phrase"),
    ],
)
async def test_first_code_uses_reported_route_without_resend_or_secret_output(
    delivery, expected, monkeypatch, capsys
):
    client = Client(sent_code(delivery))
    inputs(monkeypatch, (0, CODE))
    assert await setup_login.request_login_code(client, PHONE) == (CODE, FIRST_HASH)
    assert client.sends == ["12025550123"] and client.resends == []
    output = capsys.readouterr().out
    assert json.loads(output.splitlines()[0]) == {
        "delivery": expected,
        "next_delivery": None,
        "retry_after_seconds": None,
    }
    for private_value in [PHONE, "12025550123", CODE, FIRST_HASH, "synthetic"]:
        assert private_value not in output


async def test_explicit_fallback_waits_for_deadline_and_uses_new_code_hash(
    monkeypatch, capsys
):
    client = Client(
        sent_code(next_type=types.auth.CodeTypeSms(), timeout=60),
        sent_code(types.auth.SentCodeTypeSms(5), code_hash=SECOND_HASH),
    )
    inputs(monkeypatch, (10, ""), (50, ""), (0, "resend"), (0, CODE))
    assert await setup_login.request_login_code(client, PHONE) == (CODE, SECOND_HASH)
    assert len(client.sends) == len(client.resends) == 1
    request = client.resends[0]
    assert request.phone_number == "12025550123"
    assert request.phone_code_hash == FIRST_HASH
    output = capsys.readouterr().out
    summaries = [
        json.loads(line) for line in output.splitlines() if line.startswith("{")
    ]
    assert [value["delivery"] for value in summaries] == ["telegram_app", "sms"]
    assert summaries[0]["next_delivery"] == "sms"
    assert summaries[0]["retry_after_seconds"] == 60
    assert FIRST_HASH not in output and SECOND_HASH not in output


async def test_enter_before_deadline_does_not_send_anything(monkeypatch):
    client = Client(sent_code(next_type=types.auth.CodeTypeSms(), timeout=60))
    inputs(monkeypatch, (59, ""), (0, CODE))
    assert await setup_login.request_login_code(client, PHONE) == (CODE, FIRST_HASH)
    assert len(client.sends) == 1 and not client.resends


async def test_declining_fallback_never_sends_or_exposes_typed_value(
    monkeypatch, capsys
):
    client = Client(sent_code(next_type=types.auth.CodeTypeSms(), timeout=0))
    inputs(monkeypatch, (0, ""), (0, CODE))
    with pytest.raises(Refused):
        await setup_login.request_login_code(client, PHONE)
    assert len(client.sends) == 1 and not client.resends
    assert CODE not in capsys.readouterr().out


@pytest.mark.parametrize(
    "response",
    [
        sent_code(),
        sent_code(next_type=types.auth.CodeTypeSms()),
        sent_code(next_type=types.auth.CodeTypeFragmentSms(), timeout=0),
    ],
)
async def test_no_supported_fallback_or_known_timeout_stops_on_empty_code(
    response, monkeypatch
):
    client = Client(response)
    inputs(monkeypatch, (0, ""))
    with pytest.raises(Refused):
        await setup_login.request_login_code(client, PHONE)
    assert len(client.sends) == 1 and not client.resends


async def test_resend_is_limited_to_one_even_when_telegram_offers_another(monkeypatch):
    response = sent_code(next_type=types.auth.CodeTypeSms(), timeout=0)
    client = Client(response, response)
    inputs(monkeypatch, (0, ""), (0, "resend"), (0, ""))
    with pytest.raises(Refused):
        await setup_login.request_login_code(client, PHONE)
    assert len(client.sends) == len(client.resends) == 1


@pytest.mark.parametrize("error", [TimeoutError(), errors.PhoneCodeExpiredError(None)])
async def test_unknown_or_expired_resend_does_not_restart_challenge(error, monkeypatch):
    client = Client(sent_code(next_type=types.auth.CodeTypeSms(), timeout=0), error)
    inputs(monkeypatch, (0, ""), (0, "resend"))
    with pytest.raises(type(error)):
        await setup_login.request_login_code(client, PHONE)
    assert len(client.sends) == len(client.resends) == 1


@pytest.mark.parametrize(
    "delivery,expected",
    [
        (types.auth.SentCodeTypeEmailCode("synthetic@example.invalid", 5), "email"),
        (types.auth.SentCodeTypeSetUpEmailRequired(), "email_setup_required"),
        (
            types.auth.SentCodeTypeFirebaseSms(5, receipt="synthetic-receipt"),
            "firebase_sms",
        ),
        (
            types.auth.SentCodeTypeFragmentSms("https://example.invalid/synthetic", 5),
            "fragment",
        ),
        (object(), "unsupported"),
    ],
)
async def test_unsupported_delivery_stops_without_code_prompt_or_private_output(
    delivery, expected, monkeypatch, capsys
):
    client = Client(sent_code(delivery))
    inputs(monkeypatch)
    with pytest.raises(Refused):
        await setup_login.request_login_code(client, PHONE)
    output = capsys.readouterr().out
    assert json.loads(output.splitlines()[0])["delivery"] == expected
    assert "synthetic" not in output
    assert not client.resends


async def test_sms_word_named_resend_is_a_code_not_an_action(monkeypatch):
    client = Client(sent_code(types.auth.SentCodeTypeSmsWord()))
    inputs(monkeypatch, (0, "resend"))
    assert await setup_login.request_login_code(client, PHONE) == ("resend", FIRST_HASH)
    assert not client.resends


@pytest.fixture
def profile(tmp_path, monkeypatch):
    private_write(tmp_path / "secrets/api-id", "12345")
    private_write(tmp_path / "secrets/api-hash", "a" * 32)
    config = tmp_path / "config/app.env"
    private_write(config, "ENLOQ_EXPECTED_ACCOUNT_ID=7\n")
    data = tmp_path / ".local/personal"
    saved_session = data / "personal-globesoul.session"
    private_write(saved_session, "authored-saved-session-placeholder")
    settings = Settings(
        mode="personal",
        data=data,
        refs={
            "TELEGRAM_API_ID_REF": str(tmp_path / "secrets/api-id"),
            "TELEGRAM_API_HASH_REF": str(tmp_path / "secrets/api-hash"),
            "EXPECTED_ACCOUNT_ID": "7",
        },
    )
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    monkeypatch.setattr(setup.Settings, "load", lambda *a, **kw: settings)
    return config, saved_session


async def test_empty_code_never_reaches_sign_in_or_alters_saved_profile(
    profile, monkeypatch
):
    config, saved_session = profile
    before = (config.read_bytes(), saved_session.read_bytes())
    client = Client(sent_code())
    monkeypatch.setattr(setup, "PersonalLoginClient", lambda *a, **kw: client)
    inputs(monkeypatch, (0, PHONE), (0, ""))
    with pytest.raises(Refused):
        await setup.login()
    assert not client.sign_ins and not client.resends
    assert client.disconnected
    assert (config.read_bytes(), saved_session.read_bytes()) == before


async def test_new_account_cannot_replace_existing_account_pin(profile, monkeypatch):
    config, saved_session = profile
    before = (config.read_bytes(), saved_session.read_bytes())
    client = Client(sent_code())
    client.identity = 99
    monkeypatch.setattr(setup, "PersonalLoginClient", lambda *a, **kw: client)
    inputs(monkeypatch, (0, PHONE), (0, CODE))
    with pytest.raises(Refused):
        await setup.login()
    assert client.sign_ins == [
        {"phone": PHONE, "code": CODE, "phone_code_hash": FIRST_HASH}
    ]
    assert client.disconnected
    assert (config.read_bytes(), saved_session.read_bytes()) == before
