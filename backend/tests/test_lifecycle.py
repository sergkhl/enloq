import asyncio
import json
import os
import subprocess
import sys
import time

import pytest
from enloq import service
from enloq.config import Settings, private_write
from enloq.domain.models import Refused, Unavailable
from enloq.inference.audio import LocalWhisper
from enloq.inference.runner import bounded_process
from enloq.service import service_definition


async def test_A07_audio_size_and_duration_refuse_before_local_model(
    tmp_path, monkeypatch
):
    from enloq.inference import audio

    private_write(
        tmp_path / ".local/models/whisper/provenance.json",
        json.dumps(
            {"model": "mlx-community/whisper-large-v3-turbo", "revision": "synthetic"}
        ),
    )
    binary = tmp_path / "audio/.venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.touch()
    path = tmp_path / "audio.bin"
    with path.open("wb") as stream:
        stream.truncate(25_000_001)
    worker = LocalWhisper(tmp_path)
    with pytest.raises(Refused):
        await worker.transcribe(path)
    path.write_bytes(b"authored")
    calls = []

    async def probe(argv, **kwargs):
        calls.append(argv)
        return b'{"format":{"duration":"601"}}', b""

    monkeypatch.setattr(audio, "bounded_process", probe)
    with pytest.raises(Refused):
        await worker.transcribe(path)
    assert len(calls) == 1 and "ffprobe" in calls[0][0]


async def test_A14_worker_slot_survives_parent_crash_and_child_is_terminated(tmp_path):
    pidfile = tmp_path / "child.pid"
    mutex = tmp_path / "slot.lock"
    child = f"import os,time; from pathlib import Path; Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(60)"
    parent = f"import asyncio; from enloq.inference.runner import bounded_process; asyncio.run(bounded_process({[sys.executable, '-c', child]!r},stdin=b'',cwd={str(tmp_path)!r},env={{}},mutex={str(mutex)!r}))"
    process = subprocess.Popen(
        [sys.executable, "-c", parent],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not pidfile.exists() and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert pidfile.exists()
        child_pid = int(pidfile.read_text())
        with pytest.raises(Unavailable):
            await bounded_process(
                [sys.executable, "-c", "pass"],
                stdin=b"",
                cwd=tmp_path,
                env={},
                mutex=mutex,
                timeout=2,
            )
        process.kill()
        process.wait()
        deadline = time.monotonic() + 5
        alive = True
        while time.monotonic() < deadline:
            try:
                os.kill(child_pid, 0)
            except ProcessLookupError:
                alive = False
                break
            await asyncio.sleep(0.05)
        assert not alive
        await bounded_process(
            [sys.executable, "-c", "pass"],
            stdin=b"",
            cwd=tmp_path,
            env={},
            mutex=mutex,
            timeout=2,
        )
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_A14_service_configuration_and_mode_isolation(tmp_path):
    definition = service_definition(tmp_path, mode="offline")
    assert definition["arguments"][-3:] == ["offline", "--port", "8765"]
    assert definition["program"] == definition["arguments"][0]
    assert definition["umask"] == 0o077 and definition["keepAlive"] == "afterFailure"
    assert definition["runAtLoad"] is True and definition["throttleInterval"] == 30
    assert definition["ownerLockPaths"] == [str(tmp_path / ".local/service.owner.lock")]
    assert not any(
        k in json.dumps(definition).lower()
        for k in ["api_hash", "api_key", "token", "password"]
    )
    # Offline loading ignores even invalid private configuration.
    private_write(tmp_path / "config/app.env", "unsupported=value")
    assert Settings.load("offline", root=tmp_path).refs == {}
    with pytest.raises(Refused):
        service_definition(tmp_path, mode="acceptance")


def fake_machelm(tmp_path, monkeypatch, body):
    """A stand-in for MacHelm's runner that records what it was asked."""
    runner = tmp_path / "runner"
    runner.write_text(f'#!/bin/sh\necho "$@" >> "{tmp_path}/asked"\n{body}\n')
    runner.chmod(0o755)
    monkeypatch.setenv("ENLOQ_MACHELM_RUNNER", str(runner))
    return tmp_path / "asked"


def run_service(monkeypatch, *arguments):
    monkeypatch.setattr(sys, "argv", ["service", *arguments])
    service.main()


def test_A14_startup_belongs_to_machelm_with_no_fallback(tmp_path, monkeypatch, capsys):
    launch_agents = tmp_path / "Library/LaunchAgents"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("ENLOQ_MACHELM_RUNNER", str(tmp_path / "absent"))
    for action in ("status", "start", "stop", "uninstall"):
        with pytest.raises(Refused, match="MacHelm owns this service's startup"):
            run_service(monkeypatch, action)
    assert not launch_agents.exists(), "no LaunchAgent of our own is written"

    # MacHelm does not know the service: a visible error, not a legacy start.
    asked = fake_machelm(
        tmp_path,
        monkeypatch,
        """echo '{"error": "notFound", "message": "not in Startup Services"}'; exit 1""",
    )
    run_service(monkeypatch, "status")
    assert json.loads(capsys.readouterr().out) == {
        "registered": False,
        "managed": False,
        "owner": None,
    }
    for action in ("start", "stop", "uninstall"):
        with pytest.raises(Refused, match="MacHelm does not manage this service"):
            run_service(monkeypatch, action)
    assert set(asked.read_text().splitlines()) == {f"status {service.LABEL} --json"}
    assert not launch_agents.exists()

    # Its original LaunchAgent still owns it: also refused, never run beside it.
    fake_machelm(
        tmp_path,
        monkeypatch,
        """echo '{"registered": true, "managed": false, "activity": "running", "pid": 7}'""",
    )
    run_service(monkeypatch, "status")
    assert json.loads(capsys.readouterr().out)["owner"] == "original LaunchAgent"
    with pytest.raises(Refused, match="MacHelm does not manage this service"):
        run_service(monkeypatch, "start")


def test_A14_lifecycle_commands_delegate_and_report_truthfully(
    tmp_path, monkeypatch, capsys
):
    running = (
        '{"registered": true, "managed": true, "activity": "running", "pid": 4242,'
        ' "automaticStartup": true, "lastExitCode": 0, "problems": []}'
    )
    asked = fake_machelm(tmp_path, monkeypatch, f"echo '{running}'")
    run_service(monkeypatch, "start")
    assert json.loads(capsys.readouterr().out) == {
        "registered": True,
        "managed": True,
        "owner": "MacHelm",
        "state": "running",
        "automatic_startup": True,
        "pid": "4242",
        "last exit code": "0",
    }
    assert f"start {service.LABEL} --json" in asked.read_text().splitlines()

    # A stop that has not finished is reported as such; nothing is force-killed.
    asked.unlink()
    fake_machelm(
        tmp_path,
        monkeypatch,
        f"""case "$1" in stop) echo '{{"activity": "stopping"}}'; exit 3;; esac
echo '{running}'""",
    )
    with pytest.raises(Refused, match="still stopping"):
        run_service(monkeypatch, "uninstall")
    lines = asked.read_text().splitlines()
    assert f"stop {service.LABEL} --wait 15 --json" in lines
    assert not any(line.startswith("remove") for line in lines)

    # A refusal from MacHelm is passed on in its own words.
    fake_machelm(
        tmp_path,
        monkeypatch,
        f"""case "$1" in start) echo '{{"error": "blocked", "message": "macOS is blocking background items"}}'; exit 1;; esac
echo '{running}'""",
    )
    with pytest.raises(Refused, match="macOS is blocking background items"):
        run_service(monkeypatch, "start")

    asked.unlink()
    fake_machelm(tmp_path, monkeypatch, f"echo '{running}'")
    run_service(monkeypatch, "uninstall")
    assert json.loads(capsys.readouterr().out) == {
        "registered": False,
        "private_data_retained": True,
    }
    assert asked.read_text().splitlines()[-2:] == [
        f"stop {service.LABEL} --wait 15 --json",
        f"remove {service.LABEL} --json",
    ]
