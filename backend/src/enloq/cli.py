import argparse
import os

import uvicorn

from .api.server import create_app
from .config import ROOT, Settings
from .logs import configure_logging
from .storage.lock import OwnerLock


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["offline", "personal"], default="offline")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    os.umask(0o077)
    configure_logging()
    with OwnerLock(ROOT / ".local/service.owner.lock"):
        settings = Settings.load(args.mode, port=args.port)
        print(f"Enloq: {settings.origin} ({settings.mode})")
        print(
            "Pair a browser or phone with the code from Saved Messages."
            if settings.remote
            else "Open with ./scripts/open to connect your browser."
        )
        uvicorn.run(
            create_app(settings),
            host=settings.listen_host,
            port=settings.port,
            workers=1,
            access_log=False,
            log_config=None,
            # Longer than the edge's two minutes, so the edge always closes an
            # idle connection first. At uvicorn's default 5 seconds, a stalled
            # event loop closed connections the edge was already reusing, and
            # a POST on one came back as 502 "connection reset by peer".
            timeout_keep_alive=130,
        )


if __name__ == "__main__":
    main()
