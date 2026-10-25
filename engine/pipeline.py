"""Pipeline assembly: the ordered stages and the ``run`` entry used by CLI and server."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .core.project import Project, STAGES
from .core.runner import run_stages
from .core import hw as HW
from .ingest.stage import ingest, transcribe
from .edit.stage import edit_plan, edl
from .music.stage import music
from .visuals.stage import charts
from .render.stage import render_preview, render_final, export, qa, package

STAGE_FUNCS = {
    "ingest": ingest, "transcribe": transcribe, "edit_plan": edit_plan, "music": music, "charts": charts, "edl": edl,
    "render_preview": render_preview, "render_final": render_final, "export": export, "qa": qa, "package": package,
}


def default_settings() -> dict[str, Any]:
    return {
        "host_name": "", "language": "en", "quality": "balanced",
        "min_silence": 1.2, "pad": 0.25, "remove_fillers": True,
        "min_chapter_s": 240, "chapter_sensitivity": 0.5, "chapter_cards": True, "transition": "fade", "transition_s": 0.6,
        "min_shot": 2.5, "punch_in_zoom": 1.12,
        "music_enabled": True, "music_mood": "curious", "music_intro": True, "music_outro": True, "music_bed": False,
        "bed_gain_db": -22, "duck_db": -12, "allow_sharealike": False,
        "charts_enabled": True, "chart_seconds": 6.0,
        "brand": {"primary": "#1F3A5F", "accent": "#E8B04B", "text": "#FFFFFF", "background": "#0F1B2D", "podcast_name": ""},
        "burn_in_captions": False, "vtt_voice_tags": False, "use_hardware_encoder": True,
        "jamendo_client_id": "", "hf_token": "", "contact_email": "", "models_dir": "",
    }


def prepare(project_dir: Path, inputs: list[dict[str, Any]] | None = None, guest: dict[str, Any] | None = None,
            title: str | None = None, settings: dict[str, Any] | None = None) -> Project:
    p = Project(Path(project_dir)).ensure()
    meta = p.load()
    if inputs is not None:
        meta["inputs"] = inputs
    if guest is not None:
        meta["guest"] = guest
    if title is not None:
        meta["title"] = title
    hw = HW.detect()
    meta["hardware"] = hw
    s = {**default_settings(), **(settings or {})}
    meta["settings"] = s
    meta["whisper_model"] = s.get("whisper_model") or HW.choose_model(hw, s.get("quality", "balanced"))
    p.save(meta)
    return p


def estimates_for(p: Project) -> dict[str, float]:
    meta = p.load()
    hw = meta.get("hardware") or HW.detect()
    dur = float(meta.get("duration") or _guess_duration(meta) or 3600)
    est = HW.estimate(hw, dur, meta.get("whisper_model"))
    return {"ingest": 10 + dur * 0.01, "transcribe": est["transcribe"] + est["diarize"], "edit_plan": est["edit_plan"],
            "music": 30, "charts": 40, "edl": 2, "render_preview": est["render_preview"], "render_final": est["render_final"],
            "export": 10, "qa": est["export_qa"], "package": 15}


def _guess_duration(meta: dict[str, Any]) -> float | None:
    from .ingest.media import probe
    for inp in meta.get("inputs") or []:
        try:
            return probe(Path(inp["path"])).duration
        except Exception:
            continue
    return None


def run(project_dir: Path, only: list[str] | None = None, force: bool = False, until: str | None = None,
        skip: list[str] | None = None) -> dict[str, Any]:
    p = Project(Path(project_dir)).ensure()
    meta = p.load()
    settings = {**default_settings(), **meta.get("settings", {})}
    names = STAGES[: STAGES.index(until) + 1] if until else STAGES
    stages = [(n, STAGE_FUNCS[n]) for n in names if not (skip and n in skip)]
    return run_stages(p, stages, settings, estimates_for(p), force=force, only=only)
