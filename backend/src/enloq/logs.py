"""Extended logs (ADR 61): each line says what happened, with its message and
traceback, and the app and the web send theirs here too. Nothing filters what
a log may say, except that anything shaped like a bearer token or a provider
key is masked, and no call site writes a request's headers or body. The last
lines also stay in memory for Settings → Logs, which only the owner reads."""

import collections
import json
import logging
import re
import threading
import time

# What the owner's Logs screen can read back, newest last.
RECENT_LINES = 3000
# Keys and tokens that a library's own error message might quote.
CREDENTIAL = re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+|\b(sk-)[a-z0-9_-]{8,}")


def scrub(line):
    return CREDENTIAL.sub(lambda match: (match.group(1) or match.group(2)) + "…", line)


class ExtendedFormatter(logging.Formatter):
    """``<UTC time> <level> <logger> <message>``, then any traceback."""

    converter = time.gmtime

    def __init__(self):
        super().__init__(
            "%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%SZ"
        )

    def format(self, record):
        line = super().format(record)
        fields = getattr(record, "suggestion_decision", None)
        if isinstance(fields, dict):
            line += " " + json.dumps(fields, sort_keys=True, default=str)
        return scrub(line)


class RecentLines(logging.Handler):
    """The last lines, for GET /api/logs."""

    def __init__(self, size=RECENT_LINES):
        super().__init__()
        self.lines = collections.deque(maxlen=size)
        self.guard = threading.Lock()

    def emit(self, record):
        try:
            line = self.format(record)
        except Exception:
            self.handleError(record)
            return
        with self.guard:
            self.lines.append(line)

    def read(self, limit=RECENT_LINES):
        with self.guard:
            lines = list(self.lines)
        return lines[-limit:]


recent = RecentLines()


def configure_logging():
    formatter = ExtendedFormatter()
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    recent.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler, recent]
    root.setLevel(logging.WARNING)
    logging.getLogger("enloq").setLevel(logging.INFO)
    logging.getLogger("enloq.suggestions").setLevel(logging.DEBUG)
    # A line per Telegram request belongs to the pacing database, which keeps
    # them all for 72 hours.
    logging.getLogger("enloq.mtproto").setLevel(logging.WARNING)
    logging.captureWarnings(True)
