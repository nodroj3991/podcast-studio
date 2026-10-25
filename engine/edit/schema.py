"""Transcript and project data model.

The transcript JSON is the source of truth produced by the ingest stage.  The loader is
tolerant: it accepts the WhisperX shape (``segments[].words[]`` with ``start``/``end``/
``speaker`` on either the word or the segment) and an optional ``speakers`` map that
renames ``SPEAKER_00`` -> ``"Dr Jane Smith"``.

Every word gets a stable integer ``id`` (its index in reading order).  All later decisions
(delete this word, this is a filler, cut here) reference word ids, never text, so edits
made in the review screen always stay attached to the right timings.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any


@dataclass
class Word:
    id: int | str                    # stable key (split corrections make "120.1"); never renumbered
    text: str
    start: float
    end: float
    speaker: str                     # canonical label e.g. SPEAKER_00
    score: float | None = None
    segment: int = 0                 # index of the segment this word belongs to
    idx: int = 0                     # position in reading order (for neighbour lookups)


@dataclass
class Transcript:
    words: list[Word]
    speakers: dict[str, str]         # label -> display name
    duration: float
    language: str = "en"
    source: dict[str, Any] = field(default_factory=dict)   # file names etc. from ingest
    raw: dict[str, Any] = field(default_factory=dict)      # untouched original JSON

    # ------------------------------------------------------------------ helpers
    def name(self, label: str) -> str:
        return self.speakers.get(label, label)

    def speaker_labels(self) -> list[str]:
        seen: list[str] = []
        for w in self.words:
            if w.speaker not in seen:
                seen.append(w.speaker)
        return seen

    def text(self, a: int | None = None, b: int | None = None) -> str:
        return " ".join(w.text for w in self.words[a:b])

    def to_json(self) -> dict[str, Any]:
        return {
            "version": 1,
            "language": self.language,
            "duration": self.duration,
            "speakers": self.speakers,
            "source": self.source,
            "words": [asdict(w) for w in self.words],
        }


def _num(v: Any, default: float | None = None) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def load_transcript(path: str | Path) -> Transcript:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return parse_transcript(data)


def parse_transcript(data: dict[str, Any]) -> Transcript:
    speakers: dict[str, str] = dict(data.get("speakers") or {})
    # ingest stage may store {"SPEAKER_00": {"name": "..."}}; flatten it
    for k, v in list(speakers.items()):
        if isinstance(v, dict):
            speakers[k] = v.get("name") or k

    words: list[Word] = []

    segments = data.get("segments") or []
    last_end = 0.0
    counter = 0
    for si, seg in enumerate(segments):
        seg_speaker = seg.get("speaker") or "SPEAKER_00"
        seg_words = seg.get("words") or []
        if not seg_words and seg.get("text"):
            # no word timings: spread the words evenly across the segment
            toks = str(seg["text"]).split()
            s0, s1 = _num(seg.get("start"), last_end), _num(seg.get("end"), last_end)
            step = (s1 - s0) / max(len(toks), 1)
            seg_words = [{"word": t, "start": s0 + i * step, "end": s0 + (i + 1) * step} for i, t in enumerate(toks)]
        for w in seg_words:
            wid = w.get("id", counter)
            counter += 1
            if w.get("removed"):
                continue
            text = str(w.get("word") or w.get("text") or "").strip()
            if not text:
                continue
            start = _num(w.get("start"))
            end = _num(w.get("end"))
            if start is None:            # punctuation-only tokens in WhisperX have no timing
                start = last_end
            if end is None or end < start:
                end = start + 0.05
            words.append(Word(wid, text, start, end, str(w.get("speaker") or seg_speaker),
                              _num(w.get("score")), si, len(words)))
            last_end = end

    duration = _num(data.get("duration")) or (words[-1].end if words else 0.0)
    return Transcript(words=words, speakers=speakers, duration=duration,
                      language=data.get("language", "en"), source=data.get("source") or {}, raw=data)


# ---------------------------------------------------------------------- project
@dataclass
class ProjectPaths:
    """Standard layout of an episode project folder."""
    root: Path

    @property
    def transcript(self) -> Path: return self.root / "transcript" / "transcript.json"
    @property
    def media(self) -> Path: return self.root / "media"
    @property
    def edit_dir(self) -> Path: return self.root / "edit"
    @property
    def decisions(self) -> Path: return self.edit_dir / "decisions.json"
    @property
    def edl_json(self) -> Path: return self.edit_dir / "edl.json"
    @property
    def edl_txt(self) -> Path: return self.edit_dir / "edl.txt"
    @property
    def edl_csv(self) -> Path: return self.edit_dir / "edl.csv"
    @property
    def graphics(self) -> Path: return self.edit_dir / "graphics"
    @property
    def renders(self) -> Path: return self.root / "renders"
    @property
    def work(self) -> Path: return self.root / "work"
    @property
    def project_json(self) -> Path: return self.root / "project.json"

    def ensure(self) -> "ProjectPaths":
        for p in (self.edit_dir, self.graphics, self.renders, self.work):
            p.mkdir(parents=True, exist_ok=True)
        return self

    def load_project(self) -> dict[str, Any]:
        if self.project_json.exists():
            return json.loads(self.project_json.read_text(encoding="utf-8"))
        return {}

    def save_project(self, data: dict[str, Any]) -> None:
        self.project_json.write_text(json.dumps(data, indent=2), encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)          # atomic: a crash mid-write never leaves a half file


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))
