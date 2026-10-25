"""Stage functions: ``ingest`` (probe + sync + mixdown) and ``transcribe``."""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any

from ..core.project import Project, write_json, read_json
from ..core.runner import Context, UserFacingError
from . import media as M
from . import transcribe as T
from .exports import paragraphs, write_txt, write_docx


def ingest(project: Project, ctx: Context) -> None:
    meta = project.load()
    inputs: list[dict[str, Any]] = meta.get("inputs") or []
    if not inputs:
        raise UserFacingError("No recording files were added to this episode.",
                              "Drop your MP4/MOV/WAV files on the New Episode screen and try again.")
    files: list[dict[str, Any]] = []
    for i, inp in enumerate(inputs):
        src = Path(inp["path"])
        if not src.exists():
            raise UserFacingError(f"The file {src.name} can't be found any more.",
                                  "It may have been moved or is on a drive that is not plugged in. Add it again.")
        ctx.progress(i / max(len(inputs), 1), f"Reading {src.name}")
        info = M.probe(src)
        if not info.has_audio:
            raise UserFacingError(f"{src.name} has no audio track.", "Check you exported the recording with sound.")
        # keep the original where it is (large), but record it; copy only if asked
        if ctx.settings.get("copy_media_into_project"):
            dst = project.media / src.name
            if not dst.exists():
                shutil.copy2(src, dst)
            info.path = str(dst)
        wav = M.extract_audio(Path(info.path), project.work / f"audio_{i:02d}.wav", sr=16000)
        files.append({**M.info_to_json(info), "role": inp.get("role", "auto"), "speaker": inp.get("speaker"),
                      "wav": str(wav), "offset": 0.0, "sync_confidence": None})

    # ---- sync separate files to the first one
    if len(files) > 1:
        ref = Path(files[0]["wav"])
        for f in files[1:]:
            ctx.progress(0.7, f"Syncing {Path(f['path']).name}")
            off, conf = M.sync_offset(ref, Path(f["wav"]))
            f["offset"], f["sync_confidence"] = round(off, 4), round(conf, 1)
            ctx.log.info("sync %s offset=%.3fs conf=%.1f", f["path"], off, conf)
            if conf < 6:
                f["sync_warning"] = "Low confidence - check the first minute of the preview for lip-sync."
    # ---- which files are per-speaker audio tracks?
    audio_tracks = [f for f in files if f["role"] in ("speaker", "auto")] if len(files) > 1 else files
    mix = project.work / "mix_16k.wav"
    ctx.progress(0.85, "Mixing audio")
    M.mix_down([Path(f["wav"]) for f in audio_tracks], [f["offset"] for f in audio_tracks], mix, sr=16000)
    mix_info = M.probe(mix)
    layout = "multicam" if sum(1 for f in files if f["has_video"]) > 1 else ("single" if any(f["has_video"] for f in files) else "audio_only")
    meta.update(files=files, mix_wav=str(mix), duration=mix_info.duration, layout=layout)
    project.save(meta)


def transcribe(project: Project, ctx: Context) -> None:
    meta = project.load()
    hw = meta.get("hardware") or {}
    s = ctx.settings
    model_name = s.get("whisper_model") or meta.get("whisper_model") or "small"
    device = hw.get("device", "cpu")
    compute = hw.get("compute_type", "int8")
    duration = float(meta.get("duration") or 0)
    vocab = T.load_vocabulary(Path(s["vocabulary_file"])) if s.get("vocabulary_file") else []
    vocab += list(s.get("vocabulary", []))
    guest = meta.get("guest") or {}
    for key in ("name", "institution", "title"):
        if guest.get(key):
            vocab.append(guest[key])
    mix = Path(meta["mix_wav"])

    cache = project.work / "whisper_raw.json"
    if cache.exists() and not ctx.force:
        result = read_json(cache)
        ctx.log.info("using cached whisper output")
    else:
        result = T.transcribe(mix, model_name, device, compute, s.get("language") or None, vocab,
                              lambda f, d: ctx.progress(f * 0.75, d), duration, Path(s["models_dir"]) if s.get("models_dir") else None)
        write_json(cache, result)          # checkpoint: a crash in diarization never re-transcribes
    segments = result["segments"]
    if s.get("whisperx_align", True):
        if T.maybe_whisperx_align(result, mix, device):
            ctx.log.info("whisperx alignment applied")

    # ---- speakers
    ctx.progress(0.78, "Working out who is speaking", force=True)
    files = meta.get("files", [])
    tracks = [f for f in files if f.get("role") in ("speaker", "auto")]
    method = "none"
    if len(tracks) >= 2 and meta.get("layout") != "single":
        # per-speaker files: offsets applied so the energy series align with the mix
        synced = []
        for i, f in enumerate(tracks):
            out = project.work / f"track_sync_{i:02d}.wav"
            M.mix_down([Path(f["wav"])], [f["offset"]], out, sr=16000)
            synced.append(out)
        labels = [f"SPEAKER_{i:02d}" for i in range(len(tracks))]
        T.diarize_by_tracks(segments, synced, labels)
        method = "tracks"
        for i, f in enumerate(tracks):
            if f.get("speaker"):
                meta.setdefault("speakers", {})[labels[i]] = f["speaker"]
    elif s.get("hf_token"):
        ok = T.diarize_pyannote(segments, mix, s["hf_token"], hw.get("gpu", "cpu"), s.get("num_speakers") or 2,
                                lambda f, d: ctx.progress(0.78 + f * 0.17, d))
        method = "pyannote" if ok else "none"
        if not ok:
            ctx.log.warning("pyannote unavailable - trying basic voice clustering")
    if method == "none" and s.get("basic_diarization", True) and (s.get("num_speakers") or 2) >= 2:
        ctx.progress(0.85, "Separating voices (basic)", force=True)
        if T.diarize_basic(segments, mix, int(s.get("num_speakers") or 2)):
            method = "basic"
            meta["diarization_note"] = ("Speakers were separated with the built-in basic method. For more accurate "
                                        "results add a Hugging Face token in Settings (free).")
    if method == "none":
        for seg in segments:
            for w in seg["words"]:
                w["speaker"] = "SPEAKER_00"
            seg["speaker"] = "SPEAKER_00"
    T.smooth_speakers(segments)
    T.relabel_speakers(segments)

    # ---- vocabulary post-correction
    all_words = [w for seg in segments for w in seg["words"]]
    fixes = T.apply_vocabulary(all_words, vocab) if vocab else 0
    # rebuild segments' word lists (apply_vocabulary merged in place on the flat list)
    if fixes:
        # merged words changed the flat list; re-split it into the original segments by time
        for seg in segments:
            seg["words"] = [w for w in all_words if seg["start"] - 0.01 <= w["start"] < seg["end"] + 0.01]
            if seg["words"]:
                seg["text"] = " ".join(w["word"] for w in seg["words"])

    # ---- speaker names
    speakers: dict[str, str] = meta.get("speakers") or {}
    labels = []
    for seg in segments:
        for w in seg["words"]:
            if w["speaker"] not in labels:
                labels.append(w["speaker"])
    host = s.get("host_name")
    guest_name = guest.get("name")
    for i, lab in enumerate(labels):
        speakers.setdefault(lab, (host if i == 0 and host else None) or (guest_name if i == 1 and guest_name else None) or f"Speaker {i + 1}")
    meta["speakers"] = speakers

    transcript = {
        "version": 1, "created": time.time(), "language": result.get("language", "en"),
        "duration": duration, "model": result.get("model"), "diarization": method, "vocabulary_fixes": fixes,
        "speakers": speakers, "source": {"mix": str(mix), "files": [f["path"] for f in files]},
        "segments": segments,
    }
    write_json(project.transcript, transcript)
    project.save(meta)
    ctx.progress(0.97, "Writing transcript files", force=True)
    export_transcript(project, transcript, meta)


def export_transcript(project: Project, transcript: dict[str, Any], meta: dict[str, Any], words_override=None) -> None:
    words = words_override or [w for seg in transcript["segments"] for w in seg["words"]]
    paras = paragraphs(words, transcript["speakers"])
    title = meta.get("title") or "Transcript"
    guest = meta.get("guest") or {}
    write_txt(paras, project.transcript_dir / "transcript.txt", title)
    write_docx(paras, project.transcript_dir / "transcript.docx", title,
               {"Guest": guest.get("name"), "Institution": guest.get("institution"), "Recorded": meta.get("date")})
