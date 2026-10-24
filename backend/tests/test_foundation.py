import json
import logging

import pytest
from enloq.config import Settings, private_read, private_write
from enloq.domain.models import Refused
from enloq.logs import SafeFormatter


def test_suggestion_diagnostics_allow_only_bounded_content_free_fields():
    record = logging.LogRecord(
        "enloq.suggestions", logging.DEBUG, "", 0, "suggestion_decision", (), None
    )
    record.suggestion_decision = {
        "attempt": "a" * 32,
        "trigger": "automatic",
        "reason": "automatic_signal",
        "emotion": "angry",
        "probability": 0.8,
        "usefulness": 0.2,
        "threshold": 0.7,
        "body": "Private canary",
        "chat_key": "Private canary",
        "prompt": "Private canary",
        "style": "Private canary",
        "outcome": "Private canary",
        "options": 100000,
    }
    line = SafeFormatter().format(record)
    assert "Private canary" not in line and "chat_key" not in line
    fields = json.loads(line.split("suggestion_decision ")[1])
    assert fields == {
        "attempt": "a" * 32,
        "trigger": "automatic",
        "reason": "automatic_signal",
        "emotion": "angry",
        "probability": 0.8,
        "usefulness": 0.2,
        "threshold": 0.7,
    }
    record.suggestion_decision = {
        "attempt": "1/user:42",
        "reason": "Private canary",
        "usefulness": float("nan"),
    }
    assert SafeFormatter().format(record).endswith("suggestion_decision {}")


def test_offline_does_not_read_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "enloq.config.parse_config",
        lambda _: pytest.fail("credential config opened"),
    )
    settings = Settings.load(root=tmp_path)
    with pytest.raises(Refused):
        settings.secret("MUSE_API_KEY_REF")


def test_private_file_and_log_boundary(tmp_path):
    path = tmp_path / "secret"
    private_write(path, "private canary")
    assert private_read(path) == "private canary"
    path.chmod(0o644)
    with pytest.raises(Refused):
        private_read(path)
    record = logging.LogRecord(
        "httpx", logging.ERROR, "", 0, "Bearer secret message body", (), None
    )
    line = SafeFormatter().format(record)
    assert line.endswith(" ERROR httpx event_redacted")
    assert "secret" not in line


def test_log_lines_carry_event_codes_and_safe_metadata_only():
    import sys

    def line(name, message, args=(), level=logging.WARNING, error=None):
        exc_info = None
        if error is not None:
            try:
                raise error
            except type(error):
                exc_info = sys.exc_info()
        record = logging.LogRecord(name, level, "", 0, message, args, exc_info)
        record.created = 1790740952.5
        return SafeFormatter().format(record)

    # App loggers pass their fixed event codes with time, level, logger and class.
    assert line("enloq.scheduler", "dispatch_unavailable") == (
        "2026-09-30T04:02:32Z WARNING enloq.scheduler dispatch_unavailable"
    )
    assert line(
        "enloq.mtproto",
        "mtproto_reconnect_stopped",
        level=logging.ERROR,
        error=ConnectionError("authored canary detail"),
    ).endswith("mtproto_reconnect_stopped error=ConnectionError")
    # Arguments, free text, other loggers and exception messages stay redacted.
    redacted = [
        line("enloq.scheduler", "dispatch_unavailable", ("canary",)),
        line("enloq.scheduler", "authored canary text"),
        line("enloqx", "dispatch_unavailable"),
        line("asyncio", "Task failed: canary", error=RuntimeError("canary")),
        line("enloq", {"canary": 1}),
    ]
    assert all("event_redacted" in r and "canary" not in r for r in redacted)
    assert redacted[3].endswith("asyncio event_redacted error=RuntimeError")


def test_app_warning_call_sites_use_fixed_event_codes():
    import ast
    from pathlib import Path

    import enloq
    from enloq.logs import EVENTS

    used = set()
    for path in Path(enloq.__file__).parent.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            where = f"{path.name}:{getattr(node, 'lineno', 0)}"
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_LOG" for t in node.targets
            ):
                name = node.value.args[0].value
                assert name == "enloq" or name.startswith("enloq."), where
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "_LOG"
                and node.func.attr
                in {"debug", "warning", "error", "critical", "exception"}
            ):
                code = node.args[0] if node.args else None
                assert (
                    len(node.args) == 1
                    and isinstance(code, ast.Constant)
                    and code.value in EVENTS
                ), where
                used.add(code.value)
    # Every warning-level app call uses a fixed code, and no code is stale.
    assert used == EVENTS


def test_media_lease_validation_and_cleanup(tmp_path):
    from enloq.transport.media import (
        MediaBounds,
        MediaValidationError,
        MediaWorkspace,
        validate_image,
    )
    from PIL import Image

    bounds = MediaBounds.narrowed(
        mime_types=frozenset({"image/png"}),
        image_bytes=10000,
        image_dimension=100,
        image_pixels=10000,
    )
    workspace = MediaWorkspace(tmp_path / "media")
    with workspace.lease(mime_type="image/png") as path:
        Image.new("RGB", (8, 8), "blue").save(path)
        assert validate_image(path, bounds).width == 8
        assert path.stat().st_mode & 0o777 == 0o600
    assert not path.exists()
    with pytest.raises(MediaValidationError):
        with workspace.lease() as path:
            path.write_bytes(b"not an image")
            validate_image(path, bounds)
    assert not path.exists()
