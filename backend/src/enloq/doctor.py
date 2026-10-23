import argparse
import importlib.metadata
import json
import shutil
import sqlite3
from pathlib import Path

from .config import ROOT, Settings
from .domain.models import Refused
from .inference.audio import PROXY_WHISPER
from .inference.openrouter import LOGICAL_MODEL, PROVIDER, proxy_endpoint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--redacted", action="store_true", required=True)
    parser.parse_args()
    settings = Settings.load("personal")
    refs = {
        key: {
            "present": Path(value).is_file(),
            "private": Path(value).is_file()
            and Path(value).stat().st_mode & 0o077 == 0,
        }
        for key, value in settings.refs.items()
        if key.endswith("_REF")
    }
    registry = ROOT / ".local/personal/accounts.sqlite3"
    accounts = []
    if registry.is_file():
        with sqlite3.connect(f"file:{registry}?mode=ro", uri=True) as db:
            accounts = [
                dict(state=state, count=count)
                for state, count in db.execute(
                    "SELECT state,count(*) FROM accounts GROUP BY state"
                )
            ]
    try:
        proxy = proxy_endpoint(settings)
    except (Refused, OSError, ValueError):
        proxy = None  # Not configured, unreadable or not a proxy URL.
    print(
        json.dumps(
            {
                "dependencies": {
                    name: importlib.metadata.version(name)
                    for name in ("telethon", "fastapi", "pydantic")
                },
                "credentials": refs,
                "routes": {
                    "model": LOGICAL_MODEL,
                    "provider": PROVIDER,
                    "speech": PROXY_WHISPER,
                    "proxy": proxy,
                },
                # The MLX worker serves offline fixtures and scripts/check-voice.py.
                "local_whisper_installed": (
                    ROOT / ".local/models/whisper/provenance.json"
                ).is_file(),
                "ffmpeg": bool(shutil.which("ffmpeg")),
                "personal_session_configured": bool(
                    settings.refs.get("PERSONAL_SESSION_REF")
                ),
                "personal_accounts": accounts,
                "personal_login": "In-app QR/phone login; zero accounts is a supported state",
                "live_acceptance": "not inferred from dependency or credential presence",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
