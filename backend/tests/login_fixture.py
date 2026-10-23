"""Authored login simulator, injected only by tests; no Telegram connection."""

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from enloq.config import private_write
from telethon import errors, functions, types


class SyntheticLoginClient:
    saved = {}
    instances = []
    qr_seconds = 60

    def __init__(self, path, *args, **kwargs):
        self.path = path
        self.session = SimpleNamespace(close=self.close_session)
        self.closed = False
        self.connected = False
        self.authorized = self.saved.get(path)
        self.user_id = self.authorized or 2
        self.scan = asyncio.Event()
        self.listening = False
        self.password_required = False
        self.requests = []
        private_write(Path(path), "Authored synthetic authorization")
        self.instances.append(self)

    def close_session(self):
        self.closed = True

    async def connect(self):
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        return bool(self.authorized)

    async def get_me(self):
        return (
            SimpleNamespace(
                id=self.user_id,
                bot=False,
                first_name=f"Demo {self.user_id}",
                last_name="",
                username=f"synthetic_{self.user_id}",
                phone="12025550123",
            )
            if self.authorized
            else None
        )

    async def send_code_request(self, phone):
        self.requests.append("send_code")
        self.user_id = int(phone[-2:]) or 2
        self.password_required = self.user_id == 3
        return types.auth.SentCode(
            types.auth.SentCodeTypeApp(5),
            "synthetic-hash",
            next_type=types.auth.CodeTypeSms(),
            timeout=0,
        )

    async def sign_in(self, *, code=None, password=None, **kwargs):
        self.requests.append("password" if password is not None else "code")
        if code is not None and code != "12345":
            raise errors.PhoneCodeInvalidError(None)
        if password is not None and password != "synthetic-password":
            raise errors.PasswordHashInvalidError(None)
        if self.password_required and password is None:
            raise errors.SessionPasswordNeededError(None)
        self.authorized = self.user_id
        self.saved[self.path] = self.user_id
        return await self.get_me()

    async def __call__(self, request):
        self.requests.append(type(request).__name__)
        if isinstance(request, functions.auth.LogOutRequest):
            self.authorized = None
            self.saved.pop(self.path, None)
            return True
        if isinstance(request, functions.auth.ResendCodeRequest):
            return types.auth.SentCode(types.auth.SentCodeTypeSms(5), "synthetic-next")
        raise AssertionError("Unexpected synthetic request")

    async def qr_login(self, ignored_ids=None):
        self.requests.append("qr")
        client = self

        class QR:
            url = "tg://login?token=AUTHORED-SYNTHETIC-TOKEN"
            expires = datetime.now(UTC) + timedelta(seconds=client.qr_seconds)

            async def wait(self, timeout):
                client.listening = True
                await asyncio.wait_for(client.scan.wait(), timeout)
                if client.password_required:
                    raise errors.SessionPasswordNeededError(None)
                client.authorized = client.user_id
                client.saved[client.path] = client.user_id
                return await client.get_me()

        return QR()
