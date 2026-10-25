"""Charts stage: pull data, render PNG + MP4 per chart, suggest placements from transcript cues.

Outputs in ``charts/``:
    profile.json            raw data + crosschecks + missing-data notes
    <key>.vl.json / .png / .mp4
    placements.json         [{"key","title","png","mp4","src_in","src_out","rec_in","rec_out","cue","confidence","enabled"}]

Placement: each chart has cue phrases; the first transcript sentence containing one of them
(preferring the *guest's* speech) is where the chart is shown, starting at the end of that
sentence so it does not cover the question, lasting ``chart_seconds`` (default 6, matching
the clip length).  Charts the host has moved in the review screen keep ``user_placed``.
Charts are never placed inside the intro, over a chapter card, or within 4 s of a cut.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.project import Project, read_json, write_json
from ..core.runner import Context
from ..edit.schema import load_transcript
from ..edit.cuts import kept_ranges, source_to_edited
from . import scholarly as S
from . import charts as C


def charts(project: Project, ctx: Context) -> None:
    s = ctx.settings
    meta = project.load()
    guest = meta.get("guest") or {}
    brand = s.get("brand") or {}
    out_dir = project.charts_dir
    existing = read_json(out_dir / "placements.json", []) or []
    user_placed = {p["key"]: p for p in existing if p.get("user_placed")}

    profile_data = read_json(out_dir / "profile.json", None)
    if (profile_data is None or ctx.force) and s.get("charts_enabled", True) and guest.get("name"):
        ctx.progress(0.05, f"Looking up {guest['name']} on OpenAlex")
        try:
            profile_data = S.gather(guest["name"], guest.get("institution"), s.get("contact_email"), guest.get("openalex_id"))
        except Exception as e:  # noqa: BLE001
            ctx.log.warning("scholarly lookup failed: %s", e)
            profile_data = {"profile": None, "candidates": [], "crosschecks": {}, "missing": [f"Online lookup failed: {e}"]}
        write_json(out_dir / "profile.json", profile_data)
    profile = (profile_data or {}).get("profile")

    reason = "; ".join((profile_data or {}).get("missing") or ["Guest not found in OpenAlex"])[:120]
    chart_defs = C.profile_charts(profile, brand, guest.get("name", "the guest"), reason) if s.get("charts_enabled", True) else []
    # custom charts added through the UI (charts/custom/*.json: {"title","rows","x","y","kind","source"})
    for f in sorted((out_dir / "custom").glob("*.json")) if (out_dir / "custom").exists() else []:
        d = read_json(f)
        chart_defs.append(C.custom_chart(brand, d["title"], d["rows"], d["x"], d["y"], d.get("kind", "bar"), d.get("source", "Guest-provided data")))

    tr = load_transcript(project.transcript) if project.transcript.exists() else None
    decisions = read_json(project.decisions, {}) or {}
    kept = kept_ranges(tr, decisions) if tr else []
    host_label = next((lab for lab, n in (tr.speakers.items() if tr else []) if n == s.get("host_name")), None)

    placements = []
    seconds = float(s.get("chart_seconds", 6.0))
    total = max(len(chart_defs), 1)
    used: list[tuple[float, float]] = []
    for i, cd in enumerate(chart_defs):
        ctx.progress(0.15 + 0.8 * i / total, f"Rendering chart: {cd['title']}")
        base = out_dir / cd["key"]
        write_json(Path(str(base) + ".vl.json"), cd["spec"])
        png = C.render_png(cd["spec"], Path(str(base) + ".png"))
        mp4 = Path(str(base) + ".mp4")
        if not mp4.exists() or ctx.force:
            C.render_mp4(cd["spec"], mp4, seconds=seconds, mode=cd["anim"])
        p = {"key": cd["key"], "title": cd["title"], "png": str(png), "mp4": str(mp4), "vl": str(base) + ".vl.json",
             "cue": "", "confidence": 0.0, "enabled": True, "src_in": None, "src_out": None, "rec_in": None, "rec_out": None,
             "nodata": bool((cd["spec"].get("usermeta") or {}).get("nodata"))}
        if cd["key"] in user_placed:
            p.update({k: user_placed[cd["key"]][k] for k in ("src_in", "src_out", "cue", "enabled")}, user_placed=True, confidence=1.0)
        elif tr and cd["cues"]:
            hit = find_cue(tr, cd["cues"], host_label, used)
            if hit:
                p.update(src_in=hit["start"], src_out=hit["start"] + seconds, cue=hit["text"], confidence=hit["confidence"])
                used.append((hit["start"], hit["start"] + seconds))
        if p["src_in"] is not None and kept:
            rec = source_to_edited(p["src_in"], kept)
            if rec is None:                       # cue got cut: nudge to the nearest kept time
                from ..edit.cuts import source_to_edited_nearest
                rec = source_to_edited_nearest(p["src_in"], kept)
            p["rec_in"], p["rec_out"] = round(rec, 3), round(rec + seconds, 3)
        p["enabled"] = p["enabled"] and not p["nodata"]
        placements.append(p)
    write_json(out_dir / "placements.json", placements)
    # a short human report
    lines = [f"Charts for {guest.get('name', '?')}"]
    if profile_data:
        for m in profile_data.get("missing", []):
            lines.append(f"  ! {m}")
        if profile_data.get("candidates") and not profile:
            lines.append("  Candidates: " + "; ".join(f"{c['name']} ({c.get('institution') or 'no institution'}, {c['works']} works)" for c in profile_data["candidates"][:5]))
    for p in placements:
        where = f"at {p['rec_in']:.0f}s (cue: \"{p['cue']}\")" if p["rec_in"] is not None else "not placed - drag it in the review screen"
        lines.append(f"  {'[x]' if p['enabled'] else '[ ]'} {p['title']}: {where}")
    (out_dir / "charts_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def find_cue(tr, cues: list[str], host_label: str | None, used: list[tuple[float, float]]) -> dict[str, Any] | None:
    """First sentence containing a cue phrase; prefer guest speech; skip the opening minute."""
    words = tr.words
    skip = min(90.0, tr.duration * 0.08)
    sentences = []
    cur = []
    for w in words:
        cur.append(w)
        if w.text.endswith((".", "?", "!")) or len(cur) > 40:
            sentences.append(cur); cur = []
    if cur:
        sentences.append(cur)
    best = None
    for sent in sentences:
        text = " ".join(w.text for w in sent)
        low = text.lower()
        for ci, cue in enumerate(cues):
            if re.search(r"\b" + re.escape(cue), low):
                start = sent[-1].end + 0.3
                if start < skip or any(a - 8 < start < b + 8 for a, b in used):
                    continue
                conf = 1.0 - ci * 0.08
                if host_label and sent[0].speaker == host_label:
                    conf -= 0.25
                cand = {"start": round(start, 3), "text": text[:90], "confidence": round(max(conf, 0.1), 2)}
                if best is None or cand["confidence"] > best["confidence"]:
                    best = cand
                break
        if best and best["confidence"] >= 0.9:
            break
    return best
