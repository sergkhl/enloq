"""Explicit Mac launcher: local approval without credentials in browser URLs."""

import json
import re
import subprocess
import time

import httpx

from .config import private_read
from .domain.models import Refused


def confirm_browser(code: str, seconds: int, claimant=None) -> bool:
    # The loopback Mac service is reached only by browsers on this Mac.
    if not re.fullmatch(r"[0-9]{6}", code):
        raise Refused("Invalid browser matching code")
    # Only a public numeric matching code enters the fixed AppleScript program.
    script = (
        'display dialog "This browser will have access to your connected Telegram '
        "accounts on this Mac.\\n\\nCompare this code with the browser: "
        + code
        + '\\n\\nOnly trust the browser showing this code." '
        'with title "Trust this browser?" buttons {"Cancel", "Trust browser"} '
        'default button "Cancel" cancel button "Cancel" giving up after '
        + str(max(1, seconds))
    )
    try:
        result = subprocess.run(
            ["/usr/bin/osascript", "-"],
            input=script,
            text=True,
            capture_output=True,
            timeout=max(1, seconds) + 2,
        )
    except subprocess.TimeoutExpired:
        return False
    return result.returncode == 0 and "button returned:Trust browser" in result.stdout


def pair_browser(data, port, *, browser=None, confirm=confirm_browser):
    """Use the existing service only; never create a Telegram session owner.

    The Mac instance only: a server with a public origin takes no launcher
    pairing (ADR 47 Q1).
    """
    local = f"http://127.0.0.1:{port}"
    headers = {"Origin": local}
    try:
        secret = private_read(data / "bootstrap", max_bytes=200)
    except (OSError, Refused) as error:
        raise Refused(
            "Enloq isn't running. Start its service and try again."
        ) from error
    with httpx.Client(
        base_url=local, headers=headers, timeout=5, trust_env=False
    ) as client:

        def control(path, **fields):
            try:
                response = client.post(path, json={"secret": secret, **fields})
                if response.status_code != 200:
                    raise Refused("Connection attempt ended. Open Enloq again.")
                return response.json()
            except httpx.HTTPError as error:
                raise Refused(
                    "Enloq isn't running. Start its service and try again."
                ) from error

        pairing = control("/api/auth/pairings")
        identity = pairing["id"]
        if not re.fullmatch(r"[a-f0-9]{32}", identity):
            raise Refused("Invalid local pairing response")
        endpoint = f"/api/auth/pairings/{identity}/control"
        deadline = time.monotonic() + 120
        finished = False
        try:
            # The locator is public: it cannot approve or complete a pairing.
            link = f"{local}/?connect={identity}"
            command = ["/usr/bin/open"]
            if browser:
                command += ["-a", browser]
            subprocess.run([*command, link], check=True)
            while time.monotonic() < deadline:
                state = control(endpoint)
                if state["state"] in {"complete", "already_trusted"}:
                    finished = True
                    print("Enloq opened in your trusted browser.")
                    return
                if state["state"] in {"expired", "cancelled"}:
                    break
                if state["state"] == "pending":
                    approved = confirm(
                        state["code"],
                        max(1, int(deadline - time.monotonic())),
                        {
                            "label": state.get("label", ""),
                            "kind": state.get("kind", ""),
                        },
                    )
                    if not approved:
                        break
                    control(endpoint, action="approve", code=state["code"])
                time.sleep(0.5)
            print("Browser connection cancelled or expired. Open Enloq to try again.")
        finally:
            if not finished:
                try:
                    control(endpoint, action="cancel")
                except Refused:
                    pass


def open_browser(root, *, mode=None, port=None, browser=None):
    installed = root / ".local/service/installed.json"
    settings = (
        json.loads(private_read(installed))
        if installed.exists() and mode is None
        else {}
    )
    mode = mode or settings.get("mode", "offline")
    port = port or settings.get("port", 8765)
    if mode not in {"offline", "personal"} or not 1024 <= int(port) <= 65535:
        raise Refused("Invalid local mode or port")
    pair_browser(root / ".local" / mode, int(port), browser=browser)
