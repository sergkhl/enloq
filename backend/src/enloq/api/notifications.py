from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from pydantic import Field, ValidationError
from starlette.concurrency import run_in_threadpool

from ..application.notification_sounds import MAX_AUDIO_BASE64, SoundPreset
from ..domain.models import Model, Refused


class PushKeys(Model):
    p256dh: str = Field(min_length=80, max_length=128, repr=False)
    auth: str = Field(min_length=16, max_length=64, repr=False)


class Subscription(Model):
    endpoint: str = Field(min_length=1, max_length=2048, repr=False)
    keys: PushKeys
    expirationTime: float | None = None


class NotificationPreferences(Model):
    sound: bool | None = None
    push: Literal[False] | None = None
    sound_preset: SoundPreset | None = None


class NotificationSoundUpload(Model):
    name: str = Field(min_length=1, max_length=1024, repr=False)
    data: str = Field(min_length=1, max_length=MAX_AUDIO_BASE64, repr=False)


MAX_SOUND_REQUEST_BYTES = MAX_AUDIO_BASE64 + 8192


async def bounded_sound_upload(request: Request) -> NotificationSoundUpload:
    # No typed body argument: FastAPI must not buffer an unbounded JSON body
    # before this endpoint can count its actual streamed bytes.
    lengths = request.headers.getlist("content-length")
    declared = None
    if lengths:
        length = lengths[0].strip()
        if len(lengths) != 1 or not length.isascii() or not length.isdigit():
            raise HTTPException(400, "The audio upload has an invalid length")
        if len(length) > 20:
            raise HTTPException(413, "The audio upload is too large")
        declared = int(length)
        if declared > MAX_SOUND_REQUEST_BYTES:
            raise HTTPException(413, "The audio upload is too large")
    encoded = bytearray()
    async for chunk in request.stream():
        if len(encoded) + len(chunk) > MAX_SOUND_REQUEST_BYTES:
            raise HTTPException(413, "The audio upload is too large")
        encoded.extend(chunk)
    if declared is not None and len(encoded) != declared:
        raise HTTPException(400, "The audio upload has an invalid length")
    try:
        return NotificationSoundUpload.model_validate_json(encoded)
    except ValidationError:
        pass
    # Keep the normal fixed validation response without input fields or a
    # chained validation error that includes the uploaded data.
    raise RequestValidationError([])


class NotificationIdentity(Model):
    id: str = Field(pattern=r"^[a-f0-9]{32}$")


class NotificationTarget(Model):
    """What a clicked notification opens for the browser it was delivered to."""

    kind: Literal[
        "conversation", "test", "people", "ai", "unavailable", "reminders", "reminder"
    ]
    account_id: int | None = None
    peer: str | None = None


class NotificationStatus(Model):
    browser: str
    sound: bool
    push: bool
    destination: str | None
    failure: str | None
    public_key: str
    sound_preset: SoundPreset = "harp"
    sound_file_name: str | None = None
    sound_revision: str | None = None


class NotificationAttentionStatus(Model):
    events: list[str]
    sound: bool
    sound_preset: SoundPreset = "harp"
    sound_file_name: str | None = None
    sound_revision: str | None = None


class Attention(Model):
    tab: str = Field(pattern=r"^[a-f0-9-]{32,36}$")
    account_id: int | None = Field(default=None, gt=0)
    peer: str | None = Field(default=None, pattern=r"^user:[1-9][0-9]*$")
    focused: bool
    audio: bool
    push_permission: Literal["granted", "denied", "default", "unsupported"] | None = (
        None
    )


class ChimeClaim(NotificationIdentity):
    tab: str = Field(pattern=r"^[a-f0-9-]{32,36}$")


class DeviceToken(Model):
    """The phone's FCM registration token (ADR 38)."""

    token: str = Field(min_length=1, max_length=4096, repr=False)


class DevicePreferences(Model):
    push: Literal[False] | None = None
    sound: bool | None = None


class DeviceNotificationStatus(Model):
    """A phone's own push state (ADR 38 Q5), beside the browser destination."""

    push: bool
    sound: bool
    available: bool
    failure: str | None


def phone(request: Request) -> str:
    if request.state.client_kind != "device":
        raise Refused("Only the Enloq app uses this")
    return request.state.local_session


def notification_routes(delivery):
    router = APIRouter(prefix="/api/notifications")

    @router.get("", response_model=NotificationStatus)
    def status(request: Request):
        return delivery.status(request.state.local_session)

    @router.post("/subscription", response_model=NotificationStatus)
    def subscribe(body: Subscription, request: Request):
        if request.state.client_kind == "device":
            # A phone is its own destination, never the browser's (ADR 38 Q5).
            raise Refused("Phones turn on notifications in the Enloq app")
        return delivery.enable(
            request.state.local_session, body.model_dump(exclude_none=True)
        )

    @router.post("/preferences", response_model=NotificationStatus)
    def preferences(body: NotificationPreferences, request: Request):
        return delivery.preferences(request.state.local_session, **body.model_dump())

    @router.post(
        "/sound",
        response_model=NotificationStatus,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": NotificationSoundUpload.model_json_schema()
                    }
                },
            }
        },
    )
    async def upload_sound(request: Request):
        body = await bounded_sound_upload(request)
        return await run_in_threadpool(
            delivery.upload_sound, request.state.local_session, body.name, body.data
        )

    @router.get("/sound")
    def sound(
        request: Request,
        revision: str = Query(pattern=r"^[a-f0-9]{32}$"),
    ):
        body, mime = delivery.sounds.get(request.state.local_session, revision)
        return Response(
            body,
            media_type=mime,
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    @router.post("/validate")
    def validate(body: NotificationIdentity, request: Request):
        if request.state.client_kind == "device":
            return delivery.devices.validate(request.state.local_session, body.id)
        return delivery.validate(request.state.local_session, body.id)

    @router.post(
        "/open", response_model=NotificationTarget, response_model_exclude_none=True
    )
    def open_notification(body: NotificationIdentity, request: Request):
        if request.state.client_kind == "device":
            return delivery.devices.target(request.state.local_session, body.id)
        return delivery.target(request.state.local_session, body.id)

    @router.post("/test")
    async def test(request: Request):
        return await delivery.test(request.state.local_session)

    @router.post("/attention", response_model=NotificationAttentionStatus)
    def attention(body: Attention, request: Request):
        return delivery.attention.update(
            request.state.local_session, **body.model_dump()
        )

    @router.post("/claim")
    def claim(body: ChimeClaim, request: Request):
        return delivery.attention.claim(request.state.local_session, body.tab, body.id)

    @router.get("/device", response_model=DeviceNotificationStatus)
    def device_status(request: Request):
        return delivery.devices.status(phone(request))

    @router.post("/device", response_model=DeviceNotificationStatus)
    def device_enable(body: DeviceToken, request: Request):
        return delivery.devices.enable(phone(request), body.token)

    @router.post("/device/preferences", response_model=DeviceNotificationStatus)
    def device_preferences(body: DevicePreferences, request: Request):
        return delivery.devices.preferences(phone(request), **body.model_dump())

    @router.post("/device/test")
    async def device_test(request: Request):
        return await delivery.devices.test(phone(request))

    return router
