"""Set up the authored fixture server's groups and media chats for the Enloq
app's journeys.

ADR 42 AG1: the group journey briefs an authored group through Update with the
offline fake engine, which publishes its briefing in about a second. This sets
up the fixture's authored groups (`/api/test/group-chats`), with Group AI
processing Off and no briefing, as the web's group specs do, and starts each
group's four-hour automatic clock so that only the journey's deliberate Update
briefs them. It reads only the
disposable server's control secret, pairs a throwaway Trusted Device of its own
for the request, as `scripts/fixture_pair.py` approves the app's, and forgets
that device again. Never point it at a personal or VPS service.
"""

import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from enloq.config import ROOT, private_read

BASE = "http://127.0.0.1:8776"
PROOF = "X-Pairing-Proof"


def device_token(client: httpx.Client, secret: str) -> str:
    def control(identity="", **fields):
        path = "/api/auth/pairings"
        if identity:
            path += f"/{identity}/control"
        answer = client.post(path, json={"secret": secret, **fields})
        answer.raise_for_status()
        return answer.json()

    identity = control()["id"]
    claim = client.post(
        f"/api/auth/pairings/{identity}/device",
        json={"name": "Fixture setup"},
    )
    claim.raise_for_status()
    proof = claim.json()["proof"]
    control(identity, action="approve", code=control(identity)["code"])
    answer = client.post(
        f"/api/auth/pairings/{identity}/device/complete",
        json={},
        headers={PROOF: proof},
    )
    answer.raise_for_status()
    return answer.json()["token"]


def main() -> int:
    directory = Path(private_read(ROOT / ".local/browser-test-location"))
    secret = private_read(directory / "bootstrap", max_bytes=200)
    with httpx.Client(
        base_url=BASE, headers={"Origin": BASE}, timeout=10, trust_env=False
    ) as client:
        token = device_token(client, secret)
        device = {"Authorization": f"Bearer {token}"}
        try:
            seeded = client.post("/api/test/group-chats", json={}, headers=device)
            seeded.raise_for_status()
            groups = seeded.json()["groups"]
            # No automatic update may start mid-journey and cover what the
            # journey checks; Update is deliberate and unaffected.
            for peer in groups:
                clock = client.post(
                    "/api/test/group-chats",
                    json={"action": "automatic_clock", "peer": peer},
                    headers=device,
                )
                clock.raise_for_status()
            print("Authored groups:", ", ".join(groups))
            # The media chats (ADR 46 CU1): voice notes and circles, a VP9
            # video, a GIF, stickers, a document and music files.
            for path, data in (
                ("/api/test/voice-player", {"long": True}),
                ("/api/test/video", {"action": "setup", "case": "playable"}),
                ("/api/test/media-kinds", {}),
                # Message details (ADR 46 CU2), with CU3's profile.
                ("/api/test/details", {}),
                # Rewrite earlier messages (ADR 46 CU3).
                ("/api/test/older", {}),
                # Message Selection and deletion (ADR 46 CU4).
                ("/api/test/deletion", {}),
                # GIF and sticker sending (ADR 46 CU5).
                ("/api/test/gif-sticker", {"action": "setup", "case": "composer"}),
                # Group recovery (ADR 46 CU6): a retained group whose send
                # paused when access was lost, access back again. Its setup
                # would turn the account's AI processing off without
                # "master", which the other flows need on.
                (
                    "/api/test/group-recovery",
                    {
                        "peer": "chat:941",
                        "master": True,
                        "retained": True,
                        "accepted": True,
                        "draft": "A newer authored draft.",
                        "access": "available",
                    },
                ),
            ):
                client.post(path, json=data, headers=device).raise_for_status()
            print(
                "Authored media chats: Voice player, Video player, Media kinds,"
                " Message details, Earlier, Deletion fixture, GIF and sticker"
                " fixture, Retained authored discussion"
            )
        finally:
            trusted = client.get("/api/auth/browsers", headers=device).json()
            for entry in trusted:
                if entry["current"]:
                    client.post(
                        f"/api/auth/browsers/{entry['id']}/revoke",
                        json={},
                        headers=device,
                    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
