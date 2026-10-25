"""Stages: render_preview, render_final, export, qa, package."""
from __future__ import annotations

import json
import shutil
import subprocess
import zipfile
from pathlib import Path
from typing import Any

from ..core.project import Project, read_json, write_json
from ..core.runner import Context, UserFacingError
from ..edit.schema import load_transcript
from ..edit.cuts import kept_ranges, words_in_edit
from ..edit.audio import ebur128_stats
from ..ingest.exports import paragraphs, write_txt, write_docx
from . import timeline as TL
from . import video as V
from . import audio_mix as A
from . import captions as CAP


def _timeline(project: Project, settings: dict[str, Any]) -> dict[str, Any]:
    edl = read_json(project.edl_json)
    if not edl:
        raise UserFacingError("The edit list is missing.", "Run the Edit step again.")
    tl = TL.build(edl, settings)
    write_json(project.work / "timeline.json", tl)
    return tl


def _render(project: Project, ctx: Context, profile: str) -> Path:
    meta = project.load()
    edl = read_json(project.edl_json)
    tl = _timeline(project, ctx.settings)
    items = tl["items"]
    sources = edl["sources"]
    work = project.work / f"render_{profile}"
    work.mkdir(parents=True, exist_ok=True)
    done_marker = work / "done.json"
    prefer_hw = bool(ctx.settings.get("use_hardware_encoder", True))
    fallback_png = (edl.get("titles") or {}).get("intro_png")
    # per-item cache keyed by a hash of the item, so a resumed render skips finished clips
    parts: list[Path] = []
    total = len(items)
    for k, it in enumerate(items):
        key = json.dumps({x: it.get(x) for x in it if x not in ("n", "final_in", "final_out")}, sort_keys=True)
        import hashlib
        h = hashlib.md5(key.encode()).hexdigest()[:10]
        out = work / f"{k:05d}_{it['kind']}_{h}.mp4"
        parts.append(out)
        if out.exists() and out.stat().st_size > 0:
            continue
        ctx.progress(0.05 + 0.75 * k / total, f"Clip {k + 1} of {total}")
        tmp = out.with_suffix(".part.mp4")
        if it["kind"] in ("intro", "card"):
            V.encode_card(it["png"], it["dur"], tmp, profile, prefer_hw=prefer_hw, log=ctx.log)
        elif it["kind"] == "junction":
            V.encode_junction(it, sources, tmp, profile, prefer_hw, ctx.log)
        else:
            V.encode_clip(it, sources, tmp, profile, fallback_png, prefer_hw, ctx.log)
        tmp.replace(out)
    ctx.progress(0.82, "Joining clips", force=True)
    video_only = work / "video_only.mp4"
    V.concat(parts, video_only, ctx.log)

    # ---- audio (shared between preview and final: same timeline)
    ctx.progress(0.85, "Mixing audio", force=True)
    mix = A.full_mix(sources, project.work / "mix_48k.wav", ctx.log)
    voice = A.voice(mix, project.work / "voice_48k.wav", ctx.settings, ctx.log)
    speech = A.speech_track(voice, items, work / "speech.wav", work, ctx.log)
    music = read_json(project.music_dir / "music.json", {}) or {}
    mtrack = A.music_track(music, tl["music_sections"], tl["duration"], speech, work / "music.wav", work, ctx.log)
    ctx.progress(0.92, "Setting loudness to -14 LUFS", force=True)
    measured = A.master(speech, mtrack, work / "final.wav", ctx.log)
    ctx.log.info("loudness pass-1 measured: %s", measured)

    ctx.progress(0.96, "Writing video file", force=True)
    chapters_meta = _chapter_metadata(tl, work / "chapters.ffmeta")
    out = project.renders / ("preview.mp4" if profile == "preview" else "episode_1080p.mp4")
    V.mux(video_only, work / "final.wav", out, chapters_meta, ctx.log)
    write_json(done_marker, {"output": str(out), "duration": tl["duration"]})
    meta["duration_final"] = tl["duration"]
    project.save(meta)
    return out


def _chapter_metadata(tl: dict[str, Any], path: Path) -> Path:
    lines = [";FFMETADATA1"]
    chs = tl["chapters"]
    for i, ch in enumerate(chs):
        start = int(ch["final_time"] * 1000)
        end = int((chs[i + 1]["final_time"] if i + 1 < len(chs) else tl["duration"]) * 1000)
        lines += ["[CHAPTER]", "TIMEBASE=1/1000", f"START={start}", f"END={max(end, start + 1)}", f"title={ch['title']}"]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def render_preview(project: Project, ctx: Context) -> None:
    _render(project, ctx, "preview")


def render_final(project: Project, ctx: Context) -> None:
    _render(project, ctx, "final")


# ------------------------------------------------------------------------------ export
def export(project: Project, ctx: Context) -> None:
    meta = project.load()
    s = ctx.settings
    tr = load_transcript(project.transcript)
    decisions = read_json(project.decisions, {}) or {}
    tl = read_json(project.work / "timeline.json") or _timeline(project, s)
    kept = kept_ranges(tr, decisions)
    speakers = decisions.get("speakers") or tr.speakers
    ctx.progress(0.1, "Timing captions to the edited video")
    words = words_in_edit(tr, kept)
    for w in words:
        w["start"] = round(TL.rec_to_final(w["start"], [tuple(m) for m in tl["mapping"]]), 3)
        w["end"] = round(max(TL.rec_to_final(w["end"], [tuple(m) for m in tl["mapping"]]), w["start"] + 0.02), 3)
    cues = CAP.build_cues(words)
    (project.captions / "episode.srt").write_text(CAP.to_srt(cues), encoding="utf-8")
    (project.captions / "episode.vtt").write_text(CAP.to_vtt(cues, speakers, voice_tags=bool(s.get("vtt_voice_tags", False))), encoding="utf-8")
    write_json(project.captions / "cues.json", cues)
    write_json(project.captions / "words_final.json", words)

    ctx.progress(0.4, "Writing transcript")
    paras = paragraphs([{**w, "word": w["text"]} for w in words], speakers)
    title = meta.get("title") or "Transcript"
    guest = meta.get("guest") or {}
    write_txt(paras, project.transcript_dir / "transcript_edited.txt", title)
    write_docx(paras, project.transcript_dir / "transcript_edited.docx", title,
               {"Guest": guest.get("name"), "Institution": guest.get("institution"), "Note": "Timestamps match the edited video"})

    ctx.progress(0.6, "Writing chapters and description")
    chs = [dict(c) for c in tl["chapters"]]
    if chs:
        chs[0]["final_time"] = 0.0                 # the first chapter covers the title card
    ch_lines = [f"{_yt(ch['final_time'])} {ch['title']}" for ch in chs]
    (project.output / "chapters.txt").write_text("\n".join(ch_lines) + "\n", encoding="utf-8")
    music = read_json(project.music_dir / "music.json", {}) or {}
    charts = read_json(project.charts_dir / "placements.json", []) or []
    profile = (read_json(project.charts_dir / "profile.json", {}) or {}).get("profile") or {}
    desc = []
    if guest.get("name"):
        desc.append(f"In this episode: {guest['name']}" + (f", {guest['title']}" if guest.get("title") else "") + (f" at {guest['institution']}" if guest.get("institution") else "") + ".")
        desc.append("")
    desc += ["Chapters:"] + ch_lines + [""]
    if music.get("attribution"):
        desc += ["Music:"] + music["attribution"] + [""]
    if any(c.get("enabled") for c in charts):
        desc += ["Research data shown in charts: " + (f"{profile.get('source', 'OpenAlex')} ({profile.get('source_url', 'https://openalex.org')}), retrieved {profile.get('retrieved', '')}" if profile else "guest-provided data"), ""]
    (project.output / "description.txt").write_text("\n".join(desc), encoding="utf-8")

    if s.get("burn_in_captions") and (project.renders / "episode_1080p.mp4").exists():
        ctx.progress(0.7, "Rendering burned-in caption version", force=True)
        src = project.renders / "episode_1080p.mp4"
        # cues that overlap the lower-third window are raised to the top ({\an8}) in a burn-in copy
        lt_end = float(tl.get("intro_seconds", 0)) + 1.0 + float((read_json(project.edl_json, {}).get("titles") or {}).get("lower_third_seconds", 6.0))
        burn_cues = [{**c, "lines": ["{\\an8}" + c["lines"][0]] + c["lines"][1:]} if c["start"] < lt_end + 0.5 else c for c in cues]
        (project.captions / "episode_burn.srt").write_text(CAP.to_srt(burn_cues), encoding="utf-8")
        srt = (project.captions / "episode_burn.srt").as_posix().replace(":", "\\:").replace("'", "\\'")
        style = "FontName=Inter,FontSize=20,PrimaryColour=&H00FFFFFF,OutlineColour=&H80000000,BorderStyle=4,BackColour=&H80000000,Outline=0,Shadow=0,MarginV=40"
        V._run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vf", f"subtitles='{srt}':force_style='{style}'",
                *V.encoder_args("final", bool(s.get("use_hardware_encoder", True)), ctx.log), "-pix_fmt", "yuv420p", "-c:a", "copy",
                "-movflags", "+faststart", str(project.renders / "episode_1080p_captions.mp4")], ctx.log)
    ctx.progress(1.0, "Exports written", force=True)


def _yt(t: float) -> str:
    t = int(t)
    return f"{t // 3600}:{t % 3600 // 60:02d}:{t % 60:02d}" if t >= 3600 else f"{t // 60:02d}:{t % 60:02d}"


# ------------------------------------------------------------------------------ QA
def qa(project: Project, ctx: Context) -> None:
    report: list[str] = []
    ok = True
    final = project.renders / "episode_1080p.mp4"
    tl = read_json(project.work / "timeline.json") or {}
    target = project.renders / "preview.mp4" if not final.exists() else final
    if not target.exists():
        raise UserFacingError("No rendered video was found.", "Run the render step again.")

    def check(cond: bool, good: str, bad: str):
        nonlocal ok
        report.append(("PASS  " if cond else "FAIL  ") + (good if cond else bad))
        ok = ok and cond

    ctx.progress(0.1, "Checking the video file")
    probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(target)], text=True))
    dur = float(probe["format"].get("duration", 0))
    vs = next((s for s in probe["streams"] if s["codec_type"] == "video"), {})
    aus = next((s for s in probe["streams"] if s["codec_type"] == "audio"), {})
    check(vs.get("codec_name") == "h264" and aus.get("codec_name") == "aac", f"H.264 video + AAC audio, {vs.get('width')}x{vs.get('height')}", "Unexpected codecs")
    exp = float(tl.get("duration", dur))
    check(abs(dur - exp) < 0.6, f"Duration {dur:.1f}s matches the edit list ({exp:.1f}s)", f"Duration {dur:.1f}s differs from edit list {exp:.1f}s")
    # decode test: decode everything, count errors
    r = subprocess.run(["ffmpeg", "-hide_banner", "-v", "error", "-i", str(target), "-f", "null", "-"], capture_output=True, text=True)
    check(r.returncode == 0 and not r.stderr.strip(), "File decodes cleanly end to end", f"Decode errors: {r.stderr[:200]}")

    ctx.progress(0.4, "Measuring loudness")
    st = ebur128_stats(target)
    lufs, tp = st.get("integrated_lufs"), st.get("true_peak_dbtp")
    check(lufs is not None and abs(lufs + 14.0) <= 1.0, f"Loudness {lufs} LUFS (target -14 +/-1)", f"Loudness {lufs} LUFS is off target")
    check(tp is not None and tp <= -0.8, f"True peak {tp} dBTP (no clipping)", f"True peak {tp} dBTP too hot")

    ctx.progress(0.6, "Looking for black frames")
    r = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(target), "-vf", "blackdetect=d=0.4:pic_th=0.98", "-an", "-f", "null", "-"],
                       capture_output=True, text=True)
    blacks = [l for l in r.stderr.splitlines() if "black_start" in l]
    # the intro/chapter cards fade from black for 0.5 s: allow blacks < 0.6s only at card starts
    card_starts = {round(i["final_in"], 1) for i in tl.get("items", []) if i["kind"] in ("intro", "card")}
    bad_blacks = []
    for l in blacks:
        import re
        m = re.search(r"black_start:([\d.]+) black_end:([\d.]+) black_duration:([\d.]+)", l)
        if m and not (round(float(m.group(1)), 1) in card_starts and float(m.group(3)) < 0.7):
            bad_blacks.append(f"{float(m.group(1)):.1f}s for {float(m.group(3)):.1f}s")
    check(not bad_blacks, "No unexpected black frames", "Black frames at " + ", ".join(bad_blacks[:5]))

    ctx.progress(0.75, "Checking cuts against words")
    tr = load_transcript(project.transcript)
    edl = read_json(project.edl_json) or {}
    mid_word = []
    for c in edl.get("clips", []):
        for b in (c["src_in"], c["src_out"]):
            for w in tr.words:
                if w.start + 0.03 < b < w.end - 0.03:
                    mid_word.append(f"{b:.2f}s inside '{w.text}'")
                    break
    check(not mid_word, f"All {len(edl.get('clips', []))} cut points fall between words", "Cuts inside words: " + "; ".join(mid_word[:4]))

    ctx.progress(0.85, "Checking captions")
    cues = read_json(project.captions / "cues.json", []) or []
    problems = CAP.check_cues(cues)
    check(cues and not problems, f"{len(cues)} captions, all within 2 lines x 42 chars and 1-7 s", "Caption problems: " + "; ".join(problems[:4]))
    last = max((c["end"] for c in cues), default=0)
    check(last <= dur + 0.2, "Captions end inside the video", f"Captions run {last - dur:.1f}s past the end of the video")
    # sync spot check: the words of 5 cues must lie inside their cue on the final timeline
    words_final = read_json(project.captions / "words_final.json", []) or []
    off = 0
    for cue in cues[:: max(1, len(cues) // 5)][:5]:
        inside = [w for w in words_final if cue["start"] - 0.05 <= w["start"] <= cue["end"] + 0.05]
        if not inside:
            off += 1
    check(off == 0, "Caption timing matches word timings", f"{off} sampled captions have no words inside their window")

    for name in ("episode.srt", "episode.vtt"):
        check((project.captions / name).exists(), f"{name} written", f"{name} missing")
    for name in ("transcript_edited.docx", "transcript_edited.txt"):
        check((project.transcript_dir / name).exists(), f"{name} written", f"{name} missing")

    report.insert(0, f"QA {'PASSED' if ok else 'FAILED'} - {target.name}")
    (project.output / "qa_report.txt").write_text("\n".join(report) + "\n", encoding="utf-8")
    ctx.log.info("\n".join(report))
    project.update(qa_passed=ok)
    if not ok and ctx.settings.get("qa_strict", False):
        raise UserFacingError("Quality check failed - see qa_report.txt in the Output folder.", "Open the report, fix the flagged item (usually in the review screen) and render again.")


# ------------------------------------------------------------------------------ package
def package(project: Project, ctx: Context) -> None:
    meta = project.load()
    out = project.output
    out.mkdir(exist_ok=True)
    slug = "".join(c if c.isalnum() or c in "-_ " else "" for c in (meta.get("title") or project.root.name)).strip().replace(" ", "_")[:60] or "episode"
    copies = [
        (project.renders / "episode_1080p.mp4", f"{slug}.mp4"),
        (project.renders / "episode_1080p_captions.mp4", f"{slug}_captions_burned_in.mp4"),
        (project.renders / "preview.mp4", f"{slug}_preview.mp4"),
        (project.captions / "episode.srt", f"{slug}.srt"),
        (project.captions / "episode.vtt", f"{slug}.vtt"),
        (project.transcript_dir / "transcript_edited.docx", f"{slug}_transcript.docx"),
        (project.transcript_dir / "transcript_edited.txt", f"{slug}_transcript.txt"),
        (project.graphics / "thumbnail.jpg", f"{slug}_thumbnail.jpg"),
        (project.music_dir / "attribution.txt", "music_attribution.txt"),
        (project.edit_dir / "edl.txt", "edit_decision_list.txt"),
    ]
    n = 0
    for src, name in copies:
        if name and src.exists() and src.is_file():
            shutil.copy2(src, out / name)
            n += 1
    charts_out = out / "charts"
    charts_out.mkdir(exist_ok=True)
    for p in project.charts_dir.glob("*.png"):
        shutil.copy2(p, charts_out / p.name)
    for p in project.charts_dir.glob("*.mp4"):
        shutil.copy2(p, charts_out / p.name)
    ctx.progress(0.6, "Zipping")
    zpath = project.root / f"{slug}_outputs.zip"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_STORED) as z:     # video is already compressed
        for p in out.rglob("*"):
            if p.is_file():
                z.write(p, p.relative_to(out))
    meta.update(output_folder=str(out), output_zip=str(zpath), outputs=sorted(str(p.relative_to(out)) for p in out.rglob("*") if p.is_file()))
    project.save(meta)
