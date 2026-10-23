"""Android push to the Enloq app through Firebase Cloud Messaging (ADR 38).
Each push is an HTTP v1 data-only message carrying Web
Push's opaque payload as one JSON string, sent for a service account whose
private key exists only on this host. Tokens, payloads and credentials never
enter logs."""

import base64
import json
import re
import threading
import time

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from ..domain.models import Refused
from .push import NoRedirectSession

# FCM registration tokens are opaque URL-safe strings, about 160 characters.
TOKEN = re.compile(r"[A-Za-z0-9_:-]{32,4096}")
SCOPE = "https://www.googleapis.com/auth/firebase.messaging"
OAUTH = "https://oauth2.googleapis.com/token"
# Errors that mean the token itself is finished: the app was uninstalled, its
# data cleared or the token belongs to another project.
RETIRED = {"UNREGISTERED", "SENDER_ID_MISMATCH"}


def validate_token(settings, token):
    """The phone's FCM registration token, in its exact form."""
    if not settings.fcm_project:
        raise Refused("Phone push is not set up on this server")
    if not isinstance(token, str) or not TOKEN.fullmatch(token):
        raise Refused("Unsupported phone push token")
    return token


def segment(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


class FcmTransport:
    def __init__(self, settings):
        self.settings = settings
        self.lock = threading.Lock()
        self.access = None

    def assertion(self, now):
        key = serialization.load_pem_private_key(
            self.settings.secret("FCM_KEY_REF").encode(), None
        )
        header = {"alg": "RS256", "typ": "JWT"}
        if self.settings.fcm_key_id:
            header["kid"] = self.settings.fcm_key_id
        claims = {
            "iss": self.settings.fcm_account,
            "scope": SCOPE,
            "aud": OAUTH,
            "iat": now,
            "exp": now + 3600,
        }
        signing = f"{segment(header)}.{segment(claims)}"
        signature = key.sign(signing.encode(), padding.PKCS1v15(), hashes.SHA256())
        return f"{signing}.{segment(signature)}"

    def credential(self, session):
        """A cached OAuth access token, renewed a minute before it lapses."""
        with self.lock:
            now = int(time.time())
            if self.access and self.access[1] > now + 60:
                return self.access[0]
            answer = session.post(
                OAUTH,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": self.assertion(now),
                },
                timeout=10,
            )
            if answer.status_code != 200:
                raise requests.RequestException("token exchange refused")
            body = answer.json()
            self.access = (body["access_token"], now + int(body.get("expires_in", 0)))
            return self.access[0]

    def send(self, token, payload, ttl):
        try:
            token = validate_token(self.settings, token)
            with NoRedirectSession() as session:
                answer = session.post(
                    f"https://fcm.googleapis.com/v1/projects/{self.settings.fcm_project}/messages:send",
                    json={
                        "message": {
                            "token": token,
                            "data": {"payload": json.dumps(payload)},
                            "android": {
                                "priority": "HIGH",
                                "ttl": f"{max(0, int(ttl))}s",
                            },
                        }
                    },
                    headers={"Authorization": f"Bearer {self.credential(session)}"},
                    timeout=10,
                )
        except Refused:
            return "refused"
        except (requests.RequestException, OSError, ValueError, KeyError):
            return "retry"
        status = answer.status_code
        if 200 <= status < 300:
            return "sent"
        if status == 401:
            # A revoked or lapsed access token: mint a new one next time.
            with self.lock:
                self.access = None
            return "retry"
        try:
            details = answer.json()["error"].get("details", [])
            codes = {item.get("errorCode") for item in details}
        except (ValueError, KeyError, AttributeError, TypeError):
            codes = set()
        if status == 404 or codes & RETIRED:
            return "expired"
        return "retry" if status == 429 or status >= 500 else "refused"


class FakeFcmTransport:
    """Offline default; tests read what would have been sent."""

    def __init__(self, settings):
        self.settings = settings
        self.sent = []
        self.outcomes = []

    def send(self, token, payload, ttl):
        validate_token(self.settings, token)
        self.sent.append((token, payload, ttl))
        return self.outcomes.pop(0) if self.outcomes else "sent"
