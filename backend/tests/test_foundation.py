import json
import logging
import sqlite3
import sys

import pytest
from enloq.config import Settings, private_read, private_write
from enloq.domain.models import Refused
from enloq.logs import ExtendedFormatter, RecentLines


def test_a_suggestion_decision_line_carries_its_fields():
    record = logging.LogRecord(
        "enloq.suggestions", logging.DEBUG, "", 0, "suggestion_decision", (), None
    )
    record.suggestion_decision = {"attempt": "a" * 32, "reason": "automatic_signal"}
    line = ExtendedFormatter().format(record)
    assert json.loads(line.split("suggestion_decision ")[1]) == {
        "attempt": "a" * 32,
        "reason": "automatic_signal",
    }


def test_offline_does_not_read_credentials(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "enloq.config.parse_config",
        lambda _: pytest.fail("credential config opened"),
    )
    settings = Settings.load(root=tmp_path)
    with pytest.raises(Refused):
        settings.secret("MUSE_API_KEY_REF")


def test_private_files_stay_private(tmp_path):
    path = tmp_path / "secret"
    private_write(path, "private canary")
    assert private_read(path) == "private canary"
    path.chmod(0o644)
    with pytest.raises(Refused):
        private_read(path)


def log_line(name, message, args=(), level=logging.WARNING, error=None):
    exc_info = None
    if error is not None:
        try:
            raise error
        except type(error):
            exc_info = sys.exc_info()
    record = logging.LogRecord(name, level, "", 0, message, args, exc_info)
    record.created = 1790740952.5
    return ExtendedFormatter().format(record)


def test_log_lines_carry_their_message_and_traceback():
    # ADR 61: time, level, logger, message and arguments, then the traceback.
    assert log_line("enloq.scheduler", "dispatch_unavailable") == (
        "2026-09-30T04:02:32Z WARNING enloq.scheduler dispatch_unavailable"
    )
    assert log_line("httpx", "reading %s", ("a body",)).endswith(
        "WARNING httpx reading a body"
    )
    line = log_line(
        "uvicorn.error",
        "Exception in ASGI application",
        level=logging.ERROR,
        error=sqlite3.OperationalError("database is locked"),
    )
    assert "Exception in ASGI application\nTraceback" in line
    assert "log_line" in line
    assert line.endswith("sqlite3.OperationalError: database is locked")


def test_anything_shaped_like_a_credential_is_masked():
    line = log_line(
        "httpx",
        "Authorization: Bearer abc.DEF-123 for sk-or-v1-0123456789 at https://x/v1",
    )
    assert "abc.DEF" not in line and "0123456789" not in line
    assert line.endswith("Authorization: Bearer … for sk-… at https://x/v1")


def test_recent_lines_keep_the_newest():
    recent = RecentLines(size=2)
    recent.setFormatter(ExtendedFormatter())
    for text in ("first", "second", "third"):
        recent.emit(logging.LogRecord("enloq", logging.INFO, "", 0, text, (), None))
    assert [line.rsplit(" ", 1)[1] for line in recent.read()] == ["second", "third"]
    assert len(recent.read(1)) == 1


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
