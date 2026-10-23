"""Interactive local setup. Phone, app credentials, codes and passwords never echo."""

import argparse
import asyncio
import getpass
import logging
import os
import sys
from pathlib import Path

from telethon import errors

from .config import (
    ROOT,
    Settings,
    parse_config,
    private_directory,
    private_read,
    private_write,
)
from .domain.models import Refused
from .setup_login import PersonalLoginClient, request_login_code
from .storage.lock import OwnerLock


def save_references(updates):
    path = ROOT / "config/app.env"
    values = parse_config(path) if path.exists() else {}
    values.update(updates)
    private_write(
        path,
        "\n".join("ENLOQ_" + key + "=" + value for key, value in values.items()) + "\n",
    )


def credentials():
    api_id = getpass.getpass("Your own Telegram application API ID (hidden): ").strip()
    api_hash = getpass.getpass(
        "Your own Telegram application API hash (hidden): "
    ).strip()
    if (
        not api_id.isdecimal()
        or int(api_id) <= 0
        or len(api_hash) != 32
        or any(c not in "0123456789abcdefABCDEF" for c in api_hash)
    ):
        raise Refused("Application credentials have an invalid shape")
    private_write(ROOT / "secrets/personal-api-id", api_id)
    private_write(ROOT / "secrets/personal-api-hash", api_hash)
    save_references(
        {
            "PERSONAL_TELEGRAM_API_ID_REF": "secrets/personal-api-id",
            "PERSONAL_TELEGRAM_API_HASH_REF": "secrets/personal-api-hash",
        }
    )
    print("Private personal application references saved.")


def proxy_key():
    """ADR 39's LiteLLM proxy endpoint and the caller key bound to it."""
    from .inference.openrouter import checked_endpoint

    endpoint = checked_endpoint(
        input("LiteLLM proxy endpoint, e.g. https://llm.example.com/v1: ")
    )
    key = getpass.getpass("LiteLLM proxy caller key (hidden): ").strip()
    if not key.startswith("sk-") or key.startswith("sk-or-") or len(key) > 200:
        raise Refused("Use an sk- proxy caller key, not an OpenRouter key")
    private_write(ROOT / "secrets/litellm-api-key", key)
    private_write(ROOT / "secrets/muse-endpoint", endpoint)
    save_references(
        {
            "MUSE_ENDPOINT_REF": "secrets/muse-endpoint",
            "MUSE_API_KEY_REF": "secrets/litellm-api-key",
        }
    )
    print("Private proxy references saved.")


def reuse_credentials():
    values = parse_config(ROOT / "config/app.env")
    updates = {}
    for role in ("API_ID", "API_HASH"):
        key = "TELEGRAM_" + role + "_REF"
        reference = values.get(key)
        if not reference or not private_read(ROOT / reference):
            raise Refused("Prepared application credential reference is unavailable")
        personal = "PERSONAL_" + key
        if (
            values.get(personal)
            and (ROOT / values[personal]).resolve() != (ROOT / reference).resolve()
        ):
            raise Refused(
                "Existing personal credentials differ; preserve them for explicit resolution"
            )
        updates[personal] = reference
    save_references(updates)
    print(
        "Prepared Telegram application references selected; account login remains separate."
    )


async def login():
    settings = Settings.load("personal")
    if (settings.data / "accounts.sqlite3").exists():
        return await account_login(settings)
    path = private_directory(settings.data) / "personal.session"
    if path.is_symlink():
        raise Refused("An app-owned session file is required")
    with (
        OwnerLock(ROOT / ".local/service.owner.lock"),
        OwnerLock(Path(str(path) + ".owner.lock")),
    ):
        client = PersonalLoginClient(
            str(path),
            int(settings.secret("TELEGRAM_API_ID_REF")),
            settings.secret("TELEGRAM_API_HASH_REF"),
            catch_up=False,
            receive_updates=False,
            device_model="Enloq",
        )
        try:
            await client.connect()
            if not await client.is_user_authorized():
                if settings.refs.get("EXPECTED_ACCOUNT_ID"):
                    print(
                        "This profile remains pinned to its existing account. "
                        "A different account needs separate app data before activation."
                    )
                phone = getpass.getpass(
                    "Personal Telegram phone number with country code, e.g. +44... (hidden): "
                ).strip()
                code, phone_code_hash = await request_login_code(client, phone)
                try:
                    await client.sign_in(
                        phone=phone, code=code, phone_code_hash=phone_code_hash
                    )
                except errors.SessionPasswordNeededError:
                    await client.sign_in(
                        password=getpass.getpass(
                            "Telegram two-step password (hidden): "
                        )
                    )
            me = await client.get_me()
            if not me or me.bot:
                raise Refused("A personal user account is required")
            expected = settings.refs.get("EXPECTED_ACCOUNT_ID")
            if expected and int(expected) != me.id:
                raise Refused(
                    "Existing configured account does not match; preserve the session and resolve it explicitly"
                )
            print(f"Connected personal account ID: {me.id}")
            if input("Pin this account to this app? Type yes: ").strip() != "yes":
                raise Refused(
                    "Personal identity was not pinned; session retained for explicit setup"
                )
            save_references(
                {
                    "PERSONAL_SESSION_REF": ".local/personal/personal.session",
                    "EXPECTED_ACCOUNT_ID": str(me.id),
                }
            )
            print(
                "Account pinned. Start the personal service to register and synchronize it."
            )
        finally:
            await client.disconnect()
            for file in settings.data.glob("personal.session*"):
                if file.is_file() and not file.is_symlink():
                    file.chmod(0o600)


async def account_login(settings):
    """Terminal fallback shares the browser's challenge and registration logic."""
    from .application.accounts import AccountManager
    from .login import LoginManager

    with OwnerLock(ROOT / ".local/service.owner.lock"):
        manager = AccountManager(settings)
        logins = LoginManager(manager, client_factory=PersonalLoginClient)
        try:
            phone = await asyncio.to_thread(
                getpass.getpass, "Telegram phone number with country code (hidden): "
            )
            state = await logins.begin("local-terminal", "phone", phone)
            while state["stage"] in {"code", "password"}:
                print(state["message"])
                value = await asyncio.to_thread(
                    getpass.getpass,
                    "Telegram two-step password (hidden): "
                    if state["stage"] == "password"
                    else "Telegram code (hidden; Enter requests an allowed resend): ",
                )
                if not value and state["stage"] == "code" and state["can_resend"]:
                    state = await logins.submit(state["id"], "local-terminal", "resend")
                elif value:
                    state = await logins.submit(
                        state["id"], "local-terminal", state["stage"], value
                    )
                else:
                    await logins.cancel(state["id"], "local-terminal")
                    raise Refused("Login cancelled")
            if state["stage"] != "complete":
                raise Refused(state["message"])
            print(
                f"Account {state['account_id']} is registered. Start the personal service to synchronize it."
            )
        finally:
            await logins.close()
            await manager.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["credentials", "proxy-key", "login"])
    parser.add_argument("--reuse", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    if args.reuse and args.action != "credentials":
        raise Refused("Credential reuse is an explicit credentials action")
    if not args.reuse and not sys.stdin.isatty():
        raise Refused("Run setup in your own interactive local terminal")
    if args.reuse:
        reuse_credentials()
    elif args.action == "credentials":
        credentials()
    elif args.action == "proxy-key":
        proxy_key()
    else:
        asyncio.run(login())


if __name__ == "__main__":
    try:
        main()
    except Refused as error:
        print(str(error))
        raise SystemExit(1) from None
    except Exception as error:
        print(type(error).__name__ + ": setup stopped; credential details withheld")
        raise SystemExit(1) from None
