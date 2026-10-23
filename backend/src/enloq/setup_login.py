"""Interactive code delivery diagnostics without exposing login secrets."""

import asyncio
import getpass
import json
import math
import time

from telethon import errors, utils
from telethon.tl import functions, types

from .domain.models import Refused
from .transport.mtproto import (
    MAX_ATTEMPTS,
    REQUEST_TIMEOUT,
    MTProtoAttemptLimit,
    PacedTelegramClient,
    _Attempts,
)
from .transport.onboarding import IdentityOnlyStartup


class PersonalLoginClient(IdentityOnlyStartup, PacedTelegramClient):
    """Allow one confirmed login redirect without enabling general RPC retries."""

    async def _call(self, sender, request, ordered=False, flood_sleep_threshold=None):
        if not isinstance(
            request, (functions.auth.SendCodeRequest, functions.auth.ResendCodeRequest)
        ):
            return await super()._call(sender, request, ordered=ordered)
        attempts = _Attempts()
        for redirect in range(2):
            try:
                return await super()._call(
                    sender, request, ordered=ordered, attempts=attempts
                )
            except (errors.PhoneMigrateError, errors.NetworkMigrateError) as error:
                if (
                    redirect
                    or not isinstance(error.new_dc, int)
                    or error.new_dc <= 0
                    or error.new_dc == self.session.dc_id
                    or await self.is_user_authorized()
                ):
                    raise
                if attempts.used >= MAX_ATTEMPTS:
                    raise MTProtoAttemptLimit(
                        "Login request attempts exhausted before data-center handoff"
                    ) from None
                print(
                    "Telegram requested another data center; following its login redirect once."
                )
                # IdentityOnlyStartup suppresses automatic reconnect/catch-up
                # while the existing owner deliberately moves this session.
                async with asyncio.timeout(REQUEST_TIMEOUT):
                    await self._switch_dc(error.new_dc)
                sender = self._sender
                # Reuse the exact request, wire-attempt budget, and recovery
                # allowance. A redirect is a confirmed refusal, not delivery.


# Only these fixed labels leave the response object. In particular, never print
# its phone_code_hash, email pattern, Fragment URL, receipt, or code prefix.
_DELIVERY = {
    types.auth.SentCodeTypeApp: "telegram_app",
    types.auth.SentCodeTypeSms: "sms",
    types.auth.SentCodeTypeCall: "voice_call",
    types.auth.SentCodeTypeFlashCall: "flash_call",
    types.auth.SentCodeTypeMissedCall: "missed_call",
    types.auth.SentCodeTypeEmailCode: "email",
    types.auth.SentCodeTypeSetUpEmailRequired: "email_setup_required",
    types.auth.SentCodeTypeFirebaseSms: "firebase_sms",
    types.auth.SentCodeTypeFragmentSms: "fragment",
    types.auth.SentCodeTypeSmsWord: "sms_word",
    types.auth.SentCodeTypeSmsPhrase: "sms_phrase",
}
_NEXT_DELIVERY = {
    types.auth.CodeTypeSms: "sms",
    types.auth.CodeTypeCall: "voice_call",
    types.auth.CodeTypeFlashCall: "flash_call",
    types.auth.CodeTypeMissedCall: "missed_call",
    types.auth.CodeTypeFragmentSms: "fragment",
}
_CODE_HELP = {
    "telegram_app": (
        "Check the Telegram service chat on a device already signed in to this phone number."
    ),
    "sms": "Telegram selected SMS delivery. Check this number's SMS inbox.",
    "voice_call": "Telegram selected a voice call. Enter the code read out by the call.",
    "flash_call": "Telegram selected a flash call. The calling phone number is the code.",
    "missed_call": "Telegram selected a missed call. Enter the code from the caller's final digits.",
    "sms_word": "Telegram selected an SMS word. Enter the whole word as the code.",
    "sms_phrase": "Telegram selected an SMS phrase. Enter the whole phrase as the code.",
}


def delivery_metadata(sent):
    if not isinstance(sent, types.auth.SentCode):
        if isinstance(sent, types.auth.SentCodePaymentRequired):
            raise Refused(
                "Telegram requires an official-app payment flow; terminal code login cannot complete it."
            )
        raise Refused("Unexpected Telegram login response; no code requested locally")
    return {
        "delivery": _DELIVERY.get(type(sent.type), "unsupported"),
        "next_delivery": (
            _NEXT_DELIVERY.get(type(sent.next_type), "unsupported")
            if sent.next_type is not None
            else None
        ),
        # A missing timeout is unknown, not permission for an immediate resend.
        "retry_after_seconds": (
            max(0, sent.timeout) if isinstance(sent.timeout, int) else None
        ),
    }


async def request_login_code(client, phone):
    phone = utils.parse_phone(phone)
    if not phone:
        raise Refused("Enter a Telegram phone number with its country code")
    sent = await client.send_code_request(phone)
    resent = False
    while True:
        metadata = delivery_metadata(sent)
        received_at = time.monotonic()
        print(json.dumps(metadata))
        delivery = metadata["delivery"]
        if delivery not in _CODE_HELP:
            raise Refused(
                f"Telegram selected {delivery}, which this terminal flow cannot complete. "
                "Use the official Telegram app or an independently approved login-token flow; "
                "waiting for an SMS or a Telegram message will not complete this challenge."
            )
        print(_CODE_HELP[delivery])
        print(
            "This is Telegram's reported route; receipt on your device is not verified."
        )
        timeout = metadata["retry_after_seconds"]
        can_resend = (
            not resent
            and metadata["next_delivery"] in _CODE_HELP
            and timeout is not None
        )
        if can_resend:
            print(
                f"If no code arrives, press Enter at the hidden code prompt. "
                f"One explicit resend via {metadata['next_delivery']} is available "
                f"after {timeout} seconds."
            )
        else:
            print(
                "No further supported resend is available in this run. Enter without a code exits."
            )
        while True:
            code = getpass.getpass(
                "Telegram login code (hidden; Enter if it did not arrive): "
            ).strip()
            if code:
                return code, sent.phone_code_hash
            # Never pass an empty code to Telethon.sign_in: it would request
            # another code implicitly instead of attempting authentication.
            if not can_resend:
                raise Refused(
                    "No code entered; session retained without requesting another code"
                )
            remaining = math.ceil(received_at + timeout - time.monotonic())
            if remaining > 0:
                print(
                    f"Telegram's resend delay has {remaining} seconds remaining. No request sent."
                )
                continue
            choice = getpass.getpass(
                "Request the offered next delivery method once? Type resend, or Enter to exit (hidden): "
            ).strip()
            if choice != "resend":
                raise Refused("Login stopped; no additional code requested")
            # Use the native resend once. Calling send_code_request again can
            # silently restart an expired challenge inside Telethon.
            resent = True
            sent = await client(
                functions.auth.ResendCodeRequest(phone, sent.phone_code_hash)
            )
            break
