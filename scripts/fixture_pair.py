"""Pair a simulator, emulator or phone with the authored fixture server.

ADR 38 Q11: app journeys run against `scripts/test_server.py` on port 8776,
never against a personal or VPS service. This reads only that disposable
server's control secret, prints a pairing link for the Enloq app and approves
the first claim, as `web/journeys/fixture.ts` does for browsers.
"""

import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from enloq.config import ROOT, private_read

BASE = "http://127.0.0.1:8776"


def main() -> int:
    directory = Path(private_read(ROOT / ".local/browser-test-location"))
    secret = private_read(directory / "bootstrap", max_bytes=200)
    with httpx.Client(
        base_url=BASE, headers={"Origin": BASE}, timeout=5, trust_env=False
    ) as client:

        def control(identity="", **fields):
            path = "/api/auth/pairings"
            if identity:
                path += f"/{identity}/control"
            answer = client.post(path, json={"secret": secret, **fields})
            answer.raise_for_status()
            return answer.json()

        identity = control()["id"]
        print(f"{BASE}/?connect={identity}", flush=True)
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = control(identity)
            if state["state"] == "pending":
                print(f"Approving {state['label']} ({state['code']})", flush=True)
                control(identity, action="approve", code=state["code"])
            elif state["state"] in {"complete", "already_trusted"}:
                print("Paired.")
                return 0
            elif state["state"] in {"expired", "cancelled"}:
                print(f"Pairing {state['state']}.")
                return 1
            time.sleep(0.5)
        print("Pairing expired.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
