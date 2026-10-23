import asyncio
import os
import signal
import sys
from pathlib import Path

from ..domain.models import Unavailable


async def bounded_process(
    argv,
    *,
    stdin: bytes,
    cwd,
    env,
    timeout=600,
    stdout_limit=1048576,
    stderr_limit=65536,
    mutex=None,
):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        str(Path(__file__).resolve().parents[1] / "worker_guard.py"),
        str(os.getpid()),
        str(timeout),
        str(mutex) if mutex else "-",
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )

    async def read(stream, limit):
        chunks, total = [], 0
        while chunk := await stream.read(8192):
            total += len(chunk)
            if total > limit:
                raise Unavailable("Subprocess output exceeded capture bound")
            chunks.append(chunk)
        return b"".join(chunks)

    async def write():
        process.stdin.write(stdin)
        await process.stdin.drain()
        process.stdin.close()

    try:
        async with asyncio.timeout(timeout):
            results = await asyncio.gather(
                write(),
                read(process.stdout, stdout_limit),
                read(process.stderr, stderr_limit),
                process.wait(),
            )
            if results[3] != 0:
                raise Unavailable("Subprocess unavailable; diagnostic content withheld")
            return results[1], results[2]
    finally:
        if process.returncode is None:
            os.killpg(process.pid, signal.SIGKILL)
            await process.wait()
