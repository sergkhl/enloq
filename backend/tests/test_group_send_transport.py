"""Authored SDK peers exercise group send and cached-source download boundaries."""

import asyncio
import hashlib
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from enloq.domain.delivery import SendDescription, SendImage
from enloq.domain.models import DeliveryRefused, Refused
from enloq.transport import group_sending
from enloq.transport.mtproto import (
    RequestPolicy,
    _BoundedPacker,
    observation_cycle,
)
from PIL import Image
from telethon import errors, functions, types

from backend.tests.test_group_intake import entity, group, native, rig, source
from backend.tests.test_selected_transport import Evidence, retained

SENDS = (
    functions.messages.SendMessageRequest,
    functions.messages.SendMediaRequest,
    functions.messages.SendMultiMediaRequest,
)


class GroupRPC:
    request_policy = object()  # This stand-in models the final packer boundary.

    def __init__(self, app, now, peer, metadata):
        self.app, self.now, self.peer, self.metadata = app, now, peer, metadata
        self.user = types.User(id=app.store.account_id, access_hash=20, is_self=True)
        self.offered = app.store.account_id
        self.send_as_metadata = []
        self.calls, self.uploads, self.final = [], [], []
        self.before_wire = self.after_upload = None
        self.lookup_peer = None
        self.native = native(peer, 1, now[0], text="Authored source")
        self.document = None
        self.answer = 700
        self.manual_handoff = True
        self.online = True
        self.parts = [b"authored-media"]
        self.part_hook = None

    def is_connected(self):
        return self.online

    async def get_me(self):
        return self.user

    async def get_input_entity(self, peer):
        if self.lookup_peer:
            return self.lookup_peer
        if isinstance(peer, types.PeerChannel):
            return types.InputPeerChannel(peer.channel_id, 100)
        if isinstance(peer, types.PeerChat):
            return types.InputPeerChat(peer.chat_id)
        return types.InputPeerUser(peer.user_id, 10)

    async def upload_file(self, path, **kwargs):
        self.uploads.append((Path(path).read_bytes(), kwargs))
        if self.after_upload:
            self.after_upload()
        return types.InputFile(90, 1, kwargs["file_name"], "authored")

    async def __call__(self, request):
        if self.before_wire:
            self.before_wire(request)
        guard = getattr(request, "_enloq_final_guard", None)
        if guard:
            guard(request)
        assert bytes(request)
        self.calls.append(request)
        if isinstance(request, SENDS):
            evidence = request._enloq_wire
            if self.manual_handoff:
                evidence.watching()
                evidence.handing_off()
            self.final.append(request)
            if isinstance(self.answer, BaseException):
                raise self.answer
            if self.answer is None:
                return types.Updates([], [], [], datetime.now(UTC), 0)
            randoms = (
                [value.random_id for value in request.multi_media]
                if isinstance(request, functions.messages.SendMultiMediaRequest)
                else [request.random_id]
            )
            return types.Updates(
                [
                    types.UpdateMessageID(self.answer + index, random)
                    for index, random in enumerate(randoms)
                ],
                [],
                [],
                datetime.now(UTC),
                0,
            )
        if isinstance(
            request,
            (functions.channels.GetChannelsRequest, functions.messages.GetChatsRequest),
        ):
            if isinstance(self.metadata, BaseException):
                raise self.metadata
            return types.messages.Chats([self.metadata])
        if isinstance(request, functions.channels.GetSendAsRequest):
            return types.channels.SendAsPeers(
                [types.SendAsPeer(types.PeerUser(self.offered))],
                self.send_as_metadata,
                [self.user],
            )
        if isinstance(
            request,
            (
                functions.channels.GetMessagesRequest,
                functions.messages.GetMessagesRequest,
            ),
        ):
            return types.messages.Messages(
                topics=[],
                messages=[self.native],
                chats=[self.metadata],
                users=[types.User(id=7, first_name="Alice")],
            )
        if isinstance(request, functions.help.GetConfigRequest):
            return SimpleNamespace(caption_length_max=1024)
        if isinstance(request, functions.messages.UploadMediaRequest):
            if isinstance(request.media, types.InputMediaUploadedPhoto):
                return types.MessageMediaPhoto(
                    photo=types.Photo(91, 100, b"authored", datetime.now(UTC), [], 2)
                )
            if isinstance(request.media, types.InputMediaDocument):
                # Telegram's answer for an existing document (2026-10-17).
                raise errors.MediaInvalidError(request)
            return types.MessageMediaDocument(document=self.document)
        if isinstance(request, functions.upload.GetFileRequest):
            return types.upload.File(types.storage.FileUnknown(), 0, b"probe")
        raise AssertionError(type(request).__name__)

    def iter_download(self, location, **kwargs):
        """A selected document's reference probe: Telethon's iterator issues
        `upload.getFile` through the client, under the task's guards."""

        async def parts():
            request = functions.upload.GetFileRequest(
                location, 0, kwargs["request_size"]
            )
            yield (await self(request)).bytes

        return parts()

    async def iter_parts(self, asset, *, msg_data):
        assert msg_data[1] == 1
        for chunk in self.parts:
            if self.part_hook:
                self.part_hook()
            yield chunk


class PacedGroupRPC(GroupRPC):
    async def __call__(self, request):
        if isinstance(request, SENDS):
            request._enloq_packed = True

        async def perform():
            if isinstance(request, SENDS):
                state = SimpleNamespace(
                    request=request, future=asyncio.get_running_loop().create_future()
                )
                _BoundedPacker(SimpleNamespace(append=lambda value: None)).append(state)
                if state.future.done():
                    state.future.result()
                state.future.cancel()
            return await super(PacedGroupRPC, self).__call__(request)

        return await self.request_policy.execute(request, perform)


def setup(tmp_path, peer="channel:42", **metadata_flags):
    app, now = rig(tmp_path)
    key = group(app, now, peer)
    app.sync.ingest(source(app, now, peer))
    rpc = GroupRPC(app, now, peer, entity(peer, now[0], **metadata_flags))
    app.transport.client = rpc
    receipts = []
    app.transport.bind_receipts(receipts.append)
    description = SendDescription(
        "a" * 32, 1, peer, "Exact authored wording", 1, (901,)
    )
    return app, now, key, rpc, description, receipts


def image(tmp_path, index):
    directory = tmp_path / "send-images"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f"{index}.png"
    Image.new("RGB", (12, 12), "blue").save(path)
    path.chmod(0o600)
    content = path.read_bytes()
    return SendImage(
        path,
        directory,
        hashlib.sha256(content).hexdigest(),
        "image/png",
        len(content),
        12,
        12,
    )


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
@pytest.mark.parametrize("kind", ["text", "image", "album", "gif", "sticker"])
async def test_all_group_payloads_keep_exact_personal_sender_reply_bytes_and_zero_payment(
    tmp_path, peer, kind
):
    app, _, _, rpc, description, receipts = setup(tmp_path, peer)
    if kind in {"image", "album"}:
        count = 1 if kind == "image" else 3
        images = tuple(image(tmp_path, index) for index in range(count))
        description = replace(
            description,
            images=images,
            random_ids=tuple(901 + index for index in range(count)),
        )
    elif kind in {"gif", "sticker"}:
        media, rpc.document = await retained(tmp_path, kind=kind)
        description = replace(
            description, media=media, text=description.text if kind == "gif" else ""
        )
    evidence = Evidence()
    result = await app.transport.send(description, evidence)
    assert result.pairs == tuple(
        (identity, 700 + index) for index, identity in enumerate(description.random_ids)
    )
    assert len(rpc.final) == 1
    request = rpc.final[0]
    assert request.allow_paid_stars == 0 and request.allow_paid_floodskip is False
    assert request.reply_to.reply_to_msg_id == 1 and request.reply_to.top_msg_id is None
    assert group_sending.identity_of(request.peer) == peer
    if peer.startswith("channel:"):
        assert (
            isinstance(request.send_as, types.InputPeerUser)
            and request.send_as.user_id == 1
        )
    else:
        assert request.send_as is None
    if kind == "album":
        assert [value.message for value in request.multi_media] == [
            description.text,
            "",
            "",
        ]
    else:
        assert request.message == description.text
    if description.images:
        assert [value[0] for value in rpc.uploads] == [
            value.path.read_bytes() for value in description.images
        ]
    assert len(receipts) == len(description.random_ids)
    assert evidence.events == ["calling", "watched", "exposed"]
    assert all(
        not isinstance(
            value,
            (
                functions.messages.SaveDefaultSendAsRequest,
                functions.messages.ReadHistoryRequest,
                functions.channels.ReadHistoryRequest,
                functions.channels.GetParticipantsRequest,
                functions.channels.GetParticipantRequest,
                functions.messages.GetFullChatRequest,
            ),
        )
        for value in rpc.calls
    )


@pytest.mark.parametrize(
    "damage",
    [
        "account",
        "bot",
        "sender",
        "peer",
        "forum",
        "broadcast",
        "protected",
        "migrated",
        "anonymous",
        "paid",
        "plain_right",
        "untyped",
    ],
)
async def test_group_send_fails_closed_before_exposure_for_ineligible_identity_or_rights(
    tmp_path, damage
):
    app, now, key, rpc, description, _ = setup(tmp_path)
    if damage == "account":
        rpc.user = types.User(id=2)
    elif damage == "bot":
        rpc.user.bot = True
    elif damage == "sender":
        rpc.offered = 2
    elif damage == "peer":
        rpc.lookup_peer = types.InputPeerChannel(43, 100)
    elif damage == "migrated":
        app, now, key, rpc, description, _ = setup(tmp_path / "basic", "chat:42")
        rpc.metadata.migrated_to = types.InputChannel(99, 100)
    elif damage == "anonymous":
        rpc.metadata.admin_rights = types.ChatAdminRights(anonymous=True)
    elif damage == "paid":
        rpc.metadata.send_paid_messages_stars = 2
    elif damage == "plain_right":
        rpc.metadata.banned_rights = types.ChatBannedRights(None, send_plain=True)
    elif damage == "untyped":
        app.store.put_chat(app.store.chat(key).model_copy(update={"kind": "private"}))
    else:
        setattr(rpc.metadata, {"protected": "noforwards"}.get(damage, damage), True)
    evidence = Evidence()
    with pytest.raises(Refused):
        await app.transport.send(description, evidence)
    assert rpc.final == [] and "exposed" not in evidence.events


@pytest.mark.parametrize("kind", ["image", "album", "gif", "sticker"])
async def test_current_group_rights_and_reply_are_rechecked_after_media_preparation(
    tmp_path, kind
):
    app, _, key, rpc, description, _ = setup(tmp_path)
    if kind in {"image", "album"}:
        count = 1 if kind == "image" else 2
        description = replace(
            description,
            images=tuple(image(tmp_path, index) for index in range(count)),
            random_ids=tuple(901 + index for index in range(count)),
        )
        rpc.after_upload = lambda: setattr(
            rpc.metadata,
            "banned_rights",
            types.ChatBannedRights(None, send_photos=True),
        )
    else:
        media, rpc.document = await retained(tmp_path, kind=kind)
        description = replace(
            description, media=media, text=description.text if kind == "gif" else ""
        )

        def change(request):
            if isinstance(request, functions.upload.GetFileRequest):
                rpc.metadata.banned_rights = types.ChatBannedRights(
                    None, send_gifs=True, send_stickers=True
                )

        rpc.before_wire = change
    evidence = Evidence()
    with pytest.raises(DeliveryRefused, match="rights"):
        await app.transport.send(description, evidence)
    assert not rpc.final and "exposed" not in evidence.events
    assert app.store.message(key, 1).text == "Authored source"


@pytest.mark.parametrize("kind", ["album", "gif", "sticker"])
async def test_confirmed_access_loss_during_media_preparation_is_retained_without_a_send(
    tmp_path, kind
):
    app, _, key, rpc, description, _ = setup(tmp_path)
    if kind == "album":
        description = replace(
            description,
            images=(image(tmp_path, 1), image(tmp_path, 2)),
            random_ids=(901, 902),
        )
    else:
        media, rpc.document = await retained(tmp_path, kind=kind)
        description = replace(
            description, media=media, text=description.text if kind == "gif" else ""
        )

    def refuse(request):
        # An album associates its photos; a GIF or sticker reads 4 KB first.
        if isinstance(
            request,
            (functions.messages.UploadMediaRequest, functions.upload.GetFileRequest),
        ):
            raise errors.ChannelPrivateError(request)

    rpc.before_wire = refuse
    evidence = Evidence()
    with pytest.raises(DeliveryRefused, match="ChannelPrivateError"):
        await app.transport.send(description, evidence)
    assert app.store.groups.get(key).access == "lost"
    assert not rpc.final and "exposed" not in evidence.events


@pytest.mark.parametrize(
    "damage",
    [
        "wording",
        "caption",
        "sender",
        "reply",
        "payment",
        "floodskip",
        "access",
        "epoch",
        "metadata",
        "expired",
        "bytes",
    ],
)
async def test_final_wire_guard_refuses_mutation_or_changed_proofs_before_handoff(
    tmp_path, damage
):
    app, now, key, rpc, description, _ = setup(tmp_path)
    if damage in {"caption", "bytes"}:
        description = replace(description, images=(image(tmp_path, 1),))

    def change(request):
        if not isinstance(request, SENDS):
            return
        if damage in {"wording", "caption"}:
            request.message = "Changed"
        elif damage == "sender":
            request.send_as = types.InputPeerChannel(88, 90)
        elif damage == "reply":
            request.reply_to.reply_to_msg_id = 2
        elif damage == "payment":
            request.allow_paid_stars = 1
        elif damage == "floodskip":
            request.allow_paid_floodskip = True
        elif damage == "access":
            app.store.groups.observe(
                key,
                access="lost",
                supported=True,
                reason="authored-loss",
                observed_at=now[0],
            )
        elif damage == "epoch":
            app.store.groups.mark_gap(key)
        elif damage == "metadata":
            group_sending.invalidate(app.transport, description.peer)
        elif damage == "expired":
            now[0] += 16
        else:
            description.images[0].path.write_bytes(b"changed")

    rpc.before_wire = change
    evidence = Evidence()
    with pytest.raises(Refused):
        await app.transport.send(description, evidence)
    assert not rpc.final and "exposed" not in evidence.events


async def test_physical_protocol_repack_rechecks_proof_before_any_second_transmission(
    tmp_path,
):
    app, now, _, rpc, description, _ = setup(tmp_path)
    evidence = Evidence()
    await app.transport.send(description, evidence)
    request = rpc.final[0]
    appended = []
    packer = _BoundedPacker(SimpleNamespace(append=appended.append))
    first = SimpleNamespace(
        request=request, future=asyncio.get_running_loop().create_future()
    )
    packer.append(first)
    assert appended == [first]
    now[0] += 16
    second = SimpleNamespace(
        request=request, future=asyncio.get_running_loop().create_future()
    )
    packer.append(second)
    assert appended == [first]
    with pytest.raises(Refused, match="expired"):
        second.future.result()


async def test_short_native_slow_mode_resend_keeps_the_exact_payload_and_peer_hold(
    tmp_path,
):
    app, now, _, rpc, description, _ = setup(tmp_path)

    async def sleep(seconds):
        now[0] += seconds
        await asyncio.sleep(0)

    class Watched(Evidence):
        def holding(self, until):
            self.events.append("holding")

        def not_handed(self):
            self.events.append("not_handed")

        def flood_refused(self, error, until):
            self.events.append("flood_refused")

    paced = PacedGroupRPC(app, now, description.peer, rpc.metadata)
    paced.manual_handoff = False
    paced.request_policy = RequestPolicy(
        tmp_path / "policy.sqlite3",
        clock=lambda: now[0],
        sleep=sleep,
        jitter=lambda: 0.5,
    )

    def answer(request):
        if isinstance(request, SENDS):
            paced.answer = (
                errors.SlowModeWaitError(request, capture=2) if not paced.final else 700
            )

    paced.before_wire = answer
    app.transport.client = paced
    evidence = Watched()
    result = await app.transport.send(description, evidence)
    assert result.pairs == ((901, 700),)
    assert len(paced.final) == 2 and bytes(paced.final[0]) == bytes(paced.final[1])
    assert [value.random_id for value in paced.final] == [901, 901]
    assert evidence.events.count("flood_refused") == 1
    assert paced.request_policy.due("account") == 0
    assert paced.request_policy.due("peer:channel_id:43") == 0


async def test_deliberate_group_send_rechecks_rights_outside_the_callers_read_cache(
    tmp_path,
):
    app, now, _, rpc, description, _ = setup(tmp_path)

    async def sleep(seconds):
        now[0] += seconds
        await asyncio.sleep(0)

    paced = PacedGroupRPC(app, now, description.peer, rpc.metadata)
    paced.manual_handoff = False
    paced.request_policy = RequestPolicy(
        tmp_path / "policy.sqlite3",
        clock=lambda: now[0],
        sleep=sleep,
        jitter=lambda: 0.5,
    )
    app.transport.client = paced
    with observation_cycle():
        await paced(
            functions.channels.GetChannelsRequest([types.InputChannel(42, 100)])
        )
        paced.metadata = entity(
            description.peer,
            now[0],
            banned_rights=types.ChatBannedRights(None, send_messages=True),
        )
        evidence = Evidence()
        with pytest.raises(DeliveryRefused, match="rights"):
            await app.transport.send(description, evidence)
    assert (
        len(
            [
                request
                for request in paced.calls
                if isinstance(request, functions.channels.GetChannelsRequest)
            ]
        )
        == 2
    )
    assert not paced.final and "exposed" not in evidence.events


@pytest.mark.parametrize(
    "error",
    [
        errors.ChatSendPlainForbiddenError(None),
        errors.ChatWriteForbiddenError(None),
        errors.UserBannedInChannelError(None),
        errors.RPCError(None, "ALLOW_PAYMENT_REQUIRED_2", 403),
    ],
)
async def test_native_permission_and_paid_refusals_are_definite_but_access_loss_is_retained(
    tmp_path, error
):
    app, _, key, rpc, description, _ = setup(tmp_path)
    rpc.answer = error
    evidence = Evidence()
    with pytest.raises(DeliveryRefused):
        await app.transport.send(description, evidence)
    assert evidence.refusals and len(rpc.final) == 1
    assert app.store.groups.get(key).access == (
        "lost" if isinstance(error, errors.UserBannedInChannelError) else "available"
    )


@pytest.mark.parametrize(
    "error", [TimeoutError("authored lost answer"), errors.RandomIdDuplicateError(None)]
)
async def test_uncertain_group_receipt_does_not_become_a_definite_refusal(
    tmp_path, error
):
    app, _, _, rpc, description, _ = setup(tmp_path)
    rpc.answer = error
    evidence = Evidence()
    with pytest.raises(type(error)):
        await app.transport.send(description, evidence)
    assert evidence.refusals == [] and evidence.events[-1] == "exposed"


async def test_exact_group_reply_material_author_change_is_refused_without_retargeting(
    tmp_path,
):
    app, _, _, rpc, description, _ = setup(tmp_path)
    rpc.native.from_id = types.PeerChannel(42)
    evidence = Evidence()
    with pytest.raises(Refused, match="reply source changed"):
        await app.transport.send(description, evidence)
    assert rpc.final == [] and "exposed" not in evidence.events
    assert description.reply_to == 1


async def test_new_native_rights_in_sender_choices_cannot_bless_old_metadata_proof(
    tmp_path,
):
    app, now, key, rpc, description, _ = setup(tmp_path)
    before = app.store.groups.get(key).access_revision
    rpc.send_as_metadata = [
        entity(
            description.peer,
            now[0],
            default_banned_rights=types.ChatBannedRights(None, send_plain=True),
        )
    ]
    evidence = Evidence()
    with pytest.raises(Refused, match="rights changed"):
        await app.transport.send(description, evidence)
    assert app.store.groups.get(key).access_revision == before
    assert not rpc.final and "exposed" not in evidence.events


async def test_group_native_read_is_refused_without_a_reader_or_membership_query(
    tmp_path,
):
    app, _, _, rpc, _, _ = setup(tmp_path)
    with pytest.raises(Refused, match="Group native Read"):
        await app.transport.read("channel:42", 1)
    assert rpc.calls == []


@pytest.mark.parametrize("peer", ["chat:42", "channel:42"])
async def test_deliberate_group_media_download_uses_only_the_current_known_source(
    tmp_path, peer
):
    app, _, key, rpc, _, _ = setup(tmp_path, peer)
    rpc.native.media = types.MessageMediaPhoto(
        photo=types.Photo(91, 100, b"authored", datetime.now(UTC), [], 2)
    )
    normalized = await app.transport.normalize(
        rpc.native,
        observed=app.store.clock(),
        entities={"user:7": types.User(id=7, first_name="Alice")},
    )
    app.sync.ingest(normalized)
    current = app.store.message(key, 1)
    destination = tmp_path / "download"
    destination.touch(mode=0o600)
    await app.transport.download(
        peer, 1, destination, max_bytes=1024, expected_revision=current.revision
    )
    assert destination.read_bytes() == b"authored-media"
    assert len(rpc.calls) == 1 and isinstance(
        rpc.calls[0],
        (functions.channels.GetMessagesRequest, functions.messages.GetMessagesRequest),
    )
    assert [value.id for value in rpc.calls[0].id] == [1]


@pytest.mark.parametrize(
    "damage", ["lost", "changed", "expired", "unproved", "unbounded", "during-part"]
)
async def test_group_network_media_stops_on_lost_access_or_stale_material_and_never_expands_history(
    tmp_path, damage
):
    app, now, key, rpc, _, _ = setup(tmp_path)
    rpc.native.media = types.MessageMediaPhoto(
        photo=types.Photo(91, 100, b"authored", datetime.now(UTC), [], 2)
    )
    normalized = await app.transport.normalize(
        rpc.native,
        observed=now[0],
        entities={"user:7": types.User(id=7, first_name="Alice")},
    )
    app.sync.ingest(normalized)
    revision = app.store.message(key, 1).revision
    if damage == "lost":
        app.store.groups.observe(
            key,
            access="lost",
            supported=True,
            reason="authored-loss",
            observed_at=now[0],
        )
    elif damage == "changed":
        rpc.native.from_id = types.PeerChannel(42)
    elif damage == "expired":
        now[0] += 30 * 86400 + 1
    elif damage == "unproved":
        app.store.groups.withdraw_source(normalized.key)
    elif damage == "unbounded":
        revision = None
    else:
        rpc.part_hook = lambda: app.store.groups.observe(
            key,
            access="lost",
            supported=True,
            reason="authored-loss",
            observed_at=now[0],
        )
    destination = tmp_path / "download"
    destination.touch(mode=0o600)
    with pytest.raises(Refused):
        await app.transport.download(
            "channel:42", 1, destination, max_bytes=1024, expected_revision=revision
        )
    assert destination.read_bytes() == b""
    assert all(
        isinstance(value, functions.channels.GetMessagesRequest) for value in rpc.calls
    )
