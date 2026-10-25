"""First-launch model download with progress (Whisper via Hugging Face Hub, pyannote if token).

Models are *not* in the installer (they would add 0.5-3 GB); they go to
<app data>/models and are fetched once.  Progress is measured as bytes on disk vs the
known approximate size of the repository, polled by the UI.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any

WHISPER_REPOS = {
    "tiny": ("Systran/faster-whisper-tiny", 75e6), "base": ("Systran/faster-whisper-base", 145e6),
    "small": ("Systran/faster-whisper-small", 480e6), "medium": ("Systran/faster-whisper-medium", 1.5e9),
    "large-v3": ("Systran/faster-whisper-large-v3", 3.1e9), "distil-large-v3": ("Systran/faster-distil-whisper-large-v3", 1.5e9),
}
_state: dict[str, Any] = {"running": False, "model": None, "error": None, "done": []}


def _dir_size(p: Path) -> int:
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.exists() else 0


def model_dir(models_root: Path, model: str) -> Path:
    repo = WHISPER_REPOS[model][0]
    return models_root / ("models--" + repo.replace("/", "--"))


def status(models_root: Path, model: str) -> dict[str, Any]:
    repo, size = WHISPER_REPOS.get(model, WHISPER_REPOS["small"])
    d = model_dir(models_root, model)
    have = _dir_size(d)
    complete = (d / "refs" / "main").exists() and have >= size * 0.8
    return {"model": model, "repo": repo, "expected_bytes": size, "bytes": have, "fraction": min(1.0, have / size),
            "complete": bool(complete), "running": _state["running"] and _state["model"] == model, "error": _state["error"]}


def download(models_root: Path, model: str, hf_token: str | None = None) -> None:
    if _state["running"]:
        return
    _state.update(running=True, model=model, error=None)

    def work():
        try:
            from huggingface_hub import snapshot_download
            os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
            snapshot_download(WHISPER_REPOS[model][0], cache_dir=str(models_root), token=hf_token or None)
            if hf_token:
                for repo in ("pyannote/speaker-diarization-3.1", "pyannote/segmentation-3.0", "pyannote/wespeaker-voxceleb-resnet34-LM"):
                    try:
                        snapshot_download(repo, cache_dir=str(models_root / "hf" / "hub"), token=hf_token)
                    except Exception as e:  # noqa: BLE001
                        _state["error"] = f"pyannote download: {e} (check the token and that you accepted the model terms)"
            _state["done"].append(model)
        except Exception as e:  # noqa: BLE001
            _state["error"] = str(e)
        finally:
            _state["running"] = False

    threading.Thread(target=work, daemon=True).start()
