"""Installed Telethon constructors exercised against authored in-memory RPCs."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.domain.catalog import (
    GIF_LIMIT,
    LIST_LIMIT,
    STICKER_LIMIT,
    CatalogOrigin,
    NativeCatalogSource,
    source_revision,
)
from enloq.domain.models import Refused, Unavailable
from enloq.transport import catalog as native_catalog
from enloq.transport.catalog import (
    NativeCatalog,
    native_source,
    pack_reference,
)
from enloq.transport.telethon import PersonalTransport
from pydantic import ValidationError
from telethon import functions, types

from backend.tests.test_transport import Clock

BIG_ID = 2**53 + 123


def native_pack(identity=BIG_ID + 1, *, hash=1, **kwargs):
    return types.StickerSet(
        identity, -99, "Authored pack", "authored_pack", 1, hash, **kwargs
    )


def document(
    identity=BIG_ID,
    *,
    kind="gif",
    mime=None,
    size=100,
    premium=False,
    locator=None,
    reference=b"authored-ref",
):
    attributes = (
        [types.DocumentAttributeVideo(duration=1, w=64, h=64, nosound=True)]
        if kind == "gif"
        else [
            types.DocumentAttributeSticker(
                "🙂", locator or types.InputStickerSetID(BIG_ID + 1, -99)
            ),
            types.DocumentAttributeImageSize(64, 64),
        ]
    )
    return types.Document(
        id=identity,
        access_hash=-17,
        file_reference=reference,
        date=datetime(2026, 10, 10, tzinfo=UTC),
        mime_type=mime or ("video/mp4" if kind == "gif" else "image/webp"),
        size=size,
        dc_id=2,
        attributes=attributes,
        thumbs=[types.PhotoSize("m", 64, 64, 77)],
        video_thumbs=[types.VideoSize("f", 64, 64, 100)] if premium else None,
    )


def bot_message():
    return types.BotInlineMessageText(
        message="Private bot-prepared content must never be sent or stored"
    )


class RPC:
    def __init__(self, replies=None, *, premium=False, account=1):
        self.replies = replies or {}
        self.requests = []
        self.user = types.User(
            account, first_name="Authored owner", premium=premium, bot=False
        )
        self.me_reads = 0
        self.entity_reads = []
        self.live = True

    def is_connected(self):
        return self.live

    async def get_me(self):
        self.me_reads += 1
        return self.user

    async def get_input_entity(self, username):
        self.entity_reads.append(username)
        return types.InputPeerUser(700, 701)

    async def __call__(self, request):
        self.requests.append(request)
        # Constructor serialization proves installed protocol fields are valid.
        assert bytes(request)
        response = self.replies[type(request)]
        if isinstance(response, Exception):
            raise response
        return response


def owner(client):
    transport = PersonalTransport.__new__(PersonalTransport)
    transport.account_id = 1
    transport.client = client
    transport.verified = True
    transport.requires_login = False
    transport.store = SimpleNamespace(clock=lambda: 2_000_000_000)
    transport.identity = None
    transport.catalog_changed = lambda: None
    return transport


def library_replies(*, gifs=None, stickers=None, packs=None):
    return {
        functions.messages.GetSavedGifsRequest: types.messages.SavedGifs(
            11, gifs if gifs is not None else [document()]
        ),
        functions.messages.GetAllStickersRequest: types.messages.AllStickers(
            12, packs if packs is not None else [native_pack()]
        ),
        functions.messages.GetRecentStickersRequest: types.messages.RecentStickers(
            13, [], stickers if stickers is not None else [document(kind="sticker")], []
        ),
        functions.messages.GetFavedStickersRequest: types.messages.FavedStickers(
            14, [], stickers if stickers is not None else [document(kind="sticker")]
        ),
    }


async def test_native_library_reads_bounded_metadata_only_and_real_entitlement():
    client = RPC(library_replies(), premium=True)
    transport = owner(client)
    result = await transport.catalog_library()
    assert result.premium is True and client.me_reads == 1
    assert len(client.requests) == 4
    assert [type(request) for request in client.requests] == list(library_replies())
    assert all(request.hash == 0 for request in client.requests)
    assert client.requests[2].attached is False
    assert result.gifs[0].document_id == str(BIG_ID)
    assert result.packs[0].id == str(BIG_ID + 1)
    assert result.recent[0].origins[1].pack.id == str(BIG_ID + 1)
    assert transport.identity is client.user and transport.verified
    assert not client.entity_reads


async def test_library_filters_masks_custom_emoji_and_reports_bounded_metadata():
    docs = [document(i + 1) for i in range(LIST_LIMIT + 1)]
    docs.insert(0, types.DocumentEmpty(999))
    client = RPC(
        library_replies(
            gifs=docs,
            packs=[
                native_pack(),
                native_pack(5, masks=True),
                native_pack(6, emojis=True),
                native_pack(7, archived=True),
            ],
        )
    )
    result = await NativeCatalog(owner(client)).library()
    assert (
        result.truncated
        and len(result.gifs) == LIST_LIMIT - 1
        and len(result.packs) == 1
    )
    assert len(client.requests) == 4


@pytest.mark.parametrize("account,bot", [(2, False), (1, True)])
async def test_identity_mismatch_withdraws_owner_before_any_library_request(
    account, bot
):
    client = RPC(library_replies(), account=account)
    client.user.bot = bot
    transport = owner(client)
    with pytest.raises(Refused):
        await transport.catalog_library()
    assert client.me_reads == 1 and not client.requests
    assert not transport.verified and transport.requires_login
    with pytest.raises(Unavailable):
        await transport.catalog_search("authored owner query")
    assert not client.requests


async def test_native_entitlement_timeout_is_bounded_and_retryable(monkeypatch):
    client = RPC()
    entered = asyncio.Event()

    async def stalled():
        entered.set()
        await asyncio.Future()

    client.get_me = stalled
    monkeypatch.setattr(native_catalog, "ENTITLEMENT_TIMEOUT", 0.01)
    transport = owner(client)
    with pytest.raises(Unavailable, match="Account entitlement is unavailable"):
        await transport.catalog_entitlement()
    assert entered.is_set() and not client.requests
    assert transport.verified
    client.get_me = RPC(premium=True).get_me
    assert await transport.catalog_entitlement() is True


async def test_pack_fetch_is_one_exact_known_pack_without_native_attribute_mutation():
    pack = native_pack()
    keyword = types.StickerKeyword(BIG_ID, ["authored keyword"])
    response = types.messages.StickerSet(
        pack,
        [types.StickerPack("🙂", [BIG_ID])],
        [keyword],
        [document(kind="sticker", premium=True)],
    )
    client = RPC({functions.messages.GetStickerSetRequest: response})
    result = await owner(client).catalog_pack(pack_reference(pack))
    request = client.requests[0]
    assert isinstance(request.stickerset, types.InputStickerSetID)
    assert (
        request.stickerset.id == pack.id
        and request.stickerset.access_hash == pack.access_hash
    )
    assert request.hash == 0 and len(client.requests) == 1
    assert result.items[0].metadata.requires_premium
    assert result.items[0].metadata.keywords == ("authored keyword", "🙂")
    assert keyword.keyword == ["authored keyword"]
    assert result.items[0].thumbnail.size == 77


async def test_search_uses_configured_bot_empty_peer_and_exact_owner_query():
    native = types.BotInlineMediaResult(
        "native-hit", "gif", bot_message(), document=document()
    )
    web = types.BotInlineResult(
        "web-hit",
        "gif",
        bot_message(),
        title="Authored web GIF",
        content=types.WebDocument(
            "https://example.org/authored.gif", 123, 100, "image/gif", []
        ),
    )
    unsafe = types.BotInlineResult(
        "unsafe-hit",
        "gif",
        bot_message(),
        content=types.WebDocument(
            "http://127.0.0.1/private", 123, 100, "image/gif", []
        ),
    )
    other = types.BotInlineResult("photo", "photo", bot_message())
    client = RPC(
        {
            functions.help.GetConfigRequest: SimpleNamespace(
                gif_search_username="configured_gif_bot"
            ),
            functions.messages.GetInlineBotResultsRequest: types.messages.BotResults(
                9, [native, web, unsafe, other], 300, [], next_offset="next"
            ),
        }
    )
    result = await owner(client).catalog_search(" exact authored query ", "offset")
    assert client.entity_reads == ["configured_gif_bot"]
    query = client.requests[1]
    assert query.query == " exact authored query " and query.offset == "offset"
    assert isinstance(query.peer, types.InputPeerEmpty) and query.geo_point is None
    assert isinstance(query.bot, types.InputUser) and query.bot.user_id == 700
    assert result.next_offset == "next" and result.truncated and len(result.items) == 2
    assert isinstance(result.items[0], NativeCatalogSource)
    stored = result.model_dump_json()
    assert (
        "Private bot-prepared content" not in stored and "authored query" not in stored
    )


async def test_empty_config_refuses_without_resolving_unrelated_bot():
    client = RPC(
        {functions.help.GetConfigRequest: SimpleNamespace(gif_search_username=None)}
    )
    with pytest.raises(Unavailable):
        await owner(client).catalog_search("authored")
    assert not client.entity_reads and len(client.requests) == 1


async def test_search_reference_refresh_never_reruns_query_or_substitutes_other_document():
    original = native_source(document(), 1, "gif", CatalogOrigin(kind="search"))
    client = RPC(
        {
            functions.messages.GetSavedGifsRequest: types.messages.SavedGifs(
                1, [document(reference=b"renewed"), document(123)]
            )
        }
    )
    transport = owner(client)
    refreshed = await transport.catalog_refresh_document(original)
    assert refreshed.document_id == original.document_id
    assert (
        source_revision(refreshed) == source_revision(original)
        and refreshed.file_reference != original.file_reference
    )
    assert refreshed.origins == original.origins
    assert len(client.requests) == 1 and isinstance(
        client.requests[0], functions.messages.GetSavedGifsRequest
    )
    client.replies[functions.messages.GetSavedGifsRequest] = types.messages.SavedGifs(
        1, [document(123)]
    )
    with pytest.raises(Unavailable):
        await transport.catalog_refresh_document(original)
    assert not client.entity_reads and all(
        isinstance(r, functions.messages.GetSavedGifsRequest) for r in client.requests
    )


@pytest.mark.parametrize(
    "locator",
    [
        types.InputStickerSetID(BIG_ID + 1, -99),
        types.InputStickerSetShortName("authored_pack"),
    ],
)
async def test_disappeared_recent_sticker_refreshes_exact_attribute_known_pack(locator):
    original = native_source(
        document(kind="sticker", locator=locator),
        1,
        "sticker",
        CatalogOrigin(kind="recent"),
    )
    client = RPC(
        {
            functions.messages.GetRecentStickersRequest: types.messages.RecentStickers(
                1, [], [], []
            ),
            functions.messages.GetStickerSetRequest: types.messages.StickerSet(
                native_pack(), [], [], [document(kind="sticker", reference=b"renewed")]
            ),
        }
    )
    result = await owner(client).catalog_refresh_document(original)
    assert result.document_id == original.document_id
    assert len(client.requests) == 2 and type(client.requests[1].stickerset) is type(
        locator
    )


@pytest.mark.parametrize(
    "kind,size,premium,unavailable",
    [
        ("gif", GIF_LIMIT, False, False),
        ("gif", GIF_LIMIT + 1, False, True),
        ("sticker", STICKER_LIMIT, True, False),
        ("sticker", STICKER_LIMIT + 1, False, True),
    ],
)
def test_declared_bounds_and_native_premium_marker(kind, size, premium, unavailable):
    source = native_source(
        document(kind=kind, size=size, premium=premium),
        1,
        kind,
        CatalogOrigin(kind="saved" if kind == "gif" else "recent"),
    )
    assert bool(source.metadata.unavailable) is unavailable
    assert source.metadata.requires_premium is premium


@pytest.mark.parametrize("identity", [2**63, 10**20])
def test_native_protocol_overflow_is_rejected_before_request(identity):
    source = native_source(document(), 1, "gif", CatalogOrigin(kind="saved"))
    with pytest.raises(ValidationError):
        NativeCatalogSource.model_validate(
            {**source.model_dump(), "document_id": str(identity)}
        )
    with pytest.raises(Refused):
        native_catalog.native_number(str(identity), identity=True)


@pytest.mark.parametrize(
    "rpc_request",
    [
        functions.messages.GetSavedGifsRequest(0),
        functions.messages.GetAllStickersRequest(0),
        functions.messages.GetRecentStickersRequest(0, attached=False),
        functions.messages.GetFavedStickersRequest(0),
        functions.messages.GetStickerSetRequest(
            types.InputStickerSetID(BIG_ID + 1, -99), 0
        ),
        functions.messages.GetInlineBotResultsRequest(
            types.InputUser(700, 701), types.InputPeerEmpty(), "authored", ""
        ),
    ],
)
async def test_catalog_rpc_registration_uses_existing_account_read_pacing(
    tmp_path, rpc_request
):
    clock = Clock()
    policy = clock.policy(tmp_path / "pacing.sqlite3")
    starts = []

    async def read():
        starts.append(clock.now)
        return True

    await policy.execute(rpc_request, read)
    await policy.execute(rpc_request, read)
    assert starts == [1000, 1001] and clock.waits == [1.0]


async def test_library_update_marks_stale_only_without_any_rpc():
    client = RPC()
    transport = owner(client)
    changes = []
    transport.catalog_changed = lambda: changes.append(True)
    for update in [
        types.UpdateSavedGifs(),
        types.UpdateRecentStickers(),
        types.UpdateFavedStickers(),
        types.UpdateStickerSets(),
        types.UpdateStickerSetsOrder([]),
    ]:
        await transport._catalog_update(update)
    await transport._catalog_update(types.UpdateMessageID(1, 2))
    assert len(changes) == 5 and not client.requests and client.me_reads == 0


async def test_pack_metadata_truncation_and_wrong_pack_are_truthful():
    actual = native_pack()
    response = types.messages.StickerSet(
        actual, [], [], [document(i + 1, kind="sticker") for i in range(LIST_LIMIT + 1)]
    )
    client = RPC({functions.messages.GetStickerSetRequest: response})
    transport = owner(client)
    detailed = await transport.catalog_pack(pack_reference(actual))
    assert detailed.truncated and len(detailed.items) == LIST_LIMIT
    client.replies[functions.messages.GetStickerSetRequest] = types.messages.StickerSet(
        native_pack(123), [], [], []
    )
    with pytest.raises(Refused):
        await transport.catalog_pack(pack_reference(actual))
