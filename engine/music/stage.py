"""Music stage: search by mood/length, licence-gate, download, write attribution.

Produces ``music/music.json``::

    {"intro": track|null, "outro": track|null, "bed": track|null,
     "bed_gain_db": -22, "duck_db": -12, "candidates": [...with verdicts...]}

and ``music/attribution.txt`` ready to paste into a video description.

Offline / no key: the stage writes an empty selection and the render runs without music;
the user can also drop their own files into ``music/own/`` (intro.mp3, outro.mp3, bed.mp3) -
those are used as-is with attribution "own licence" (the host is responsible for them).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.project import Project, write_json, read_json
from ..core.runner import Context
from . import providers as P
from .license import check


def search_all(settings: dict[str, Any], mood: str, min_s: int, max_s: int, log=None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    errors = []
    if settings.get("jamendo_client_id"):
        try:
            results += P.jamendo_search(settings["jamendo_client_id"], mood, min_s, max_s)
        except Exception as e:  # noqa: BLE001
            errors.append(f"jamendo: {e}")
    try:
        results += P.openverse_search(mood, min_s, max_s)
    except Exception as e:  # noqa: BLE001
        errors.append(f"openverse: {e}")
    if log and errors:
        log.warning("music search errors: %s", errors)
    allow_sa = bool(settings.get("allow_sharealike", False))
    for t in results:
        v = check(t.get("license"), t.get("license_url"), allow_sa)
        t["verdict"] = {"ok": v.ok, "code": v.code, "reason": v.reason, "attribution_required": v.attribution_required}
        if not t.get("download_allowed", True):
            t["verdict"] = {"ok": False, "code": v.code, "reason": "The artist has disabled downloads for this track.", "attribution_required": True}
    return results


def pick(results: list[dict[str, Any]], want_s: float, exclude: set[str] | None = None) -> dict[str, Any] | None:
    ok = [t for t in results if t["verdict"]["ok"] and t.get("download") and t["id"] not in (exclude or set())]
    if not ok:
        ok = [t for t in results if t["verdict"]["ok"] and t.get("download")]
    if not ok:
        return None
    return min(ok, key=lambda t: abs(t["duration"] - want_s))


def music(project: Project, ctx: Context) -> None:
    s = ctx.settings
    mood = s.get("music_mood", "curious")
    sel = read_json(project.music_dir / "music.json", {}) or {}
    own = project.music_dir / "own"
    result: dict[str, Any] = {"mood": mood, "intro": None, "outro": None, "bed": None,
                              "bed_gain_db": float(s.get("bed_gain_db", -22)), "duck_db": float(s.get("duck_db", -12)),
                              "candidates": [], "attribution": []}
    # user-chosen tracks from the review screen survive re-runs
    for key in ("intro", "outro", "bed"):
        if sel.get(key) and sel[key].get("file") and Path(sel[key]["file"]).exists() and sel[key].get("user_chosen"):
            result[key] = sel[key]
    # own files override everything
    for key in ("intro", "outro", "bed"):
        for ext in (".mp3", ".wav", ".m4a", ".aac", ".flac"):
            f = own / f"{key}{ext}"
            if f.exists():
                result[key] = {"title": f.stem, "artist": "(your own file)", "file": str(f), "license": "own",
                               "attribution": "", "provider": "own", "user_chosen": True}
    if not s.get("music_enabled", True):
        write_json(project.music_dir / "music.json", result)
        return

    needs = [k for k in ("intro", "outro", "bed") if result[k] is None and s.get(f"music_{k}", k != "bed" or s.get("music_bed", False))]
    if needs:
        ctx.progress(0.1, f"Searching {mood} music")
        try:
            short = search_all(s, mood, 15, 90, ctx.log) if any(k in needs for k in ("intro", "outro")) else []
            long = search_all(s, mood, 120, 600, ctx.log) if "bed" in needs else []
        except Exception as e:  # noqa: BLE001
            ctx.log.warning("music search unavailable (%s) - continuing without music", e)
            short, long = [], []
        result["candidates"] = short + long
        used = {v["id"] for v in (result["intro"], result["outro"], result["bed"]) if v and v.get("id")}
        for key in needs:
            pool = long if key == "bed" else short
            t = pick(pool, 30 if key != "bed" else 300, used)
            if t:
                used.add(t["id"])
            if not t:
                ctx.log.info("no licence-cleared %s track found", key)
                continue
            dst = project.music_dir / f"{key}_{t['provider']}_{t['id']}.mp3"
            ctx.progress(0.4, f"Downloading {t['title']}")
            try:
                if not dst.exists():
                    P.download(t["download"], dst)
                t = {**t, "file": str(dst)}
                result[key] = t
            except Exception as e:  # noqa: BLE001
                ctx.log.warning("download failed for %s: %s", t.get("title"), e)
    # attribution
    lines = []
    for key in ("intro", "outro", "bed"):
        t = result.get(key)
        if t and t.get("attribution"):
            lines.append(f"{key.title()} music: {t['attribution']}")
    result["attribution"] = lines
    (project.music_dir / "attribution.txt").write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    write_json(project.music_dir / "music.json", result)
