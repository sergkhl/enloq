"""Exercise real native request construction against an in-memory RPC peer."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from enloq.storage.send_operations import not_sent
from enloq.transport.telethon import PersonalTransport
from telethon import functions, types

from backend.tests.test_rebuild import attach, edit, fixture


@pytest.mark.parametrize("count", [1, 3])
async def test_native_single_and_grouped_media_snapshot_receipts(tmp_path, count):
    app = fixture(tmp_path)
    for _ in range(count):
        attach(app)
    draft = edit(app, "One shared caption")
    operation = app.store.send_operations.prepare(
        "1/user:42", draft["revision"], snapshot=app.attachments.snapshot
    )
    calls = []

    class Client:
        def is_connected(self):
            return True

        async def get_input_entity(self, peer):
            assert peer.user_id == 42
            return types.InputPeerUser(42, 123)

        async def upload_file(self, path, **kwargs):
            calls.append(("upload", kwargs))
            return types.InputFile(
                id=len(calls), parts=1, name="image.png", md5_checksum="authored"
            )

        async def __call__(self, request):
            calls.append(request)
            if isinstance(request, functions.help.GetConfigRequest):
                return SimpleNamespace(caption_length_max=1024)
            if isinstance(request, functions.messages.UploadMediaRequest):
                assert isinstance(request.media, types.InputMediaUploadedPhoto)
                return SimpleNamespace(
                    photo=types.Photo(
                        id=len(calls),
                        access_hash=123,
                        file_reference=b"authored",
                        date=datetime.now(UTC),
                        sizes=[],
                        dc_id=2,
                    )
                )
            if isinstance(request, functions.messages.SendMultiMediaRequest):
                assert [m.message for m in request.multi_media] == [
                    "One shared caption"
                ] + [""] * (count - 1)
                assert [m.random_id for m in request.multi_media] == operation[
                    "random_ids"
                ]
                assert all(
                    isinstance(m.media, types.InputMediaPhoto)
                    for m in request.multi_media
                )
                return SimpleNamespace(
                    updates=[
                        types.UpdateMessageID(id=100 + i, random_id=m.random_id)
                        for i, m in enumerate(request.multi_media)
                    ]
                )
            assert isinstance(request, functions.messages.SendMediaRequest)
            assert (
                request.message == "One shared caption"
                and request.random_id == operation["random_ids"][0]
            )
            return SimpleNamespace(
                updates=[types.UpdateMessageID(id=100, random_id=request.random_id)]
            )

    transport = object.__new__(PersonalTransport)
    transport.account_id = 1
    transport.store = app.store
    transport.client = Client()
    transport.verified = True
    transport.bind_receipts(app.store.send_operations.record_receipts)
    assert app.store.send_operations.claim(
        operation["id"]
    )  # a send is handed off only once claimed
    result = await transport.send(
        app.store.send_operations.description(operation["id"]),
        app.store.send_operations.evidence(operation["id"]),
    )
    # The hand-off ledger records that the final request may have reached Telegram.
    assert len(result.pairs) == count and not not_sent(
        app.store.send_operations.get(operation["id"])
    )
    checked = app.store.send_operations.check_receipts(operation["id"])
    assert checked.receipts == result and not checked.needs_refresh
    assert (
        len(
            [
                c
                for c in calls
                if isinstance(
                    c,
                    (
                        functions.messages.SendMediaRequest,
                        functions.messages.SendMultiMediaRequest,
                    ),
                )
            ]
        )
        == 1
    )
    assert len(
        [c for c in calls if isinstance(c, functions.messages.UploadMediaRequest)]
    ) == (count if count > 1 else 0)


@pytest.mark.parametrize(
    "rpc",
    [
        functions.upload.SaveFilePartRequest(file_id=1, file_part=0, bytes=b"authored"),
        functions.messages.SendMultiMediaRequest(
            peer=types.InputPeerUser(42, 0),
            multi_media=[
                types.InputSingleMedia(
                    types.InputMediaPhoto(types.InputPhoto(1, 1, b"authored")),
                    "",
                    random_id=1,
                )
            ],
        ),
    ],
)
async def test_upload_and_album_rpcs_are_bounded_and_not_transiently_resampled(
    tmp_path, rpc
):
    from enloq.transport.mtproto import RequestPolicy

    policy = RequestPolicy(tmp_path / "policy.sqlite3")
    calls = []

    async def fail():
        calls.append(1)
        raise TimeoutError()

    with pytest.raises(TimeoutError):
        await policy.execute(rpc, fail)
    assert len(calls) == 1


@pytest.mark.parametrize("damage", ["bytes", "symlink", "missing"])
async def test_immutable_images_are_revalidated_before_native_upload(tmp_path, damage):
    from dataclasses import FrozenInstanceError
    from io import BytesIO

    from enloq.domain.models import Refused
    from PIL import Image

    app = fixture(tmp_path)
    attach(app)
    draft = edit(app, "Reviewed caption")
    operation = app.store.send_operations.prepare(
        "1/user:42", draft["revision"], snapshot=app.attachments.snapshot
    )
    description = app.store.send_operations.description(operation["id"])
    with pytest.raises(FrozenInstanceError):
        description.images[0].sha256 = "changed"
    edit(app, "Later caption")
    assert description.text == "Reviewed caption"
    path = description.images[0].path
    if damage == "bytes":
        data = BytesIO()
        Image.new("RGB", (12, 12), "red").save(data, format="PNG")
        path.write_bytes(data.getvalue())
    else:
        target = path.with_suffix(".copy")
        path.rename(target)
        if damage == "symlink":
            path.symlink_to(target)

    class Client:
        def is_connected(self):
            return True

        async def get_input_entity(self, peer):
            pytest.fail("Invalid image must be rejected before native work")

    transport = object.__new__(PersonalTransport)
    transport.account_id, transport.client, transport.verified = 1, Client(), True
    assert app.store.send_operations.claim(operation["id"])
    with pytest.raises(Refused, match="Draft image"):
        await transport.send(
            description, app.store.send_operations.evidence(operation["id"])
        )


@pytest.mark.parametrize("count", [1, 2])
async def test_telegrams_definite_answer_while_images_upload_is_named(tmp_path, count):
    from enloq.domain.models import DeliveryRefused
    from telethon import errors

    app = fixture(tmp_path)
    for _ in range(count):
        attach(app)
    draft = edit(app, "Authored caption")
    operation = app.store.send_operations.prepare(
        "1/user:42", draft["revision"], snapshot=app.attachments.snapshot
    )
    calls = []

    class Client:
        def is_connected(self):
            return True

        async def get_input_entity(self, peer):
            return types.InputPeerUser(42, 123)

        async def upload_file(self, path, **kwargs):
            if count == 1:
                raise errors.FilePartsInvalidError(None)
            return types.InputFile(1, 1, "image.png", "authored")

        async def __call__(self, request):
            calls.append(request)
            if isinstance(request, functions.help.GetConfigRequest):
                return SimpleNamespace(caption_length_max=1024)
            assert isinstance(request, functions.messages.UploadMediaRequest)
            raise errors.PhotoInvalidDimensionsError(request)

    transport = object.__new__(PersonalTransport)
    transport.account_id = 1
    transport.store = app.store
    transport.client = Client()
    transport.verified = True
    assert app.store.send_operations.claim(operation["id"])
    with pytest.raises(DeliveryRefused) as refusal:
        await transport.send(
            app.store.send_operations.description(operation["id"]),
            app.store.send_operations.evidence(operation["id"]),
        )
    assert str(refusal.value) == (
        "Telegram refused this image (FILE_PARTS_INVALID). Nothing was sent."
        if count == 1
        else "Telegram refused these images (PHOTO_INVALID_DIMENSIONS)."
        " Nothing was sent."
    )
    assert not any(
        isinstance(
            call,
            (
                functions.messages.SendMediaRequest,
                functions.messages.SendMultiMediaRequest,
            ),
        )
        for call in calls
    )
