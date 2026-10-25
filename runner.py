"""Resumable stage runner with progress reporting.

* Each stage is a function ``stage(project, ctx) -> None``.  It is skipped if
  ``project.json`` already says it is ``done`` (unless forced), so a crash or a restart just
  resumes at the first unfinished stage.
* Progress is written to ``<project>/work/progress.json`` after every update; the desktop app
  polls that file (and the HTTP server streams it) to draw the progress screen.
* Cancellation: the UI touches ``<project>/work/CANCEL``; long loops call ``ctx.check_cancel()``.
* Errors are turned into plain-English messages (``friendly_error``) and always logged with
  the full traceback to ``<project>/logs/engine.log`` so the log can be sent for support.
"""
from __future__ import annotations

import logging
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .project import Project, STAGE_LABELS, STAGES, write_json


class Cancelled(Exception):
    pass


class UserFacingError(Exception):
    """An error with an explanation and a suggested fix for a non-programmer."""
    def __init__(self, message: str, fix: str = ""):
        super().__init__(message)
        self.fix = fix


def setup_logging(project: Project) -> logging.Logger:
    project.logs.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("engine")
    log.setLevel(logging.DEBUG)
    path = project.logs / "engine.log"
    if not any(getattr(h, "baseFilename", "") == str(path) for h in log.handlers):
        fh = logging.FileHandler(path, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        log.addHandler(fh)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in log.handlers):
        sh = logging.StreamHandler()
        sh.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        sh.setLevel(logging.INFO)
        log.addHandler(sh)
    return log


@dataclass
class Context:
    project: Project
    settings: dict[str, Any]
    log: logging.Logger
    stage: str = ""
    stage_index: int = 0
    stage_total: int = 1
    started: float = field(default_factory=time.time)
    stage_started: float = field(default_factory=time.time)
    estimates: dict[str, float] = field(default_factory=dict)
    force: bool = False
    _last_write: float = 0.0

    @property
    def progress_file(self) -> Path:
        return self.project.work / "progress.json"

    @property
    def cancel_file(self) -> Path:
        return self.project.work / "CANCEL"

    def check_cancel(self) -> None:
        if self.cancel_file.exists():
            raise Cancelled()

    def progress(self, fraction: float, detail: str = "", force: bool = False) -> None:
        """fraction 0..1 within the current stage."""
        self.check_cancel()
        now = time.time()
        if not force and now - self._last_write < 0.5:
            return
        self._last_write = now
        fraction = max(0.0, min(1.0, fraction))
        est_stage = self.estimates.get(self.stage, 0.0)
        elapsed = now - self.stage_started
        remaining_stage = max(0.0, est_stage - elapsed) if fraction < 0.05 else elapsed / fraction * (1 - fraction)
        remaining_later = sum(self.estimates.get(s, 0.0) for s in STAGES[self.stage_index + 1:] if s in self.estimates)
        write_json(self.progress_file, {
            "state": "running", "stage": self.stage, "label": STAGE_LABELS.get(self.stage, self.stage),
            "detail": detail, "stage_fraction": fraction,
            "stage_index": self.stage_index, "stage_total": self.stage_total,
            "overall_fraction": (self.stage_index + fraction) / max(self.stage_total, 1),
            "elapsed": now - self.started, "remaining": remaining_stage + remaining_later,
            "updated": now,
        })


def friendly_error(exc: BaseException) -> dict[str, str]:
    if isinstance(exc, UserFacingError):
        return {"message": str(exc), "fix": exc.fix}
    text = str(exc)
    if isinstance(exc, MemoryError) or "out of memory" in text.lower():
        return {"message": "Your computer ran out of memory during this step.",
                "fix": "Close other apps and try again, or pick a smaller transcription model in Settings > Quality."}
    if "No space left" in text or "ENOSPC" in text:
        return {"message": "Your disk is full.", "fix": "Free up at least 10 GB and press Resume. Nothing already finished needs to be redone."}
    if "ffmpeg" in text.lower() and "not found" in text.lower():
        return {"message": "The built-in video tool (FFmpeg) could not be started.", "fix": "Reinstall the app. If this keeps happening, send the log file from Settings > Help."}
    if "HTTP" in text or "Connection" in text or "resolve" in text.lower():
        return {"message": "An online lookup failed (music or research data).", "fix": "Check your internet connection and press Resume. Everything else runs offline."}
    if "pyannote" in text.lower() or "hugging face" in text.lower() or "401" in text:
        return {"message": "Speaker detection could not download its model.",
                "fix": "Open Settings > Speaker detection and check your Hugging Face token, then accept the model terms on the Hugging Face pages listed there."}
    return {"message": f"Something went wrong: {text[:300]}", "fix": "Press Resume to try the step again. If it fails twice, send the log file from Settings > Help."}


def run_stages(project: Project, stages: list[tuple[str, Callable[[Project, Context], None]]],
               settings: dict[str, Any], estimates: dict[str, float] | None = None,
               force: bool = False, only: list[str] | None = None) -> dict[str, Any]:
    log = setup_logging(project)
    ctx = Context(project, settings, log, estimates=estimates or {}, force=force)
    ctx.stage_total = len(stages)
    if ctx.cancel_file.exists():
        ctx.cancel_file.unlink()
    result: dict[str, Any] = {"state": "done"}
    try:
        for i, (name, fn) in enumerate(stages):
            ctx.stage, ctx.stage_index, ctx.stage_started = name, i, time.time()
            if only and name not in only:
                continue
            if project.is_done(name) and not force:
                log.info("skip %s (already done)", name)
                continue
            log.info("=== stage %s ===", name)
            project.set_stage(name, "running")
            ctx.progress(0.0, force=True)
            fn(project, ctx)
            project.set_stage(name, "done", seconds=time.time() - ctx.stage_started)
            ctx.progress(1.0, force=True)
    except Cancelled:
        project.set_stage(ctx.stage, "pending")
        result = {"state": "cancelled", "stage": ctx.stage}
        log.info("cancelled during %s", ctx.stage)
    except Exception as exc:  # noqa: BLE001
        log.error("stage %s failed:\n%s", ctx.stage, traceback.format_exc())
        project.set_stage(ctx.stage, "failed", error=str(exc))
        result = {"state": "failed", "stage": ctx.stage, **friendly_error(exc), "log": str(project.logs / "engine.log")}
    write_json(ctx.progress_file, {**result, "updated": time.time(), "elapsed": time.time() - ctx.started})
    return result
