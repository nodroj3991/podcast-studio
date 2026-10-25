"""Edit Decision List.

``edl.json`` is everything the renderer needs, on both timelines::

    {
      "duration_source": 5400.0, "duration_edited": 4710.2,
      "sources": [{"index":0,"path":...,"offset":0.0,"has_video":true}, ...],
      "clips":   [{"n":1,"src_in":12.3,"src_out":40.1,"rec_in":0.0,"rec_out":27.8,
                   "source":0,"zoom":1.0,"pan":0.0,"speaker":"SPEAKER_00"}, ...],
      "chapters": [{"title":..., "rec_time": 0.0, "src_time": 0.0, "card": true}, ...],
      "titles":  {"intro": {...}, "lower_thirds": [{"rec_in":2.0,"rec_out":8.0,"png":...}]},
      "music":   {...}, "charts": [{"rec_in":...,"png":...,"mp4":...}],
      "transitions": [{"rec_time": 612.0, "type": "fade", "duration": 0.6}],
      "removed": [{"start":..,"end":..,"reason":"silence"}]
    }

``edl.txt`` is the human preview shown before rendering.
"""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .cuts import kept_ranges, removed_ranges, source_to_edited_nearest, edited_duration, Range
from .schema import Transcript


def _tc(t: float) -> str:
    t = max(0.0, t)
    h, m, s = int(t // 3600), int(t % 3600 // 60), t % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


def build_edl(tr: Transcript, decisions: dict[str, Any], files: list[dict[str, Any]], layout: str,
              music: dict[str, Any] | None = None, charts: list[dict[str, Any]] | None = None,
              graphics: dict[str, Any] | None = None) -> dict[str, Any]:
    kept = kept_ranges(tr, decisions)
    removed = removed_ranges(tr, decisions)
    shots = decisions.get("camera", {}).get("shots", [])
    clips: list[dict[str, Any]] = []
    rec = 0.0
    n = 0
    for k in kept:
        # split the kept range at camera-shot boundaries
        cuts = sorted({k.start, k.end} | {s["start"] for s in shots if k.start < s["start"] < k.end})
        for a, b in zip(cuts, cuts[1:]):
            if b - a < 0.05:
                continue
            shot = next((s for s in shots if s["start"] <= a < s["end"]), shots[0] if shots else {"source": 0, "zoom": 1.0})
            n += 1
            clips.append({"n": n, "src_in": round(a, 3), "src_out": round(b, 3), "rec_in": round(rec, 3),
                          "rec_out": round(rec + (b - a), 3), "source": shot.get("source", 0),
                          "zoom": shot.get("zoom", 1.0), "pan": shot.get("pan", 0.0), "speaker": shot.get("speaker", "")})
            rec += b - a
    # a fragment shorter than 0.6 s left between a cut and a camera switch adopts the next shot
    # (continuous in source time), so the camera never flashes for a few frames
    for i in range(len(clips) - 1):
        c, nxt = clips[i], clips[i + 1]
        if c["src_out"] - c["src_in"] < 0.6 and abs(c["src_out"] - nxt["src_in"]) < 1e-3:
            c["source"], c["zoom"], c["pan"], c["speaker"] = nxt["source"], nxt["zoom"], nxt["pan"], nxt["speaker"]
    # merge consecutive clips with identical shot settings (fewer ffmpeg segments)
    merged: list[dict[str, Any]] = []
    for c in clips:
        p = merged[-1] if merged else None
        if p and abs(p["src_out"] - c["src_in"]) < 1e-3 and p["source"] == c["source"] and p["zoom"] == c["zoom"] and p["pan"] == c["pan"]:
            p["src_out"], p["rec_out"] = c["src_out"], c["rec_out"]
        else:
            merged.append(dict(c))
    for i, c in enumerate(merged, 1):
        c["n"] = i

    chapters = []
    for ch in decisions.get("chapters", []):
        chapters.append({"id": ch["id"], "title": ch["title"], "src_time": ch["start"],
                         "rec_time": round(source_to_edited_nearest(ch["start"], kept), 3),
                         "card": ch.get("card", True)})
    # a chapter break = the kept-range boundary nearest to the chapter start; snap to clip start
    for ch in chapters:
        nearest = min(merged, key=lambda c: abs(c["rec_in"] - ch["rec_time"]), default=None)
        if nearest and abs(nearest["rec_in"] - ch["rec_time"]) < 1.5:
            ch["rec_time"] = nearest["rec_in"]

    transitions = []
    tsettings = decisions.get("transitions", {"type": "fade", "duration": 0.6, "chapter_cards": True})
    for ch in chapters[1:]:
        transitions.append({"rec_time": ch["rec_time"], "type": tsettings.get("type", "fade"),
                            "duration": float(tsettings.get("duration", 0.6)), "chapter_id": ch["id"]})

    edl = {
        "version": 1,
        "duration_source": tr.duration, "duration_edited": round(edited_duration(kept), 3),
        "layout": layout,
        "sources": [{"index": i, "path": f["path"], "offset": f.get("offset", 0.0), "has_video": f.get("has_video", False),
                     "wav": f.get("wav")} for i, f in enumerate(files)],
        "clips": merged,
        "chapters": chapters,
        "transitions": transitions,
        "titles": graphics or {},
        "music": music or {},
        "charts": [c for c in (charts or []) if c.get("enabled") and c.get("rec_in") is not None],
        "charts_unplaced": [c.get("title") for c in (charts or []) if c.get("enabled") and c.get("rec_in") is None],
        "removed": [{"start": round(r.start, 3), "end": round(r.end, 3), "reason": r.reason} for r in removed],
        "kept": [{"start": round(k.start, 3), "end": round(k.end, 3)} for k in kept],
        "speakers": decisions.get("speakers") or tr.speakers,
    }
    return edl


def edl_text(edl: dict[str, Any], tr: Transcript | None = None) -> str:
    L = []
    src, out = edl["duration_source"], edl["duration_edited"]
    L.append("EDIT DECISION LIST")
    L.append(f"Source {_tc(src)}  ->  Edited {_tc(out)}   (removed {_tc(src - out)}, {100 * (1 - out / max(src, 1e-6)):.1f}%)")
    by_reason: dict[str, float] = {}
    for r in edl["removed"]:
        by_reason[r["reason"]] = by_reason.get(r["reason"], 0.0) + r["end"] - r["start"]
    L.append("Removed: " + ", ".join(f"{k} {v:.1f}s ({sum(1 for r in edl['removed'] if r['reason'] == k)} cuts)" for k, v in by_reason.items()))
    L.append("")
    L.append("CHAPTERS")
    for ch in edl["chapters"]:
        L.append(f"  {_tc(ch['rec_time'])[:8]}  {ch['title']}")
    if edl.get("music"):
        L.append("")
        L.append("MUSIC")
        m = edl["music"]
        for key in ("intro", "outro", "bed"):
            if m.get(key):
                t = m[key]
                L.append(f"  {key:5s} {t.get('title','?')} - {t.get('artist','?')}  [{t.get('license','?')}]")
    if edl.get("charts"):
        L.append("")
        L.append("CHARTS")
        for c in edl["charts"]:
            L.append(f"  {_tc(c['rec_in'])[:8]}-{_tc(c['rec_out'])[:8]}  {c.get('title','chart')}  (cue: \"{c.get('cue','')}\")")
    if edl.get("charts_unplaced"):
        L.append("  not placed (drag into the timeline in Review): " + ", ".join(edl["charts_unplaced"]))
    L.append("")
    L.append(f"CLIPS ({len(edl['clips'])})")
    L.append("  #    source in      source out     record in      cam  zoom  speaker")
    names = edl.get("speakers", {})
    for c in edl["clips"]:
        L.append(f"  {c['n']:<4d} {_tc(c['src_in'])}   {_tc(c['src_out'])}   {_tc(c['rec_in'])}   {str(c['source']):<4s} {c['zoom']:<5.2f} {names.get(c['speaker'], c['speaker'])}")
    if edl.get("transitions"):
        L.append("")
        L.append("TRANSITIONS (chapter breaks only)")
        for t in edl["transitions"]:
            L.append(f"  {_tc(t['rec_time'])[:8]}  {t['type']} {t['duration']}s")
    return "\n".join(L)


def write_edl(edl: dict[str, Any], json_path: Path, txt_path: Path, csv_path: Path) -> None:
    from ..core.project import write_json
    write_json(json_path, edl)
    txt_path.write_text(edl_text(edl), encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["clip", "source_in", "source_out", "record_in", "record_out", "camera", "zoom", "speaker"])
        for c in edl["clips"]:
            w.writerow([c["n"], _tc(c["src_in"]), _tc(c["src_out"]), _tc(c["rec_in"]), _tc(c["rec_out"]), c["source"], c["zoom"], c["speaker"]])
