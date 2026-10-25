"""Hardware detection, Whisper model choice and processing-time estimates."""
from __future__ import annotations

import os
import platform
import subprocess
from typing import Any


def detect() -> dict[str, Any]:
    info: dict[str, Any] = {
        "os": platform.system(), "arch": platform.machine(), "cpu_count": os.cpu_count() or 2,
        "ram_gb": _ram_gb(), "gpu": "none", "gpu_name": "", "device": "cpu", "compute_type": "int8",
    }
    try:
        import torch  # noqa
        if torch.cuda.is_available():
            info.update(gpu="cuda", gpu_name=torch.cuda.get_device_name(0), device="cuda", compute_type="float16",
                        vram_gb=round(torch.cuda.get_device_properties(0).total_memory / 2**30, 1))
        elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
            info.update(gpu="apple", gpu_name="Apple Silicon GPU")
    except Exception:
        if platform.system() == "Darwin" and platform.machine() == "arm64":
            info.update(gpu="apple", gpu_name="Apple Silicon")
    # faster-whisper (CTranslate2) has no Metal backend: on Apple Silicon it runs on CPU with int8,
    # which is still ~3-4x faster than an Intel laptop thanks to the unified memory + NEON.
    return info


def _ram_gb() -> float:
    try:
        if platform.system() == "Darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            return round(int(out) / 2**30, 1)
        if platform.system() == "Linux":
            for line in open("/proc/meminfo"):
                if line.startswith("MemTotal"):
                    return round(int(line.split()[1]) / 2**20, 1)
        if platform.system() == "Windows":
            import ctypes
            class MS(ctypes.Structure):
                _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong), ("ullTotalPhys", ctypes.c_ulonglong),
                            ("ullAvailPhys", ctypes.c_ulonglong), ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                            ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong), ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
            ms = MS(); ms.dwLength = ctypes.sizeof(MS)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
            return round(ms.ullTotalPhys / 2**30, 1)
    except Exception:
        pass
    return 8.0


def choose_model(hw: dict[str, Any], quality: str = "balanced") -> str:
    """Fastest Whisper model that keeps good accuracy for this hardware.

    distil-large-v3 is ~6x faster than large-v3 at nearly the same WER (English only), so it
    is the default wherever there is enough RAM; `small` is the floor for interview audio.
    """
    if quality == "max":
        return "large-v3" if hw["ram_gb"] >= 16 else "medium"
    if hw["gpu"] == "cuda":
        return "distil-large-v3" if hw.get("vram_gb", 0) >= 4 else "small"
    if hw["gpu"] == "apple":
        return "distil-large-v3" if hw["ram_gb"] >= 16 else "small"
    # plain CPU laptop
    if hw["ram_gb"] >= 16 and hw["cpu_count"] >= 8:
        return "distil-large-v3"
    return "small" if hw["ram_gb"] >= 8 else "base"


# realtime factors: seconds of processing per second of audio (measured ballparks)
_TRANSCRIBE_RTF = {
    ("cuda", "distil-large-v3"): 0.02, ("cuda", "large-v3"): 0.06, ("cuda", "small"): 0.015,
    ("apple", "distil-large-v3"): 0.12, ("apple", "large-v3"): 0.45, ("apple", "small"): 0.06, ("apple", "medium"): 0.25,
    ("cpu", "distil-large-v3"): 0.30, ("cpu", "large-v3"): 1.2, ("cpu", "small"): 0.15, ("cpu", "base"): 0.08, ("cpu", "medium"): 0.6,
}
_RENDER_RTF = {"cuda": 0.15, "apple": 0.25, "cpu": 0.9}   # 1080p H.264 with overlays & audio chain
_PREVIEW_RTF = {"cuda": 0.05, "apple": 0.08, "cpu": 0.25}


def estimate(hw: dict[str, Any], duration_s: float, model: str | None = None, diarize: bool = True) -> dict[str, float]:
    model = model or choose_model(hw)
    g = hw["gpu"] if hw["gpu"] in ("cuda", "apple") else "cpu"
    t_rtf = _TRANSCRIBE_RTF.get((g, model), _TRANSCRIBE_RTF[("cpu", "small")])
    est = {
        "transcribe": duration_s * t_rtf,
        "diarize": duration_s * (0.03 if g == "cuda" else 0.35) if diarize else 0.0,
        "edit_plan": 20 + duration_s * 0.01,
        "music_charts": 60,
        "render_preview": duration_s * _PREVIEW_RTF[g],
        "render_final": duration_s * _RENDER_RTF[g],
        "export_qa": 30 + duration_s * 0.05,
    }
    est["total"] = sum(est.values())
    est["model"] = model  # type: ignore[assignment]
    return est


def fmt_duration(s: float) -> str:
    s = int(round(s))
    h, m = s // 3600, (s % 3600) // 60
    if h:
        return f"about {h} h {m:02d} min"
    if m:
        return f"about {m} min"
    return f"under a minute"


if __name__ == "__main__":
    import json, sys
    hw = detect()
    dur = float(sys.argv[1]) if len(sys.argv) > 1 else 5400
    print(json.dumps({"hardware": hw, "estimate_90min": estimate(hw, dur)}, indent=2))
