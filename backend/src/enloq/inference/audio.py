from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from decimal import Decimal
from pathlib import Path

import httpx

from ..config import ROOT, private_read
from ..domain.models import Refused, Unavailable
from ..media_tools import FFMPEG, FFPROBE
from .base import ProviderRefused
from .openrouter import PRIVATE, proxy_endpoint, proxy_key
from .runner import bounded_process

WHISPER = "mlx-community/whisper-large-v3-turbo"
# ADR 39: the personal instance transcribes on the owner's VPS, through the
# proxy's speech route: faster-whisper with an int8 conversion of the same
# large-v3-turbo model, denoising off (ADR 37 Q16).
PROXY_WHISPER = "whisper-large-v3-turbo"
PROXY_REVISION = "dropbox-dash/faster-whisper-large-v3-turbo@0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf"
# The service transcribes one request at a time; ten minutes of speech is the
# longest accepted.
PROXY_SECONDS = 600
# The VPS takes about half a note's length (110 s in 60 s on 2026-10-15). Longer
# notes go to the hosted model first. The VPS gets a minute more than the note's
# own length before it counts as not answering.
LONG_SECONDS = 120
LOCAL_MARGIN = 60
# The fallback the owner chose on 2026-10-15, through the proxy's pass-through
# to OpenRouter's speech-to-text.
HOSTED_WHISPER = "elevenlabs/scribe-v2"
HOSTED_PROVIDER = "elevenlabs"
LISTING = "https://openrouter.ai/api/v1"
# Its list price per second of audio once the launch discount ends on
# 2026-10-19 ($0.22 an hour). A higher listed rate sends no audio.
HOSTED_RATE = "0.00006111111111111"
USAGE = {"seconds", "cost", "cost_details", "is_byok"}
# Scribe names languages in ISO 639-3; Whisper, and everything stored so far,
# in ISO 639-1.
LANGUAGES = {
    "ara": "ar",
    "deu": "de",
    "eng": "en",
    "fra": "fr",
    "hin": "hi",
    "ind": "id",
    "ita": "it",
    "jpn": "ja",
    "kor": "ko",
    "pol": "pl",
    "por": "pt",
    "rus": "ru",
    "spa": "es",
    "tha": "th",
    "tur": "tr",
    "ukr": "uk",
    "vie": "vi",
    "zho": "zh",
}


def checked(path: Path):
    if (
        path.is_symlink()
        or not path.is_file()
        or not 0 < path.stat().st_size <= 25_000_000
    ):
        raise Refused("Audio file is missing or exceeds 25 MB")


def environment():
    return {
        "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
        "HOME": os.environ["HOME"],
        "HF_HUB_OFFLINE": "1",
        "TOKENIZERS_PARALLELISM": "false",
    }


async def duration(path: Path, env):
    probe, _ = await bounded_process(
        [
            FFPROBE,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        stdin=b"",
        cwd=path.parent,
        env=env,
        timeout=20,
    )
    try:
        measured = json.loads(probe)["format"]["duration"]
        if isinstance(measured, bool):
            raise ValueError
        seconds = float(measured)
    except (ValueError, KeyError, TypeError, OverflowError):
        raise Refused("Audio duration unavailable") from None
    if not 0 < seconds <= 600:
        raise Refused("Audio exceeds ten minutes or has invalid duration")
    return seconds


class LocalWhisper:
    """The MLX worker on this Mac, for offline fixtures and scripts/check-voice.py."""

    def __init__(self, root=ROOT):
        self.root = root
        self.python = root / "audio/.venv/bin/python"
        self.model = root / ".local/models/whisper"
        self.lock = asyncio.Lock()

    async def transcribe(self, path: Path, *, on_start=None):
        checked(path)
        if not self.python.is_file() or not (self.model / "provenance.json").is_file():
            raise Unavailable(
                "Local Whisper is not installed; run scripts/install-audio"
            )
        provenance = json.loads(private_read(self.model / "provenance.json"))
        if provenance.get("model") != WHISPER:
            raise Refused("Unqualified local speech model")
        env = environment()
        seconds = await duration(path, env)
        # The application holds this instance's shared lock and rechecks account
        # admission before entering this started transcription stage.
        if on_start is not None:
            on_start(seconds)
        stdout, _ = await bounded_process(
            [
                str(self.python),
                str(self.root / "scripts/transcribe_worker.py"),
                str(path),
                str(self.model),
            ],
            stdin=b"",
            cwd=path.parent,
            env=env,
            timeout=600,
            mutex=self.root / ".local/whisper-worker.lock",
        )
        result = json.loads(stdout)
        if not isinstance(result.get("text"), str):
            raise Unavailable("Local transcript format invalid")
        return {
            **result,
            "model": WHISPER,
            "model_revision": provenance["revision"],
            "duration_seconds": seconds,
        }


class SpeechOutage(Unavailable):
    """The VPS speech service could not answer; the hosted model may."""


def hosted_url(endpoint):
    """OpenRouter's speech-to-text, on the proxy's pass-through beside /v1."""
    return endpoint.removesuffix("/v1") + "/openrouter/api/v1/audio/transcriptions"


async def hosted_rate(client):
    """The exact Scribe / ElevenLabs route and its listed per-second rate."""
    response = await client.get(f"{LISTING}/models/{HOSTED_WHISPER}/endpoints")
    if response.status_code != 200:
        raise ProviderRefused(f"Speech route listing HTTP {response.status_code}")
    data = response.json()["data"]
    routes = [
        r
        for r in data["endpoints"]
        if r.get("tag") == HOSTED_PROVIDER and r.get("status") == 0
    ]
    if data["id"] != HOSTED_WHISPER or len(routes) != 1:
        raise ProviderRefused("Exact Scribe / ElevenLabs route unavailable")
    rate = Decimal(str(routes[0]["pricing"]["prompt"]))
    if not rate.is_finite() or rate < 0:
        raise Unavailable("Provider rates cannot be bounded")
    if rate > Decimal(HOSTED_RATE):
        raise ProviderRefused("Scribe route rate exceeds the adopted rate")
    return time.time()


class ProxyWhisper:
    """Speech-to-text through the owner's LiteLLM proxy (ADR 39). Its own
    faster-whisper service on the VPS comes first: no model provider sees that
    audio. A note longer than two minutes, or one the VPS service cannot answer,
    goes to ElevenLabs Scribe v2 through OpenRouter, a paid call that Media
    admits and receipts against the Daily Budget through ``ledger``."""

    paid = True

    def __init__(self, settings, *, client=None, verified=None):
        # The endpoint and key are read per call, so a missing one fails a
        # transcription, not the service start.
        self.settings, self.verified = settings, verified
        self.lock = asyncio.Lock()
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(PROXY_SECONDS, connect=30),
            follow_redirects=False,
            trust_env=False,
        )

    async def transcribe(self, path: Path, *, on_start=None, ledger=None):
        checked(path)
        endpoint, key = proxy_endpoint(self.settings), proxy_key(self.settings)
        env = environment()
        seconds = await duration(path, env)
        # As for the local worker: admission is rechecked before this stage.
        if on_start is not None:
            on_start(seconds)
        if ledger is not None and seconds > LONG_SECONDS:
            try:
                return await self.hosted(path, endpoint, key, seconds, env, ledger)
            except Exception:
                pass  # Budget, rate or provider: the VPS still answers, slowly.
            return await self.local(path, endpoint, key, seconds)
        try:
            return await self.local(path, endpoint, key, seconds)
        except SpeechOutage:
            if ledger is None:
                raise
        return await self.hosted(path, endpoint, key, seconds, env, ledger)

    async def local(self, path, endpoint, key, seconds):
        try:
            response = await self.client.post(
                f"{endpoint}/audio/transcriptions",
                headers={"Authorization": f"Bearer {key}", **PRIVATE},
                data={"model": PROXY_WHISPER, "response_format": "verbose_json"},
                files={"file": (path.name, path.read_bytes())},
                timeout=httpx.Timeout(
                    min(seconds + LOCAL_MARGIN, PROXY_SECONDS), connect=30
                ),
            )
        except httpx.TransportError:
            raise SpeechOutage("Speech service unreachable") from None
        if response.status_code >= 500:
            raise SpeechOutage(f"Speech service HTTP {response.status_code}")
        if response.status_code != 200:
            raise Unavailable(f"Speech service HTTP {response.status_code}")
        return transcript(response.json(), PROXY_WHISPER, PROXY_REVISION, seconds)

    async def hosted(self, path, endpoint, key, seconds, env, ledger):
        if self.verified is None or time.time() - self.verified > 86400:
            self.verified = await hosted_rate(self.client)
        # One known container for every source, video circles included.
        audio, _ = await bounded_process(
            [
                FFMPEG,
                "-v",
                "error",
                "-i",
                str(path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "libopus",
                "-b:a",
                "24k",
                "-f",
                "ogg",
                "pipe:1",
            ],
            stdin=b"",
            cwd=path.parent,
            env=env,
            timeout=120,
            stdout_limit=8 * 1024 * 1024,
        )
        ledger.reserve()
        usage, error, refused = {}, None, False
        try:
            try:
                response = await self.client.post(
                    hosted_url(endpoint),
                    headers={"Authorization": f"Bearer {key}", **PRIVATE},
                    json={
                        "model": HOSTED_WHISPER,
                        "input_audio": {
                            "data": base64.b64encode(audio).decode(),
                            "format": "ogg",
                        },
                        "response_format": "verbose_json",
                    },
                )
            except httpx.TransportError:
                error = "Hosted speech unreachable"
                raise Unavailable(error) from None
            # OpenRouter bills none of these: rate, credit and upstream refusals.
            if response.status_code in (402, 429, 502):
                error, refused = f"Hosted speech HTTP {response.status_code}", True
                raise ProviderRefused(error)
            if response.status_code != 200:
                error = f"Hosted speech HTTP {response.status_code}"
                raise Unavailable(error)
            result = response.json()
            usage = {k: v for k, v in (result.get("usage") or {}).items() if k in USAGE}
            return transcript(result, HOSTED_WHISPER, None, seconds)
        except Exception as failure:
            error = error or str(failure) or type(failure).__name__
            raise
        finally:
            ledger.settle(
                HOSTED_WHISPER, HOSTED_PROVIDER, usage, refused=refused, error=error
            )


def transcript(result, model, revision, seconds):
    if not isinstance(result.get("text"), str):
        raise Unavailable("Speech transcript format invalid")
    language = result.get("language")
    if isinstance(language, str):
        language = LANGUAGES.get(language, language)
    return {
        "text": result["text"],
        "language": language if isinstance(language, str) else None,
        "model": model,
        "model_revision": revision,
        "duration_seconds": seconds,
    }
