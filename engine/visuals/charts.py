"""Vega-Lite charts in brand colours -> PNG (1920x1080) and short animated MP4 (3-6 s).

Animation = progressive reveal rendered frame by frame with vl-convert (no browser):
bars grow in / line draws left-to-right by cropping the data domain per frame, then
``ffmpeg`` stitches the frames at 30 fps with a 0.4 s fade-in, holding the final frame.
At 1920x1080 vl-convert renders ~12 frames/s on a laptop, so a 4 s clip (120 frames)
takes ~10 s.

Every spec carries a "Source: OpenAlex, retrieved 2026-10-25" caption; charts with no
data are rendered as a plain "No data available for X" card so nothing is ever invented.
"""
from __future__ import annotations

import json
import math
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

W, H = 1920, 1080


def _theme(brand: dict[str, Any]) -> dict[str, Any]:
    text = brand.get("chart_text", "#1B1B1B")
    return {
        "background": brand.get("chart_background", "#FFFFFF"),
        "font": brand.get("font", "Inter"),
        "title": {"fontSize": 44, "font": brand.get("font", "Inter"), "anchor": "start", "color": text, "offset": 24,
                  "subtitleFontSize": 26, "subtitleColor": "#555555", "subtitlePadding": 10},
        "axis": {"labelFontSize": 24, "titleFontSize": 26, "labelColor": text, "titleColor": text, "gridColor": "#E6E6E6",
                 "domainColor": "#BBBBBB", "tickColor": "#BBBBBB", "labelPadding": 8, "titlePadding": 14},
        "legend": {"labelFontSize": 24, "titleFontSize": 24, "labelColor": text},
        "view": {"stroke": None},
        "range": {"category": [brand.get("primary", "#1F3A5F"), brand.get("accent", "#E8B04B"), "#4C8C9E", "#A4A4A4", "#7B5EA7", "#C96A3C"]},
    }


def _base(brand: dict[str, Any], title: str, subtitle: str, source: str) -> dict[str, Any]:
    return {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "width": W - 220, "height": H - 330,
            "padding": {"left": 90, "right": 110, "top": 70, "bottom": 60}, "config": _theme(brand),
            "title": {"text": title, "subtitle": [subtitle, f"Source: {source}"]}}


# ------------------------------------------------------------------------------ chart types
def bar_spec(brand, title, subtitle, source, rows, x, y, x_title="", y_title="", horizontal=False, label_angle=0):
    s = _base(brand, title, subtitle, source)
    enc_x = {"field": x, "type": "ordinal" if not horizontal else "quantitative", "title": x_title,
             "axis": {"labelAngle": label_angle}}
    enc_y = {"field": y, "type": "quantitative" if not horizontal else "nominal", "title": y_title}
    if horizontal:
        enc_x = {"field": y, "type": "quantitative", "title": y_title}
        enc_y = {"field": x, "type": "nominal", "title": x_title, "sort": "-x", "axis": {"labelLimit": 520}}
    s.update({"data": {"values": rows}, "mark": {"type": "bar", "cornerRadiusEnd": 6, "color": brand.get("primary", "#1F3A5F")},
              "encoding": {"x": enc_x, "y": enc_y}})
    return s


def line_spec(brand, title, subtitle, source, rows, x, y, x_title="", y_title="", area=True):
    s = _base(brand, title, subtitle, source)
    layers = [{"mark": {"type": "line", "strokeWidth": 6, "color": brand.get("primary", "#1F3A5F"), "interpolate": "monotone"},
               "encoding": {"x": {"field": x, "type": "ordinal", "title": x_title, "axis": {"labelAngle": 0}},
                            "y": {"field": y, "type": "quantitative", "title": y_title}}},
              {"mark": {"type": "point", "size": 260, "filled": True, "color": brand.get("accent", "#E8B04B")},
               "encoding": {"x": {"field": x, "type": "ordinal"}, "y": {"field": y, "type": "quantitative"}}}]
    if area:
        layers.insert(0, {"mark": {"type": "area", "opacity": 0.12, "color": brand.get("primary", "#1F3A5F"), "interpolate": "monotone"},
                          "encoding": {"x": {"field": x, "type": "ordinal"}, "y": {"field": y, "type": "quantitative"}}})
    s.update({"data": {"values": rows}, "layer": layers})
    return s


def timeline_spec(brand, title, subtitle, source, rows):
    """Institutions over time: horizontal bars from 'from' to 'to'."""
    s = _base(brand, title, subtitle, source)
    s.update({"data": {"values": rows},
              "mark": {"type": "bar", "cornerRadius": 8, "height": 46, "color": brand.get("primary", "#1F3A5F")},
              "encoding": {"y": {"field": "institution", "type": "nominal", "sort": {"field": "from"}, "title": "", "axis": {"labelLimit": 600}},
                           "x": {"field": "from", "type": "quantitative", "title": "Year", "scale": {"zero": False}, "axis": {"format": "d"}},
                           "x2": {"field": "to_plus"}}})
    return s


def nodata_spec(brand, title, reason, source):
    s = _base(brand, title, reason, source)
    s.update({"data": {"values": [{"t": "No data available"}]}, "usermeta": {"nodata": True},
              "mark": {"type": "text", "fontSize": 54, "color": "#888888"}, "encoding": {"text": {"field": "t"}}})
    return s


# ------------------------------------------------------------------------------ rendering
def render_png(spec: dict[str, Any], out: Path, scale: float = 1.0) -> Path:
    import vl_convert as vlc
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(vlc.vegalite_to_png(json.dumps(spec), scale=scale))
    return out


def render_svg(spec: dict[str, Any], out: Path) -> Path:
    import vl_convert as vlc
    out.write_text(vlc.vegalite_to_svg(json.dumps(spec)), encoding="utf-8")
    return out


def _frames_for(spec: dict[str, Any], frames: int, mode: str) -> list[dict[str, Any]]:
    """Progressive-reveal variants of a spec."""
    out = []
    data_holder = spec.get("data", {})
    rows = data_holder.get("values", [])
    n = len(rows)
    for i in range(frames):
        t = (i + 1) / frames
        e = 1 - (1 - t) ** 3          # ease-out
        f = json.loads(json.dumps(spec))
        if mode == "grow":            # bars grow: scale values
            field = _value_field(spec)
            for r in f["data"]["values"]:
                if isinstance(r.get(field), (int, float)):
                    r[field] = r[field] * e
            # keep axis fixed to the final domain
            _fix_domain(f, field, rows)
        elif mode == "draw":          # line draws left to right
            k = max(1, math.ceil(e * n))
            f["data"]["values"] = rows[:k]
            field = _value_field(spec)
            _fix_domain(f, field, rows, x_rows=rows)
        elif mode == "fade":
            pass
        out.append(f)
    return out


def _value_field(spec):
    enc = spec.get("encoding") or (spec.get("layer") or [{}])[-1].get("encoding") or {}
    for axis in ("y", "x"):
        if enc.get(axis, {}).get("type") == "quantitative":
            return enc[axis]["field"]
    return "value"


def _fix_domain(f, field, rows, x_rows=None):
    vals = [r[field] for r in rows if isinstance(r.get(field), (int, float))]
    if not vals:
        return
    dom = [0, max(vals) * 1.05]
    targets = [f] if "encoding" in f else f.get("layer", [])
    for layer in targets:
        enc = layer.setdefault("encoding", {})
        for axis in ("y", "x"):
            if enc.get(axis, {}).get("field") == field:
                enc[axis].setdefault("scale", {})["domain"] = dom
            elif x_rows is not None and axis in enc and enc[axis].get("type") == "ordinal":
                xf = enc[axis]["field"]
                enc[axis].setdefault("scale", {})["domain"] = [r[xf] for r in x_rows]


def render_mp4(spec: dict[str, Any], out: Path, seconds: float = 4.0, fps: int = 30, mode: str = "grow",
               hold: float = 1.5, progress=None) -> Path:
    import vl_convert as vlc
    anim = max(0.5, seconds - hold)
    frames = int(anim * fps)
    tmp = Path(tempfile.mkdtemp(prefix="chart_"))
    try:
        variants = _frames_for(spec, frames, mode)
        for i, v in enumerate(variants):
            (tmp / f"f{i:04d}.png").write_bytes(vlc.vegalite_to_png(json.dumps(v), scale=1.0))
            if progress and i % 10 == 0:
                progress(i / frames)
        last = tmp / f"f{frames - 1:04d}.png"
        for j in range(int(hold * fps)):
            shutil.copy(last, tmp / f"f{frames + j:04d}.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-framerate", str(fps), "-i", str(tmp / "f%04d.png"),
                        "-vf", f"scale={W}:{H}:force_original_aspect_ratio=decrease,pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=white,fade=t=in:st=0:d=0.4,format=yuv420p",
                        "-c:v", "libx264", "-preset", "fast", "-crf", "18", "-r", str(fps), str(out)], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return out


# ------------------------------------------------------------------------------ chart set for a profile
def profile_charts(profile: dict[str, Any] | None, brand: dict[str, Any], guest: str, reason: str = "Guest not found in OpenAlex") -> list[dict[str, Any]]:
    """Return [{"key","title","spec","anim","cues":[...]}] - cues are transcript phrases that
    suggest where the chart belongs."""
    src = f"{profile['source']}, retrieved {profile['retrieved']}" if profile else "no source"
    charts = []

    def add(key, title, spec, anim, cues):
        charts.append({"key": key, "title": title, "spec": spec, "anim": anim, "cues": cues})

    if not profile:
        add("papers", f"Publications by {guest}", nodata_spec(brand, f"Publications by {guest}", reason, src), "fade", [])
        return charts
    ppy = profile.get("papers_per_year") or []
    if ppy:
        add("papers_per_year", "Papers published per year",
            bar_spec(brand, f"{profile['name']}: papers per year", f"{profile.get('works_count', len(ppy))} works indexed", src, ppy, "year", "papers", "", "Papers"),
            "grow", ["first paper", "published", "publications", "papers", "phd", "doctorate", "postdoc", "productive"])
    else:
        add("papers_per_year", "Papers published per year", nodata_spec(brand, "Papers per year", "No publication years in OpenAlex", src), "fade", [])
    cpy = profile.get("citations_per_year") or []
    if cpy:
        add("citation_growth", "Citation growth",
            line_spec(brand, f"{profile['name']}: cumulative citations", f"{profile.get('cited_by_count', '')} citations, h-index {profile.get('h_index', '?')}", src, cpy, "year", "cumulative", "", "Cumulative citations"),
            "draw", ["cited", "citations", "impact", "influential", "most cited", "h-index", "took off"])
    else:
        add("citation_growth", "Citation growth", nodata_spec(brand, "Citation growth", "OpenAlex has no citation history", src), "fade", [])
    co = profile.get("top_coauthors") or []
    if co:
        add("coauthors", "Top co-authors",
            bar_spec(brand, f"{profile['name']}: most frequent co-authors", "Shared publications", src, co, "name", "papers", "", "Papers together", horizontal=True),
            "grow", ["collaborat", "co-author", "coauthor", "colleague", "supervisor", "advisor", "mentor", "worked with", "my team", "lab"])
    tp = profile.get("topics") or []
    if tp:
        add("topics", "Research topics",
            bar_spec(brand, f"{profile['name']}: research topics", "Primary topic of each paper (OpenAlex topics)", src, tp, "topic", "papers", "", "Papers", horizontal=True),
            "grow", ["research area", "my work", "focus", "interested in", "field", "topic", "study", "research question"])
    inst = profile.get("institutions") or []
    if inst:
        rows = [{**r, "to_plus": r["to"] + 1} for r in inst]
        add("institutions", "Institutions over time",
            timeline_spec(brand, f"{profile['name']}: institutions over time", "Years with an affiliated publication", src, rows),
            "fade", ["moved to", "joined", "university", "institute", "college", "position", "appointed", "professor", "lecturer", "fellowship"])
    return charts


# ------------------------------------------------------------------------------ custom data
def custom_chart(brand: dict[str, Any], title: str, rows: list[dict[str, Any]], x: str, y: str, kind: str = "bar",
                 source: str = "Guest-provided data", subtitle: str = "") -> dict[str, Any]:
    if kind == "line":
        spec = line_spec(brand, title, subtitle, source, rows, x, y, x, y)
        anim = "draw"
    else:
        spec = bar_spec(brand, title, subtitle, source, rows, x, y, x, y, horizontal=(kind == "barh"))
        anim = "grow"
    return {"key": "custom_" + "".join(c for c in title.lower() if c.isalnum())[:30], "title": title, "spec": spec, "anim": anim, "cues": []}


def rows_from_csv(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    import csv, io
    rdr = csv.DictReader(io.StringIO(text.strip()))
    rows = []
    for r in rdr:
        row = {}
        for k, v in r.items():
            if k is None:
                continue
            v = (v or "").strip()
            try:
                row[k.strip()] = float(v) if "." in v else int(v)
            except ValueError:
                row[k.strip()] = v
        rows.append(row)
    return rows, [c.strip() for c in (rdr.fieldnames or [])]


def rows_from_pasted(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """'2019: 3, 2020: 5' or '2019 3\\n2020 5' -> rows with columns x,y."""
    import re
    rows = []
    for line in re.split(r"[\n;,]+", text.strip()):
        m = re.match(r"\s*([^\s:=]+)\s*[:=\s]\s*(-?[\d.]+)\s*$", line)
        if m:
            x, y = m.group(1), m.group(2)
            rows.append({"x": int(x) if x.isdigit() else x, "y": float(y) if "." in y else int(y)})
    return rows, ["x", "y"]
