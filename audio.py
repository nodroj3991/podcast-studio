"""Voice audio chain and loudness.

Chain (ffmpeg filters, in this order):
  highpass 80 Hz          - rumble, desk thumps, plosive energy
  afftdn                  - broadband noise reduction (adaptive, gentle: nr=12 dB)
  equalizer 250 Hz -2 dB  - "boxy" room tone
  equalizer 3 kHz  +2 dB  - presence / intelligibility
  deesser                 - tame harsh "s"
  acompressor 3:1         - even out levels (-18 dB threshold, 20 ms attack, 250 ms release)
  loudnorm (2-pass)       - EBU R128 to -14 LUFS integrated, -1 dBTP, LRA 11
  alimiter -1 dBTP        - belt and braces against clipping after mixing music

Two-pass loudnorm: pass 1 measures the programme, pass 2 applies linear gain with the
measured values so the result is exactly -14 LUFS without the pumping that single-pass
dynamic mode produces on speech.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any

TARGET_I, TARGET_TP, TARGET_LRA = -14.0, -1.0, 11.0


def voice_chain(noise_db: float = 12.0, compress: bool = True, denoise: bool = True) -> str:
    parts = ["highpass=f=80"]
    if denoise:
        parts.append(f"afftdn=nr={noise_db}:nf=-40:tn=1")
    parts += ["equalizer=f=250:t=q:w=1.2:g=-2", "equalizer=f=3000:t=q:w=1.0:g=2", "deesser=i=0.4"]
    if compress:
        parts.append("acompressor=threshold=-18dB:ratio=3:attack=20:release=250:makeup=2")
    return ",".join(parts)


def measure_loudness(path: Path, extra_filter: str = "") -> dict[str, float]:
    """Pass 1: run loudnorm in print mode and parse its JSON."""
    af = (extra_filter + "," if extra_filter else "") + f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:print_format=json"
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", af, "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True).stderr
    m = re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", out, re.S)
    if not m:
        raise RuntimeError("loudnorm measurement failed: " + out[-500:])
    data = json.loads(m.group(0))
    return {k: float(v) for k, v in data.items() if k != "normalization_type"}


def loudnorm_pass2(measured: dict[str, float]) -> str:
    return (f"loudnorm=I={TARGET_I}:TP={TARGET_TP}:LRA={TARGET_LRA}:measured_I={measured['input_i']}:"
            f"measured_TP={measured['input_tp']}:measured_LRA={measured['input_lra']}:"
            f"measured_thresh={measured['input_thresh']}:offset={measured['target_offset']}:linear=true:print_format=summary")


def final_limiter() -> str:
    return "alimiter=limit=0.891:attack=5:release=50:level=false"   # 0.891 = -1 dBTP


def ebur128_stats(path: Path) -> dict[str, Any]:
    """Measure integrated loudness, LRA and true peak of a finished file (for QA)."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-af", "ebur128=peak=true", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True).stderr
    tail = out[out.rfind("Summary:"):] if "Summary:" in out else out[-800:]
    def grab(label: str) -> float | None:
        m = re.search(label + r":\s*(-?[\d.]+)", tail)
        return float(m.group(1)) if m else None
    return {"integrated_lufs": grab("I"), "lra": grab("LRA"), "true_peak_dbtp": grab("Peak")}
