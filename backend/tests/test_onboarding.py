from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq import setup
from enloq.config import parse_config, private_write
from enloq.domain.models import Refused
from enloq.transport.onboarding import (
    IdentityOnlyStartup,
)
from telethon.tl import functions, types


@pytest.fixture
def configured(tmp_path, monkeypatch):
    private_write(tmp_path / "secrets/api-id", "12345")
    private_write(tmp_path / "secrets/api-hash", "a" * 32)
    private_write(tmp_path / "secrets/model-key", "authored-placeholder")
    private_write(
        tmp_path / "config/app.env",
        "ENLOQ_TELEGRAM_API_ID_REF=secrets/api-id\nENLOQ_TELEGRAM_API_HASH_REF=secrets/api-hash\nENLOQ_MUSE_API_KEY_REF=secrets/model-key\n",
    )
    monkeypatch.setattr(setup, "ROOT", tmp_path)
    return tmp_path


def test_A14_explicit_credential_reuse_preserves_refs_and_session_separation(
    configured,
):
    setup.reuse_credentials()
    setup.reuse_credentials()
    values = parse_config(configured / "config/app.env")
    assert values["PERSONAL_TELEGRAM_API_ID_REF"] == values["TELEGRAM_API_ID_REF"]
    assert values["PERSONAL_TELEGRAM_API_HASH_REF"] == values["TELEGRAM_API_HASH_REF"]
    assert values["MUSE_API_KEY_REF"] == "secrets/model-key"
    assert not values.get("PERSONAL_SESSION_REF")
    assert not list(configured.rglob("*.session"))
    assert len(list((configured / "secrets").iterdir())) == 3
    setup.save_references({"PERSONAL_TELEGRAM_API_ID_REF": "secrets/different"})
    with pytest.raises(Refused):
        setup.reuse_credentials()
    assert (
        parse_config(configured / "config/app.env")["PERSONAL_TELEGRAM_API_ID_REF"]
        == "secrets/different"
    )


async def test_A14_identity_startup_omits_telethon_difference_read():
    requests, loaded = [], []

    class Client(IdentityOnlyStartup):
        _mb_entity_cache = SimpleNamespace(set_self_user=lambda *args: None)
        _message_box = SimpleNamespace(load=lambda *args: loaded.append(args))

        async def __call__(self, request):
            requests.append(request)
            return SimpleNamespace(pts=1, qts=2, seq=3, date=datetime.now(UTC))

    me = types.User(id=7, access_hash=0)
    assert await Client()._on_login(me) is me
    assert len(requests) == 1 and isinstance(
        requests[0], functions.updates.GetStateRequest
    )
    assert len(loaded) == 1
