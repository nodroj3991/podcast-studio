"""Per-item video encoding (video only - audio is built separately in audio_mix.py).

All items are encoded with identical parameters so the final join is a stream-copy concat
(no second full encode).  Hardware encoders are used when they pass a 1-second self-test:
``h264_videotoolbox`` on macOS, ``h264_nvenc`` on NVIDIA, otherwise ``libx264``.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

FPS = 30

PROFILES = {
    "preview": {"w": 854, "h": 480, "crf": 30, "preset": "ultrafast", "hw": False},
    "final": {"w": 1920, "h": 1080, "crf": 20, "preset": "medium", "hw": True},
}

_ENCODER_CACHE: dict[str, list[str]] = {}


def encoder_args(profile: str, prefer_hw: bool = True, log=None) -> list[str]:
    """Return the video codec args; self-test hardware encoders once per process."""
    p = PROFILES[profile]
    key = f"{profile}:{prefer_hw}"
    if key in _ENCODER_CACHE:
        return _ENCODER_CACHE[key]
    args = ["-c:v", "libx264", "-preset", p["preset"], "-crf", str(p["crf"]), "-profile:v", "high", "-level", "4.1"]
    if p["hw"] and prefer_hw:
        for enc, extra in (("h264_videotoolbox", ["-b:v", "10M", "-maxrate", "14M", "-bufsize", "20M", "-profile:v", "high"]),
                           ("h264_nvenc", ["-preset", "p5", "-rc", "vbr", "-cq", "23", "-b:v", "0", "-profile:v", "high"])):
            if _test_encoder(enc, extra):
                args = ["-c:v", enc, *extra]
                if log:
                    log.info("using hardware encoder %s", enc)
                break
    _ENCODER_CACHE[key] = args
    return args


def _test_encoder(enc: str, extra: list[str]) -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=black:s=320x240:r=30:d=0.2",
                            "-c:v", enc, *extra, "-pix_fmt", "yuv420p", "-f", "null", "-"], capture_output=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


def common_output_args(profile: str) -> list[str]:
    return ["-pix_fmt", "yuv420p", "-r", str(FPS), "-video_track_timescale", "90000", "-movflags", "+faststart", "-an"]


def _zoom_filter(zoom: float, pan: float, w: int, h: int) -> str:
    """Punch-in: crop a window of 1/zoom and scale back up. pan -1..1 shifts the window."""
    if zoom <= 1.001:
        return f"scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    cw, ch = f"iw/{zoom}", f"ih/{zoom}"
    cx = f"(iw-{cw})/2*(1+{pan})"
    return f"crop={cw}:{ch}:{cx}:(ih-{ch})/2,scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"


def encode_clip(item: dict[str, Any], sources: list[dict[str, Any]], out: Path, profile: str, fallback_png: str | None,
                prefer_hw: bool = True, log=None) -> Path:
    p = PROFILES[profile]
    w, h = p["w"], p["h"]
    dur = float(item["dur"]) - float(item.get("trim_tail", 0.0))   # tail handed to an xfade junction
    src = sources[item["source"]] if item.get("source") is not None and item["source"] < len(sources) else None
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    inputs: list[str] = []
    if src and src.get("has_video"):
        t0 = float(item["src_in"]) - float(src.get("offset", 0.0))
        cmd += ["-ss", f"{max(0.0, t0):.3f}", "-i", src["path"], "-t", f"{dur:.3f}"]
        vchain = f"[0:v]fps={FPS},{_zoom_filter(item.get('zoom', 1.0), item.get('pan', 0.0), w, h)}"
    else:
        # audio-only episode: static card with a very slow zoom
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", fallback_png or "color=c=black"]
        vchain = f"[0:v]scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    n_in = 1
    filters = [vchain + "[v0]"]
    cur = "[v0]"

    lt = item.get("lower_third")
    if lt:
        remaining = lt["total"] - lt["offset"]
        cmd += ["-loop", "1", "-framerate", str(FPS), "-t", f"{min(dur, remaining):.3f}", "-i", lt["png"]]
        f = f"[{n_in}:v]scale={w}:{h},format=rgba"
        if lt.get("fade_out"):
            f += f",fade=t=out:st={max(0.0, min(dur, remaining) - 0.4):.3f}:d=0.4:alpha=1"
        f += f"[lt]"
        filters.append(f)
        x = f"min(0,-{w}+t*{w}/0.5)" if lt.get("slide_in") else "0"
        filters.append(f"{cur}[lt]overlay=x='{x}':y=0:eof_action=pass:format=auto[v{n_in}]")
        cur = f"[v{n_in}]"
        n_in += 1

    ch = item.get("chart")
    if ch:
        remaining = ch["total"] - ch["offset"]
        cmd += ["-ss", f"{ch['offset']:.3f}", "-i", ch["mp4"], "-t", f"{min(dur, remaining):.3f}"]
        cw = int(w * 0.78)
        f = f"[{n_in}:v]scale={cw}:-2,format=rgba"
        if ch.get("fade_in"):
            f += ",fade=t=in:st=0:d=0.4:alpha=1"
        if ch.get("fade_out"):
            f += f",fade=t=out:st={max(0.0, min(dur, remaining) - 0.4):.3f}:d=0.4:alpha=1"
        f += "[ch]"
        filters.append(f)
        # darken the video under the chart so it reads as a slide
        filters.append(f"{cur}eq=brightness=-0.12[dk]")
        filters.append(f"[dk][ch]overlay=x=(W-w)/2:y=(H-h)/2:eof_action=pass:format=auto[v{n_in}]")
        cur = f"[v{n_in}]"
        n_in += 1

    fi, fo = float(item.get("fade_in", 0.0)), float(item.get("fade_out", 0.0))
    tail = ""
    if fi:
        tail += f",fade=t=in:st=0:d={fi:.3f}"
    if fo:
        tail += f",fade=t=out:st={max(0.0, dur - fo):.3f}:d={fo:.3f}"
    filters.append(f"{cur}format=yuv420p{tail},trim=duration={dur:.3f},setpts=PTS-STARTPTS[vout]")
    cmd += ["-filter_complex", ";".join(filters), "-map", "[vout]", *encoder_args(profile, prefer_hw, log), *common_output_args(profile), str(out)]
    _run(cmd, log)
    return out


def encode_card(png: str, dur: float, out: Path, profile: str, fade: float = 0.5, prefer_hw: bool = True, log=None,
                kenburns: bool = True) -> Path:
    p = PROFILES[profile]
    w, h = p["w"], p["h"]
    frames = int(dur * FPS)
    if kenburns:
        # zoompan works on a single image: slow push-in from 1.0 to 1.06
        v = (f"scale={w * 2}:{h * 2},zoompan=z='min(1+0.06*on/{frames},1.06)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={frames}:s={w}x{h}:fps={FPS},"
             f"fade=t=in:st=0:d={fade}:color=black,fade=t=out:st={max(0.0, dur - fade):.3f}:d={fade}:color=black,format=yuv420p")
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", png,
               "-vf", v, "-t", f"{dur:.3f}", *encoder_args(profile, prefer_hw, log), *common_output_args(profile), str(out)]
    else:
        cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", png,
               "-vf", f"scale={w}:{h},fade=t=in:st=0:d={fade},fade=t=out:st={max(0.0, dur - fade):.3f}:d={fade},format=yuv420p",
               *encoder_args(profile, prefer_hw, log), *common_output_args(profile), str(out)]
    _run(cmd, log)
    return out


def encode_junction(item: dict[str, Any], sources: list[dict[str, Any]], out: Path, profile: str, prefer_hw: bool = True, log=None) -> Path:
    """xfade between the tail of A and the head of B (both `dur` seconds)."""
    p = PROFILES[profile]
    w, h = p["w"], p["h"]
    d = float(item["dur"])
    xf = {"slide": "slideleft", "wipe": "wipeleft", "dissolve": "dissolve", "fadeblack": "fadeblack", "circle": "circleopen",
          "smooth": "smoothleft"}.get(item.get("type", "slide"), item.get("type", "slideleft"))
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for side in ("a", "b"):
        s = item[side]
        src = sources[s["source"]]
        cmd += ["-ss", f"{max(0.0, s['src_in'] - float(src.get('offset', 0.0))):.3f}", "-i", src["path"], "-t", f"{d:.3f}"]
    fa = f"[0:v]fps={FPS},{_zoom_filter(item['a']['zoom'], item['a']['pan'], w, h)},trim=duration={d:.3f},setpts=PTS-STARTPTS[a]"
    fb = f"[1:v]fps={FPS},{_zoom_filter(item['b']['zoom'], item['b']['pan'], w, h)},trim=duration={d:.3f},setpts=PTS-STARTPTS[b]"
    # xfade needs A longer than offset: we overlap the entire d seconds (offset 0), output length = d
    fx = f"[a][b]xfade=transition={xf}:duration={d:.3f}:offset=0,format=yuv420p[vout]"
    cmd += ["-filter_complex", ";".join([fa, fb, fx]), "-map", "[vout]", "-t", f"{d:.3f}",
            *encoder_args(profile, prefer_hw, log), *common_output_args(profile), str(out)]
    _run(cmd, log)
    return out


def concat(parts: list[Path], out: Path, log=None) -> Path:
    lst = out.with_suffix(".txt")
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
    _run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
          "-movflags", "+faststart", str(out)], log)
    return out


def mux(video: Path, audio: Path, out: Path, chapters_meta: Path | None = None, log=None) -> Path:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-i", str(video), "-i", str(audio)]
    if chapters_meta:
        cmd += ["-i", str(chapters_meta), "-map_metadata", "2"]
    cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart", "-shortest", str(out)]
    _run(cmd, log)
    return out


def _run(cmd: list[str], log=None) -> None:
    if log:
        log.debug(" ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {r.stderr[-1500:]}\ncmd: {' '.join(cmd)[:800]}")
