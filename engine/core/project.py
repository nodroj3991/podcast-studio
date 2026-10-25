"""Episode project folder: layout, state, atomic JSON, checkpoints.

    <Episodes>/<2026-10-25 Dr Jane Smith>/
        project.json          metadata, guest, preset, stage status (the resume point)
        media/                copies/links of the raw files
        transcript/           transcript.json (source of truth), .txt, .docx
        edit/                 decisions.json, edl.json, edl.txt, graphics/
        music/                chosen tracks, attribution.txt
        charts/               *.png, *.mp4, *.vl.json, placements.json
        renders/              preview.mp4, episode_1080p.mp4
        captions/             .srt, .vtt
        work/                 intermediates (safe to delete)
        logs/                 engine.log
        Output/               the finished folder you share
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

STAGES = ["ingest", "transcribe", "edit_plan", "music", "charts", "edl", "render_preview", "render_final", "export", "qa", "package"]

STAGE_LABELS = {
    "ingest": "Checking your files",
    "transcribe": "Transcribing",
    "edit_plan": "Editing",
    "music": "Adding music",
    "charts": "Making charts",
    "edl": "Building the edit list",
    "render_preview": "Rendering preview",
    "render_final": "Rendering final video",
    "export": "Writing captions and transcript",
    "qa": "Checking quality",
    "package": "Packaging outputs",
}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        bak = path.with_suffix(path.suffix + ".bak")
        if bak.exists():
            return json.loads(bak.read_text(encoding="utf-8"))
        return default


@dataclass
class Project:
    root: Path

    # ------------------------------------------------------------ folders
    @property
    def media(self) -> Path: return self.root / "media"
    @property
    def transcript_dir(self) -> Path: return self.root / "transcript"
    @property
    def transcript(self) -> Path: return self.transcript_dir / "transcript.json"
    @property
    def edit_dir(self) -> Path: return self.root / "edit"
    @property
    def decisions(self) -> Path: return self.edit_dir / "decisions.json"
    @property
    def edl_json(self) -> Path: return self.edit_dir / "edl.json"
    @property
    def graphics(self) -> Path: return self.edit_dir / "graphics"
    @property
    def music_dir(self) -> Path: return self.root / "music"
    @property
    def charts_dir(self) -> Path: return self.root / "charts"
    @property
    def renders(self) -> Path: return self.root / "renders"
    @property
    def captions(self) -> Path: return self.root / "captions"
    @property
    def work(self) -> Path: return self.root / "work"
    @property
    def logs(self) -> Path: return self.root / "logs"
    @property
    def output(self) -> Path: return self.root / "Output"
    @property
    def project_json(self) -> Path: return self.root / "project.json"

    def ensure(self) -> "Project":
        for p in (self.media, self.transcript_dir, self.edit_dir, self.graphics, self.music_dir,
                  self.charts_dir, self.renders, self.captions, self.work, self.logs, self.output):
            p.mkdir(parents=True, exist_ok=True)
        if not self.project_json.exists():
            self.save({"version": 1, "created": time.time(), "guest": {}, "preset": "standard",
                       "stages": {}, "settings": {}})
        return self

    # ------------------------------------------------------------ metadata
    def load(self) -> dict[str, Any]:
        return read_json(self.project_json, {}) or {}

    def save(self, data: dict[str, Any]) -> None:
        if self.project_json.exists():
            shutil.copy(self.project_json, self.project_json.with_suffix(".json.bak"))
        write_json(self.project_json, data)

    def update(self, **fields: Any) -> dict[str, Any]:
        d = self.load()
        d.update(fields)
        self.save(d)
        return d

    # ------------------------------------------------------------ stage status (resume point)
    def stage_status(self, stage: str) -> dict[str, Any]:
        return self.load().get("stages", {}).get(stage, {"state": "pending"})

    def set_stage(self, stage: str, state: str, **extra: Any) -> None:
        d = self.load()
        st = d.setdefault("stages", {})
        rec = st.get(stage, {})
        rec.update({"state": state, "updated": time.time(), **extra})
        st[stage] = rec
        self.save(d)

    def is_done(self, stage: str) -> bool:
        return self.stage_status(stage).get("state") == "done"

    def invalidate_from(self, stage: str) -> None:
        """Mark this stage and everything after it as pending (used after a manual edit)."""
        d = self.load()
        st = d.setdefault("stages", {})
        for s in STAGES[STAGES.index(stage):]:
            if s in st:
                st[s]["state"] = "pending"
        self.save(d)

    @property
    def settings(self) -> dict[str, Any]:
        return self.load().get("settings", {})
