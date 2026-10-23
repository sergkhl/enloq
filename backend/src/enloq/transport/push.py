"""Encrypted standards Web Push. Endpoints/keys/payloads never enter logs."""

import base64
import json
import threading
from urllib.parse import urlsplit

import requests
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from py_vapid import Vapid
from pywebpush import WebPusher, WebPushException, webpush

from ..config import private_read, private_write
from ..domain.models import Refused


def validate_subscription(subscription):
    try:
        endpoint = urlsplit(subscription["endpoint"])
        host = endpoint.hostname or ""
        allowed = host in {
            "web.push.apple.com",
            "fcm.googleapis.com",
            "updates.push.services.mozilla.com",
        } or host.endswith(".push.apple.com")
        if not (
            allowed
            and endpoint.scheme == "https"
            and endpoint.port in {None, 443}
            and not endpoint.username
            and not endpoint.password
            and not endpoint.fragment
            and endpoint.path.startswith("/")
        ):
            raise ValueError()

        def decode(value):
            return base64.b64decode(
                value + "=" * (-len(value) % 4), altchars=b"-_", validate=True
            )

        ec.EllipticCurvePublicKey.from_encoded_point(
            ec.SECP256R1(), decode(subscription["keys"]["p256dh"])
        )
        if len(decode(subscription["keys"]["auth"])) != 16:
            raise ValueError()
        WebPusher(subscription)
    except Exception:
        raise Refused("Unsupported or invalid browser push subscription") from None


class NoRedirectSession(requests.Session):
    def __init__(self):
        super().__init__()
        self.trust_env = False

    def post(self, url, **kwargs):
        kwargs["allow_redirects"] = False
        return super().post(url, **kwargs)


class PushTransport:
    def __init__(self, settings):
        self.settings = settings
        self.path = settings.data / "push-vapid.pem"
        self.lock = threading.Lock()

    def key(self):
        with self.lock:
            if not self.path.exists():
                key = Vapid()
                key.generate_keys()
                private_write(self.path, key.private_pem().decode())
            return Vapid.from_pem(private_read(self.path).encode())

    def public_key(self):
        raw = self.key().public_key.public_bytes(
            Encoding.X962, PublicFormat.UncompressedPoint
        )
        return base64.urlsafe_b64encode(raw).decode().rstrip("=")

    def send(self, subscription, payload, ttl):
        validate_subscription(subscription)
        try:
            with NoRedirectSession() as session:
                response = webpush(
                    subscription_info=subscription,
                    data=json.dumps(payload, ensure_ascii=False),
                    vapid_private_key=self.key(),
                    vapid_claims={
                        "sub": self.settings.public_origin or "https://127.0.0.1"
                    },
                    content_encoding="aes128gcm",
                    ttl=max(0, int(ttl)),
                    timeout=10,
                    headers={"Urgency": "normal", "Topic": payload["id"]},
                    requests_session=session,
                )
            status = response.status_code
        except WebPushException as error:
            status = error.response.status_code if error.response is not None else 0
        except (requests.RequestException, OSError):
            status = 0
        except Exception:
            return "refused"
        if 200 <= status < 300:
            return "sent"
        if status in {404, 410}:
            return "expired"
        return "retry" if status == 0 or status == 429 or status >= 500 else "refused"


class FakePushTransport:
    """Offline default. Authored tests can inspect their own payloads privately."""

    def __init__(self, settings):
        self.keys = PushTransport(settings)
        self.sent = []
        self.outcomes = []

    def public_key(self):
        return self.keys.public_key()

    def send(self, subscription, payload, ttl):
        self.sent.append((subscription, payload, ttl))
        return self.outcomes.pop(0) if self.outcomes else "sent"
