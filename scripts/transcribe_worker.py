"""Single local-only STT process. All raw audio lives in the caller's lease."""

import contextlib
import io
import json
import sys

with (
    contextlib.redirect_stdout(io.StringIO()),
    contextlib.redirect_stderr(io.StringIO()),
):
    import mlx_whisper

    output = mlx_whisper.transcribe(
        sys.argv[1],
        path_or_hf_repo=sys.argv[2],
        verbose=None,
        condition_on_previous_text=False,
    )
print(
    json.dumps(
        {"text": output["text"], "language": output.get("language")}, ensure_ascii=False
    )
)
