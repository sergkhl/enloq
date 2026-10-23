"""Container health probe: the service's own loopback, under its public Host."""

from urllib.parse import urlsplit

import httpx

from .config import Settings


def main():
    settings = Settings.load("personal")
    try:
        response = httpx.get(
            f"http://127.0.0.1:{settings.port}/health",
            headers={"Host": urlsplit(settings.origin).netloc},
            timeout=3,
            trust_env=False,
        )
        ready = response.status_code == 200 and response.json()["status"] == "ready"
    except (httpx.HTTPError, ValueError, KeyError):
        ready = False
    raise SystemExit(0 if ready else 1)


if __name__ == "__main__":
    main()
