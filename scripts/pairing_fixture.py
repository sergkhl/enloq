"""Authored pairing for the Enloq app's journeys; registered only by the
fixture server, which holds authored data only (ADR 43 Q8, Q9).

- `GET /fixture/pairing` starts a pairing whose device claim this server
  approves at once: Enloq Dev's **Use the local test server**.
- `GET /fixture/pairing-code` returns the latest code posted to the synthetic
  Owner Account's Saved Messages, so a journey can type it: the six digits
  alone, in monospace (ADR 47 Q2).
- `POST /fixture/owner` names another synthetic Owner Account, one not on the
  server yet, so a journey can pair by signing it in with Telegram (ADR 47
  Q11); naming account 1 again signs out and removes the one it replaced.

They answer outside `/api`, before any trust, and only on this disposable
server through adb's port reversal.
"""

import dataclasses
import re

from fastapi import HTTPException, Request

CODE = re.compile(r"[0-9]{6}")


def register(app):
    auth = app.state.browser_auth
    local = set()
    claim = auth.claim_device

    def claim_device(identity, name):
        pairing, proof = claim(identity, name)
        if identity in local:
            local.discard(identity)
            pairing.state = "approved"
        return pairing, proof

    auth.claim_device = claim_device

    @app.get("/fixture/pairing")
    def local_pairing():
        pairing = auth.create_pairing()
        local.add(pairing.id)
        return {"id": pairing.id}

    @app.get("/fixture/pairing-code")
    def latest_code():
        owner = app.state.owner
        posts = app.state.accounts.get(owner.identity).transport.saved_posts
        for post in reversed(posts):
            if post.get("monospace") and CODE.fullmatch(post["text"] or ""):
                return {"code": post["text"]}
        raise HTTPException(404, "No pairing code was posted")

    @app.post("/fixture/owner")
    async def owner_account(request: Request):
        body = await request.json()
        owner = app.state.owner
        manager = app.state.accounts
        replaced = owner.identity
        identity = int(body["account_id"])
        owner.settings = dataclasses.replace(owner.settings, owner_account=identity)
        # The sign-in decision reads the same setting (ADR 48 Q11).
        manager.settings = dataclasses.replace(manager.settings, owner_account=identity)
        if identity == 1 and replaced not in {0, 1}:
            entries = manager.registry.rows(
                "SELECT state FROM accounts WHERE account_id=?", (replaced,)
            )
            if entries and entries[0]["state"] == "authorized":
                await manager.logout(replaced)
            if entries:
                await manager.remove(replaced)
        return {"account_id": owner.identity}
