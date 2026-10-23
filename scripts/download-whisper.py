"""Install only the owner-selected local speech model; no remote inference."""

import json
import os
from pathlib import Path

from huggingface_hub import HfApi, snapshot_download

os.umask(0o077)
model = "mlx-community/whisper-large-v3-turbo"
root = Path(__file__).resolve().parents[1] / ".local/models/whisper"
root.mkdir(parents=True, exist_ok=True, mode=0o700)
revision = HfApi().model_info(model).sha
snapshot_download(
    model,
    revision=revision,
    local_dir=root,
    allow_patterns=["config.json", "weights.safetensors", "*.npz"],
)
(root / "provenance.json").write_text(
    json.dumps({"model": model, "revision": revision}) + "\n"
)
for path in root.rglob("*"):
    if not path.is_symlink():
        path.chmod(0o700 if path.is_dir() else 0o600)
print(json.dumps({"model": model, "revision": revision, "installed": True}))
