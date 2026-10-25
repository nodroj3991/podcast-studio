"""Audio for the final timeline.

1. ``mix_48k.wav``   full-quality mix of all tracks (synced offsets) at 48 kHz
2. ``voice.wav``     voice chain (highpass, denoise, EQ, de-ess, compressor) on the whole mix
3. ``speech.wav``    kept ranges concatenated, with silence inserted for intro/chapter cards and
                     acrossfade at xfade junctions - exactly the final video timeline
4. ``music.wav``     intro / outro / bed placed on the final timeline, bed ducked by speech
5. ``final.wav``     speech + music -> two-pass loudnorm to -14 LUFS -> limiter -1 dBTP

The filter graph for step 3 can contain hundreds of segments, so it is written to a script
file and passed with ``-filter_complex_script`` (no command-line length limit).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from ..edit.audio import voice_chain, measure_loudness, loudnorm_pass2, final_limiter
from ..ingest.media import mix_down

SR = 48000


def _run(cmd: list[str], log=None) -> None:
    if log:
        log.debug(" ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg (audio) failed: {r.stderr[-1500:]}")


def full_mix(sources: list[dict[str, Any]], out: Path, log=None) -> Path:
    tracks = [s for s in sources if s.get("role", "auto") in ("speaker", "auto")] or sources
    if out.exists():
        return out
    mix_down([Path(s["path"]) for s in tracks], [float(s.get("offset", 0.0)) for s in tracks], out, sr=SR)
    return out


def voice(mix: Path, out: Path, settings: dict[str, Any], log=None) -> Path:
    if out.exists():
        return out
    chain = voice_chain(noise_db=float(settings.get("noise_reduction_db", 12)), compress=settings.get("compress", True),
                        denoise=settings.get("denoise", True))
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mix), "-af", chain, "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(out)], log)
    return out


def speech_track(voice_wav: Path, items: list[dict[str, Any]], out: Path, work: Path, log=None) -> Path:
    """Concatenate the audio for every timeline item in order."""
    lines: list[str] = []
    labels: list[str] = []
    n = 0
    pending_xfade: float | None = None
    for it in items:
        if it["kind"] in ("intro", "card"):
            lines.append(f"anullsrc=r={SR}:cl=mono,atrim=duration={it['dur']:.3f}[s{n}]")
        elif it["kind"] == "junction":
            pending_xfade = float(it["dur"])
            continue
        else:
            dur = float(it["dur"]) + (pending_xfade or 0.0)
            start = float(it["src_in"]) - (pending_xfade or 0.0)
            # the clip that precedes a junction was trimmed by d in video; audio keeps full length
            # and is crossfaded with the next clip (whose start was shifted by +d, so we move it back)
            lines.append(f"[0:a]atrim=start={max(0.0, start):.3f}:duration={dur:.3f},asetpts=PTS-STARTPTS[s{n}]")
        if pending_xfade is not None and labels:
            prev = labels.pop()
            lines.append(f"{prev}[s{n}]acrossfade=d={pending_xfade:.3f}:c1=tri:c2=tri[x{n}]")
            labels.append(f"[x{n}]")
            pending_xfade = None
        else:
            labels.append(f"[s{n}]")
        n += 1
    # trailing junction (none expected) ignored
    if len(labels) == 1:
        lines.append(f"{labels[0]}anull[out]")
    else:
        lines.append("".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]")
    script = work / "speech_filter.txt"
    script.write_text(";\n".join(lines), encoding="utf-8")
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(voice_wav), "-filter_complex_script", str(script),
          "-map", "[out]", "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(out)], log)
    return out


def music_track(music: dict[str, Any], sections: dict[str, Any], total: float, speech: Path, out: Path, work: Path, log=None) -> Path | None:
    """Place intro/outro/bed on the timeline; duck the bed and outro under speech."""
    intro, outro, bed = music.get("intro"), music.get("outro"), music.get("bed")
    if not any(t and t.get("file") and Path(t["file"]).exists() for t in (intro, outro, bed)):
        return None
    bed_gain = float(music.get("bed_gain_db", -22))
    duck_ratio = max(2.0, abs(float(music.get("duck_db", -12))) / 2.0)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(speech)]
    lines = []
    layers = []
    n = 1
    intro_end = float(sections.get("intro_end", 0.0))
    if intro and intro.get("file"):
        cmd += ["-i", intro["file"]]
        # play through the title card and 3 s into the talk, then fade out over 2.5 s
        length = intro_end + 3.0 if intro_end else 8.0
        lines.append(f"[{n}:a]aformat=channel_layouts=mono,aresample={SR},atrim=duration={length:.3f},afade=t=in:d=0.3,"
                     f"afade=t=out:st={max(0.0, length - 2.5):.3f}:d=2.5,volume=-6dB,apad=whole_dur={total:.3f}[intro]")
        layers.append("[intro]"); n += 1
    if outro and outro.get("file"):
        cmd += ["-i", outro["file"]]
        length = 25.0
        start = max(0.0, total - length)
        delay = int(start * 1000)
        lines.append(f"[{n}:a]aformat=channel_layouts=mono,aresample={SR},atrim=duration={length:.3f},afade=t=in:d=3,"
                     f"afade=t=out:st={length - 2.0:.3f}:d=2,volume=-8dB,adelay={delay}|{delay},apad=whole_dur={total:.3f}[outro_raw]")
        layers.append("[outro_raw]"); n += 1
    if bed and bed.get("file"):
        cmd += ["-stream_loop", "-1", "-i", bed["file"]]
        lines.append(f"[{n}:a]aformat=channel_layouts=mono,aresample={SR},atrim=duration={total:.3f},afade=t=in:d=2,"
                     f"afade=t=out:st={max(0.0, total - 3):.3f}:d=3,volume={bed_gain}dB[bed_raw]")
        layers.append("[bed_raw]"); n += 1
    # duck everything except the intro under speech (sidechain compressor keyed by speech)
    ducked = [l for l in layers if l != "[intro]"]
    final_layers = ["[intro]"] if "[intro]" in layers else []
    if ducked:
        lines.append("".join(ducked) + f"amix=inputs={len(ducked)}:normalize=0[m_raw]" if len(ducked) > 1 else f"{ducked[0]}anull[m_raw]")
        lines.append("[0:a]aformat=channel_layouts=mono[key]")
        lines.append(f"[m_raw][key]sidechaincompress=threshold=0.015:ratio={duck_ratio:.1f}:attack=40:release=700:makeup=1:level_sc=1.0[ducked]")
        final_layers.append("[ducked]")
    if len(final_layers) > 1:
        lines.append("".join(final_layers) + f"amix=inputs={len(final_layers)}:normalize=0,atrim=duration={total:.3f}[out]")
    else:
        lines.append(f"{final_layers[0]}atrim=duration={total:.3f}[out]")
    script = work / "music_filter.txt"
    script.write_text(";\n".join(lines), encoding="utf-8")
    cmd += ["-filter_complex_script", str(script), "-map", "[out]", "-ar", str(SR), "-ac", "1", "-c:a", "pcm_s16le", str(out)]
    _run(cmd, log)
    return out


def master(speech: Path, music: Path | None, out: Path, log=None) -> dict[str, float]:
    """Mix, two-pass loudnorm to -14 LUFS, limiter.  Returns measured input stats."""
    mixed = out.with_name("premaster.wav")
    if music:
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(speech), "-i", str(music),
              "-filter_complex", "[0:a][1:a]amix=inputs=2:normalize=0[out]", "-map", "[out]", "-c:a", "pcm_s16le", str(mixed)], log)
    else:
        mixed = speech
    # gain to -14 LUFS, true-peak limit at -1 dBTP, re-measure, apply the small residual.
    # (loudnorm's linear mode backs off its gain whenever the input peaks would exceed the
    # true-peak ceiling, which leaves speech ~0.7 LU quiet; limiting first avoids that.)
    from ..edit.audio import ebur128_stats, TARGET_I
    measured = measure_loudness(mixed)
    gain = TARGET_I - measured["input_i"]
    tmp = out.with_name("master_pass1.wav")
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(mixed), "-af", f"volume={gain:.2f}dB,{final_limiter()}",
          "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(tmp)], log)
    st = ebur128_stats(tmp)
    resid = TARGET_I - (st.get("integrated_lufs") or TARGET_I)
    if abs(resid) > 0.2:
        _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(tmp), "-af", f"volume={resid:.2f}dB,{final_limiter()}",
              "-ar", str(SR), "-ac", "2", "-c:a", "pcm_s16le", str(out)], log)
    else:
        tmp.replace(out)
    measured["pass1_gain_db"] = round(gain, 2)
    measured["residual_db"] = round(resid, 2)
    return measured
