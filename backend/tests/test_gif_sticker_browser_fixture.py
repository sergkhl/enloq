"""Authored GS6 fixture wiring through ASGI, without a listening server."""

import time

import pytest
from enloq.api.server import create_app
from enloq.application import selected_media
from enloq.config import Settings
from enloq.inference.fake import FakeEngine
from fastapi.testclient import TestClient

from backend.tests.auth_helpers import trust_browser
from scripts.gif_sticker_fixture import register


def fixture_control(client, **body):
    response = client.post("/api/test/gif-sticker", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def rewritten(client, account=1):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = fixture_control(client, action="stats", account=account)
        if result["draft"]["state"] != "rewriting":
            return result
        time.sleep(0.01)
    pytest.fail("Authored fixture Rewrite did not settle")


def rewrite(client, assistant, text, peer="user:171"):
    key = f"{assistant.store.account_id}/{peer}"
    draft = client.post(
        f"/api/accounts/{assistant.store.account_id}/chats/{peer}/draft",
        json={"revision": assistant.store.draft(key)["revision"], "text": text},
    )
    assert draft.status_code == 200
    started = client.post(
        f"/api/accounts/{assistant.store.account_id}/chats/{peer}/rewrite",
        json={"revision": draft.json()["revision"]},
    )
    assert started.status_code == 200 and started.json()["state"] == "rewriting"
    return key


def test_fixture_catalog_selection_exact_send_and_two_account_scope(
    tmp_path, monkeypatch
):
    # The disposable fixture dispatches only its synthetic web URL. Restore
    # that module seam when this ASGI-only test completes.
    monkeypatch.setattr(
        selected_media, "fetch_web_media", selected_media.fetch_web_media
    )
    app = create_app(Settings(data=tmp_path, port=8776))
    entry = app.state.accounts.registry.register(
        2, "synthetic2.session", name="Second demo account"
    )
    app.state.accounts._create(entry)
    register(app)
    with TestClient(app, base_url="http://127.0.0.1:8776") as client:
        trust_browser(client, tmp_path)
        for account in (1, 2):
            assert (
                client.post(
                    "/api/test/gif-sticker", json={"account": account}
                ).status_code
                == 200
            )
        base = "/api/accounts/1/chats/user:171"
        catalog = client.get(base + "/catalog").json()
        source = next(
            item
            for item in catalog["recent"]
            if item["metadata"]["name"] == "Pattern static"
        )
        response = client.post(
            base + "/selected-media",
            json={
                "revision": 0,
                "token": source["token"],
                "catalog_revision": source["revision"],
            },
        )
        assert response.status_code == 200
        draft = response.json()
        selected = draft["selected_media"]
        assert selected["status"] == "ready" and selected["format"] == "webp"
        content = client.get(selected["content_url"])
        assert (
            content.status_code == 200
            and content.headers["content-type"] == "image/webp"
        )
        asset = base + f"/catalog/items/{source['token']}/assets"
        prepared = client.post(
            asset + "/prepare",
            json={"revision": source["revision"], "variant": "thumbnail"},
        )
        assert prepared.status_code == 200 and prepared.json()["state"] == "ready"
        client.post("/api/test/gif-sticker", json={"action": "evict"})
        assert (
            client.get(asset, params={"revision": source["revision"]}).json()["state"]
            == "missing"
        )
        # Fixture eviction uses real catalog cache keys while durable selected
        # bytes remain readable outside that disposable cache.
        assert client.get(selected["content_url"]).content == content.content
        # Catalog tickets cannot be moved to another authored account/chat.
        assert (
            client.post(
                "/api/accounts/2/chats/user:171/selected-media",
                json={
                    "revision": 0,
                    "token": source["token"],
                    "catalog_revision": source["revision"],
                },
            ).status_code
            == 400
        )
        sent = client.post(base + "/send", json={"revision": draft["revision"]})
        assert sent.status_code == 200 and sent.json()["operation"]["text"] == ""
        stats = client.post("/api/test/gif-sticker", json={"action": "stats"}).json()
        assert stats["operations"][0]["media"]["format"] == "webp"
        assert not stats["queries_persisted"]
        assert stats["asset_facts"]["static"]["size"] == len(content.content)
        assert not stats["rewrites"]


@pytest.mark.parametrize("replacement", ["generate", "stream_self", "stream_previous"])
def test_fixture_rebinds_replaced_engine_once_and_preserves_stream_gate_and_scope(
    tmp_path, monkeypatch, replacement
):
    monkeypatch.setattr(
        selected_media, "fetch_web_media", selected_media.fetch_web_media
    )
    server = create_app(Settings(data=tmp_path, port=8776))
    entry = server.state.accounts.registry.register(2, "synthetic2.session")
    server.state.accounts._create(entry)
    register(server)
    assistant = server.state.accounts.get(1)
    foreign = server.state.accounts.get(2)
    with TestClient(server, base_url="http://127.0.0.1:8776") as client:
        trust_browser(client, tmp_path)
        fixture_control(client, action="setup", account=1)
        fixture_control(client, action="setup", account=2)
        rewrite(client, assistant, "Authored first fixture journey")
        first = rewritten(client)
        assert first["draft"]["state"] == "rewritten"
        assert first["rewrites"] == [
            {"text": "Authored first fixture journey", "images": 0}
        ]
        previous = assistant.engine

        class Replacement(FakeEngine):
            async def generate(self, role, packet):
                value = await super().generate(role, packet)
                if role == "outgoing_rewrite":
                    value["text"] = "Authored replacement engine result"
                return value

        class StreamingReplacement(Replacement):
            def __init__(self):
                super().__init__()
                self.streams = []
                self.published = []

            async def generate_stream(self, role, packet, on_text):
                self.streams.append((role, packet))
                on_text("Authored replacement stream prefix")
                self.published.append(
                    [
                        row["text"]
                        for row in assistant.store.rows(
                            "SELECT text FROM rewrite_streams WHERE chat_key=?", (key,)
                        )
                    ]
                )
                return await (
                    previous.generate(role, packet)
                    if replacement == "stream_previous"
                    else self.generate(role, packet)
                )

        current = Replacement() if replacement == "generate" else StreamingReplacement()
        assistant.engine = current
        # The existing control state must follow a replacement even on Reset,
        # before the next setup/gate/stat admission reuses it.
        fixture_control(client, action="reset")
        generate_hook = current.generate
        stream_hook = getattr(current, "generate_stream", None)
        fixture_control(client, action="setup")
        for _ in range(3):
            fixture_control(client, action="stats")
        assert current.generate is generate_hook
        assert getattr(current, "generate_stream", None) is stream_hook
        fixture_control(client, action="gate", name="rewrite")
        key = rewrite(client, assistant, "Authored second fixture journey")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            held = fixture_control(client, action="stats")
            if held["rewrites"]:
                break
            time.sleep(0.01)
        assert held["rewrites"] == [
            {"text": "Authored second fixture journey", "images": 0}
        ]
        assert held["draft"]["state"] == "rewriting"
        assert not current.calls and not getattr(current, "streams", [])
        # Neither an unrelated role on this engine nor another account's
        # generation is counted or held by the fixture's Rewrite gate.
        memory = client.portal.call(
            lambda: current.generate(
                "chat_memory", {"messages": [], "new_source_ids": []}
            )
        )
        assert memory == {
            "person": {"add": [], "remove": [], "outdated": []},
            "account": {"add": [], "remove": [], "outdated": []},
        }
        foreign_result = client.portal.call(
            lambda: foreign.engine.generate(
                "outgoing_rewrite", {"draft": {"text": "Authored other-account words"}}
            )
        )
        assert foreign_result == {"text": "Authored other-account words"}
        assert fixture_control(client, action="stats", account=2)["rewrites"] == []
        assert len(fixture_control(client, action="stats")["rewrites"]) == 1
        prior_calls = len(previous.calls)
        fixture_control(client, action="release", name="rewrite")
        finished = rewritten(client)
        assert finished["draft"]["state"] == "rewritten"
        assert finished["rewrites"] == held["rewrites"]
        assert finished["draft"]["text"] == (
            "Authored second fixture journey"
            if replacement == "stream_previous"
            else "Authored replacement engine result"
        )
        assert len(previous.calls) == prior_calls + (replacement == "stream_previous")
        outgoing = [role for role, _ in current.calls if role == "outgoing_rewrite"]
        assert len(outgoing) == (replacement != "stream_previous")
        if replacement != "generate":
            assert (
                len(current.streams) == 1
                and current.streams[0][0] == "outgoing_rewrite"
            )
            assert current.published == [["Authored replacement stream prefix"]]
        # Ordinary private work retains the replacement engine's result without
        # creating an extra media-fixture event.
        private = rewrite(
            client, assistant, "Authored unrelated private words", "user:101"
        )
        deadline = time.monotonic() + 5
        while (
            assistant.store.draft(private)["state"] == "rewriting"
            and time.monotonic() < deadline
        ):
            time.sleep(0.01)
        assert assistant.store.draft(private)["state"] == "rewritten"
        assert fixture_control(client, action="stats")["rewrites"] == held["rewrites"]
        # Restoring the old engine must reuse its original hook rather than
        # recording a second nested event or recursing through replacements.
        assistant.engine = previous
        previous_hook = previous.generate
        fixture_control(client, action="reset")
        fixture_control(client, action="setup")
        assert previous.generate is previous_hook
        prior_calls = len(previous.calls)
        rewrite(client, assistant, "Authored restored-engine fixture journey")
        restored = rewritten(client)
        assert restored["draft"]["text"] == "Authored restored-engine fixture journey"
        assert restored["rewrites"] == [
            {"text": "Authored restored-engine fixture journey", "images": 0}
        ]
        assert len(previous.calls) == prior_calls + 1
