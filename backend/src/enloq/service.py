"""A user launchd service whose startup MacHelm owns.

MacHelm registers, starts, stops and reports on the service; this module
hands it the definition and asks. Credentials never enter the definition or
process arguments, and there is no fallback to a LaunchAgent of our own.
"""

import argparse
import json
import os
import subprocess

from .config import ROOT, private_directory, private_write
from .domain.models import Refused

LABEL = "com.quantumcraft.enloq"
MACHELM_RUNNER = (
    "/Applications/MacHelm.app/Contents/Library/HelperTools/MacHelmServiceRunner"
)
# The time the lifecycle commands wait for a stop before reporting it as still
# in progress. Nothing is force-killed when it passes.
STOP_WAIT_SECONDS = 15


def service_definition(root, *, mode="offline", port=8765):
    if mode not in ("offline", "personal") or not 1024 <= port <= 65535:
        raise Refused("Only offline or personal service modes are supported")
    logs = private_directory(root / ".local/service")
    python = str(root / ".venv/bin/python")
    return {
        "label": LABEL,
        "name": "Enloq",
        "program": python,
        "arguments": [
            python,
            "-m",
            "enloq.cli",
            "--mode",
            mode,
            "--port",
            str(port),
        ],
        "workingDirectory": str(root),
        "environment": {
            "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
            "LANG": "en_US.UTF-8",
        },
        "runAtLoad": True,
        "keepAlive": "afterFailure",
        "throttleInterval": 30,
        "processType": "Background",
        "umask": 0o077,
        "standardOutPath": str(logs / "service.log"),
        "standardErrorPath": str(logs / "service-error.log"),
        # MacHelm reports Stopping until this lock is released.
        "ownerLockPaths": [str(root / ".local/service.owner.lock")],
    }


def machelm(*args):
    runner = os.environ.get("ENLOQ_MACHELM_RUNNER", MACHELM_RUNNER)
    if not os.access(runner, os.X_OK):
        raise Refused(
            "MacHelm owns this service's startup and is not installed at "
            "/Applications/MacHelm.app"
        )
    return subprocess.run([runner, *args, "--json"], capture_output=True, text=True)


def answer(result):
    try:
        value = json.loads(result.stdout)
    except ValueError:
        value = None
    return value if isinstance(value, dict) else {}


def refusal(result, fallback):
    return Refused(answer(result).get("message") or result.stderr.strip() or fallback)


def status():
    result = machelm("status", LABEL)
    report = answer(result)
    if result.returncode:
        if report.get("error") == "notFound":
            return {"registered": False, "managed": False, "owner": None}
        raise refusal(result, "MacHelm did not report on the service")
    fields = {
        "registered": bool(report.get("registered")),
        "managed": bool(report.get("managed")),
        "owner": "MacHelm" if report.get("managed") else "original LaunchAgent",
        "state": report.get("activity"),
        "automatic_startup": report.get("automaticStartup"),
    }
    if report.get("pid") is not None:
        fields["pid"] = str(report["pid"])
    if report.get("lastExitCode") is not None:
        fields["last exit code"] = str(report["lastExitCode"])
    if report.get("problems"):
        fields["problems"] = report["problems"]
    return fields


def managed_status():
    value = status()
    if not value["managed"]:
        raise Refused(
            "MacHelm does not manage this service. In MacHelm, open Startup "
            "Services and take it over, or run ./scripts/service install"
        )
    return value


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "action", choices=["install", "start", "stop", "status", "uninstall", "open"]
    )
    parser.add_argument("--mode", choices=["offline", "personal"])
    parser.add_argument("--port", type=int)
    parser.add_argument(
        "--browser", help="Mac browser application to open (for example Safari)"
    )
    args = parser.parse_args()
    os.umask(0o077)
    if args.action != "open":
        args.mode = args.mode or "offline"
        args.port = args.port or 8765
    if args.action == "install":
        if status()["registered"]:
            raise Refused("Stop the registered service before changing its mode")
        if args.mode == "personal":
            from .config import Settings

            settings = Settings.load("personal", port=args.port)
            # Installation verifies application credentials; account authorization
            # can now happen in the running dashboard, including the first login.
            for key in ("TELEGRAM_API_ID_REF", "TELEGRAM_API_HASH_REF"):
                settings.secret(key)
        definition = service_definition(ROOT, mode=args.mode, port=args.port)
        subprocess.run(
            ["npm", "--prefix", str(ROOT / "web"), "run", "build"], check=True
        )
        handover = ROOT / ".local/service/definition.json"
        private_write(handover, json.dumps(definition))
        # MacHelm refuses to replace a LaunchAgent it did not write, or a
        # definition its original LaunchAgent still owns; the refusal is shown.
        result = machelm("install-definition", str(handover))
        if result.returncode:
            raise refusal(result, "MacHelm did not accept the service definition")
        private_write(
            ROOT / ".local/service/installed.json",
            json.dumps({"mode": args.mode, "port": args.port}),
        )
        print(
            json.dumps(
                {
                    "installed": True,
                    "mode": args.mode,
                    "started": False,
                    "owner": "MacHelm",
                    "automatic_startup": answer(result).get("automaticStartup"),
                }
            )
        )
    elif args.action == "start":
        managed_status()
        result = machelm("start", LABEL)
        if result.returncode:
            raise refusal(result, "MacHelm did not start the service")
        print(json.dumps(status()))
    elif args.action in ("stop", "uninstall"):
        managed_status()
        result = machelm("stop", LABEL, "--wait", str(STOP_WAIT_SECONDS))
        if result.returncode == 3:
            raise Refused(
                "Service is still stopping; its registration and private data are retained"
            )
        if result.returncode:
            raise refusal(result, "MacHelm did not stop the service")
        if args.action == "uninstall":
            result = machelm("remove", LABEL)
            if result.returncode:
                raise refusal(result, "MacHelm did not remove the service")
        print(json.dumps({"registered": False, "private_data_retained": True}))
    elif args.action == "status":
        print(json.dumps(status()))
    elif args.action == "open":
        from .launcher import open_browser

        open_browser(ROOT, mode=args.mode, port=args.port, browser=args.browser)


if __name__ == "__main__":
    try:
        main()
    except Refused as error:
        print(str(error))
        raise SystemExit(1) from None
