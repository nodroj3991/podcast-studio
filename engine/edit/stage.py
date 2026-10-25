"""Stage functions: ``edit_plan`` (decisions.json) and ``edl`` (edl.json + previews)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.project import Project, read_json, write_json
from ..core.runner import Context
from .schema import load_transcript, parse_transcript
from .silence import find_silences, silences_to_json
from .fillers import find_fillers, fillers_to_json, DEFAULT_FILLERS
from .chapters import find_chapters, chapters_to_json
from .camera import plan_camera
from .textedit import ensure_word_ids
from . import graphics as G
from .edl import build_edl, write_edl


def edit_plan(project: Project, ctx: Context) -> None:
    meta = project.load()
    s = ctx.settings
    raw = read_json(project.transcript)
    ensure_word_ids(raw)
    write_json(project.transcript, raw)
    tr = parse_transcript(raw)
    existing = read_json(project.decisions, {}) or {}

    ctx.progress(0.1, "Finding silences")
    mix = Path(meta["mix_wav"]) if meta.get("mix_wav") else None
    silences = find_silences(tr, min_gap=float(s.get("min_silence", 1.2)), pad=float(s.get("pad", 0.25)),
                             audio_path=mix, use_audio=bool(s.get("verify_silence_with_audio", True)))
    ctx.progress(0.35, "Listing filler words")
    fillers = find_fillers(tr, s.get("filler_words") or DEFAULT_FILLERS)
    ctx.progress(0.55, "Detecting chapters")
    labels = tr.speaker_labels()
    host_label = next((lab for lab, name in tr.speakers.items() if name == s.get("host_name")), labels[0] if labels else None)
    chapters = find_chapters(tr, min_chapter_s=float(s.get("min_chapter_s", 240)), sensitivity=float(s.get("chapter_sensitivity", 0.5)),
                             host_label=host_label)
    ctx.progress(0.7, "Planning camera")
    camera = plan_camera(tr, meta.get("files", []), meta.get("layout", "single"),
                         min_shot=float(s.get("min_shot", 2.5)), zoom=float(s.get("punch_in_zoom", 1.12)))

    # keep any human decisions already made (re-running the plan must not throw away review work)
    keep_ids = {f["id"]: f["enabled"] for f in existing.get("fillers", [])}
    for f in fillers:
        if f.id in keep_ids:
            f.enabled = keep_ids[f.id]
    keep_sil = {x["id"]: x["enabled"] for x in existing.get("silences", [])}
    for x in silences:
        if x.id in keep_sil:
            x.enabled = keep_sil[x.id]
    manual_ch = [c for c in existing.get("chapters", []) if not c.get("auto", True)]

    decisions = {
        "settings": {"min_silence": float(s.get("min_silence", 1.2)), "pad": float(s.get("pad", 0.25)),
                     "remove_fillers": bool(s.get("remove_fillers", True))},
        "silences": silences_to_json(silences),
        "fillers": fillers_to_json(fillers),
        "deletions": existing.get("deletions", []),
        "speakers": existing.get("speakers") or raw.get("speakers", {}),
        "chapters": (manual_ch or chapters_to_json(chapters)),
        "camera": camera,
        "transitions": existing.get("transitions") or {"type": s.get("transition", "fade"), "duration": float(s.get("transition_s", 0.6)),
                                                       "chapter_cards": bool(s.get("chapter_cards", True))},
        "titles": existing.get("titles") or {"intro": True, "lower_third": True, "intro_seconds": 5.0, "lower_third_seconds": 6.0},
        "summary": {"silences": len(silences), "silence_seconds": round(sum(x.end - x.start for x in silences if x.enabled), 1),
                    "fillers": len(fillers), "chapters": len(chapters), "camera_mode": camera["mode"]},
    }
    write_json(project.decisions, decisions)
    ctx.progress(0.85, "Drawing title cards")
    make_graphics(project, meta, decisions, s)


def make_graphics(project: Project, meta: dict[str, Any], decisions: dict[str, Any], s: dict[str, Any]) -> dict[str, Any]:
    brand = s.get("brand") or {}
    guest = meta.get("guest") or {}
    title = meta.get("title") or f"In conversation with {guest.get('name', '')}".strip()
    g = project.graphics
    out = {"intro_png": str(G.title_card(g / "title_card.png", title, guest.get("name", ""),
                                         ", ".join(x for x in (guest.get("title"), guest.get("institution")) if x), brand,
                                         meta.get("episode_label", ""))),
           "lower_third_png": str(G.lower_third(g / "lower_third.png", guest.get("name", ""),
                                                ", ".join(x for x in (guest.get("title"), guest.get("institution")) if x), brand)),
           "chapter_cards": {}}
    if decisions.get("transitions", {}).get("chapter_cards", True):
        for i, ch in enumerate(decisions.get("chapters", [])):
            if i == 0:
                continue
            out["chapter_cards"][ch["id"]] = str(G.chapter_card(g / f"chapter_{ch['id']}.png", i + 1, ch["title"], brand))
    out["thumbnail_png"] = str(G.thumbnail(g / "thumbnail.jpg", title, guest.get("name", ""), brand, meta.get("still")))
    decisions["graphics"] = out
    write_json(project.decisions, decisions)
    return out


def edl(project: Project, ctx: Context) -> None:
    meta = project.load()
    tr = load_transcript(project.transcript)
    decisions = read_json(project.decisions)
    if decisions.get("graphics") is None or any(not Path(p).exists() for p in [decisions["graphics"].get("intro_png", "")]):
        make_graphics(project, meta, decisions, ctx.settings)
    music = read_json(project.music_dir / "music.json", {}) or {}
    charts = read_json(project.charts_dir / "placements.json", []) or []
    e = build_edl(tr, decisions, meta.get("files", []), meta.get("layout", "single"), music, charts, decisions.get("graphics"))
    e["titles"] = {**decisions.get("titles", {}), **decisions.get("graphics", {})}
    write_edl(e, project.edl_json, project.edit_dir / "edl.txt", project.edit_dir / "edl.csv")
    meta["duration_edited"] = e["duration_edited"]
    project.save(meta)
    ctx.log.info("EDL: %d clips, edited %.1fs of %.1fs", len(e["clips"]), e["duration_edited"], e["duration_source"])
