"""Final timeline: EDL clips + intro card + chapter cards + transitions + overlay windows.

Three timelines exist:
* **source** - the raw recording (what the transcript is timed to)
* **record / rec** - after cuts only (what ``edl.json`` and chart placements use)
* **final** - rec plus inserted intro/chapter cards, minus xfade overlaps (what the video,
  captions and chapter timestamps use)

``build()`` returns a list of *items* in final order::

    {"kind": "intro"|"card"|"clip"|"junction", "final_in", "final_out", ... clip fields ...,
     "fade_in": 0.0, "fade_out": 0.6, "lower_third": {...}|None, "chart": {...}|None}

and a ``rec_to_final`` mapping (list of (rec_start, rec_end, final_start)).
"""
from __future__ import annotations

from typing import Any

CARD_SECONDS = 2.5


def _split_at(clips: list[dict[str, Any]], times: set[float]) -> list[dict[str, Any]]:
    out = []
    for c in clips:
        pts = sorted(t for t in times if c["rec_in"] + 0.05 < t < c["rec_out"] - 0.05)
        cur = dict(c)
        for t in pts:
            left = dict(cur)
            left["rec_out"] = t
            left["src_out"] = cur["src_in"] + (t - cur["rec_in"])
            out.append(left)
            cur = dict(cur)
            cur["rec_in"], cur["src_in"] = t, left["src_out"]
        out.append(cur)
    return out


def build(edl: dict[str, Any], settings: dict[str, Any]) -> dict[str, Any]:
    titles = edl.get("titles") or {}
    intro_s = float(titles.get("intro_seconds", 5.0)) if titles.get("intro", True) and titles.get("intro_png") else 0.0
    lt_s = float(titles.get("lower_third_seconds", 6.0)) if titles.get("lower_third", True) and titles.get("lower_third_png") else 0.0
    lt_start = 1.0
    charts = [c for c in edl.get("charts", []) if c.get("enabled") and c.get("rec_in") is not None]
    chapters = edl.get("chapters", [])
    transitions = {t["rec_time"]: t for t in edl.get("transitions", [])}
    cards = {ch["id"]: p for ch, p in [(ch, (titles.get("chapter_cards") or {}).get(ch["id"])) for ch in chapters] if p}
    use_cards = bool(edl.get("transitions") and settings.get("chapter_cards", True))

    split_pts = {t for t in transitions} | {lt_start, lt_start + lt_s}
    for c in charts:
        split_pts |= {float(c["rec_in"]), float(c["rec_out"])}
    clips = _split_at(edl["clips"], split_pts)

    items: list[dict[str, Any]] = []
    mapping: list[tuple[float, float, float]] = []
    final = 0.0
    if intro_s:
        items.append({"kind": "intro", "final_in": 0.0, "final_out": intro_s, "png": titles["intro_png"], "dur": intro_s})
        final = intro_s
    for i, c in enumerate(clips):
        rec_in, rec_out = float(c["rec_in"]), float(c["rec_out"])
        tr = transitions.get(rec_in)
        fade_in = fade_out = 0.0
        if tr and i > 0:
            ch_id = tr.get("chapter_id")
            kind = tr.get("type", "fade")
            d = float(tr.get("duration", 0.6))
            if use_cards and ch_id in cards:
                items[-1]["fade_out"] = d
                items.append({"kind": "card", "final_in": final, "final_out": final + CARD_SECONDS, "png": cards[ch_id],
                              "dur": CARD_SECONDS, "chapter_id": ch_id})
                final += CARD_SECONDS
                fade_in = d
            elif kind == "fade":
                items[-1]["fade_out"] = d
                fade_in = d
            else:
                # xfade junction: overlap the previous item's tail and this clip's head by d
                prev = items[-1]
                prev["trim_tail"] = d
                prev["final_out"] -= d
                final -= d
                items.append({"kind": "junction", "type": kind, "dur": d, "final_in": final, "final_out": final + d,
                              "a": {"source": prev["source"], "src_in": prev["src_out"] - d, "zoom": prev["zoom"], "pan": prev.get("pan", 0.0)},
                              "b": {"source": c["source"], "src_in": float(c["src_in"]), "zoom": c["zoom"], "pan": c.get("pan", 0.0)}})
                final += d
                c = {**c, "src_in": float(c["src_in"]) + d, "rec_in": rec_in + d}
                rec_in += d
        dur = rec_out - rec_in
        item = {"kind": "clip", **c, "src_in": float(c["src_in"]), "src_out": float(c["src_out"]), "rec_in": rec_in, "rec_out": rec_out,
                "dur": dur, "final_in": final, "final_out": final + dur, "fade_in": fade_in, "fade_out": fade_out,
                "lower_third": None, "chart": None}
        if lt_s and lt_start <= rec_in < lt_start + lt_s - 0.01:
            item["lower_third"] = {"png": titles["lower_third_png"], "offset": rec_in - lt_start, "total": lt_s,
                                   "slide_in": abs(rec_in - lt_start) < 0.01, "fade_out": abs(rec_out - (lt_start + lt_s)) < 0.01}
        for ch in charts:
            if float(ch["rec_in"]) <= rec_in < float(ch["rec_out"]) - 0.01:
                item["chart"] = {"mp4": ch["mp4"], "png": ch["png"], "offset": rec_in - float(ch["rec_in"]),
                                 "total": float(ch["rec_out"]) - float(ch["rec_in"]), "title": ch.get("title", ""),
                                 "fade_in": abs(rec_in - float(ch["rec_in"])) < 0.01, "fade_out": abs(rec_out - float(ch["rec_out"])) < 0.01}
        mapping.append((rec_in, rec_out, final))
        items.append(item)
        final += dur
    for n, it in enumerate(items):
        it["n"] = n
    chapter_final = []
    for ch in chapters:
        chapter_final.append({**ch, "final_time": round(rec_to_final(float(ch["rec_time"]), mapping, items), 3)})
    return {"items": items, "mapping": mapping, "duration": round(final, 3), "chapters": chapter_final,
            "intro_seconds": intro_s, "music_sections": music_sections(items)}


def rec_to_final(t: float, mapping: list[tuple[float, float, float]], items: list[dict[str, Any]] | None = None) -> float:
    for rin, rout, fin in mapping:
        if rin - 1e-6 <= t <= rout + 1e-6:
            return fin + (t - rin)
    # fell in a trimmed overlap or before the first clip: snap to the nearest clip start
    best = min(mapping, key=lambda m: abs(m[0] - t), default=(0.0, 0.0, 0.0))
    return best[2]


def music_sections(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Where music goes on the final timeline."""
    intro = next((i for i in items if i["kind"] == "intro"), None)
    cards = [i for i in items if i["kind"] == "card"]
    return {"intro_end": intro["final_out"] if intro else 0.0,
            "cards": [(c["final_in"], c["final_out"]) for c in cards],
            "end": items[-1]["final_out"] if items else 0.0}
