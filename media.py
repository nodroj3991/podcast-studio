"""Media probing, audio extraction and waveform sync of separately recorded files.

Sync method: each file's audio is decoded to 8 kHz mono, turned into an energy envelope
(50 ms frames), and the envelope is cross-correlated against the reference file's envelope
via FFT.  The lag with the highest correlation is the offset.  Remote recordings (Zoom /
Riverside) where each person has their own file still share the same room acoustics through
the other person's headphones bleed and the common conversation rhythm, which is enough for
the envelope correlation; typical accuracy is within one frame (50 ms), then refined to
~1 ms with a second pass on the raw 8 kHz samples around the coarse lag.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

import numpy as np

VIDEO_EXT = {".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm"}
AUDIO_EXT = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".aiff", ".aif"}


@dataclass
class MediaInfo:
    path: str
    duration: float
    has_video: bool
    has_audio: bool
    width: int = 0
    height: int = 0
    fps: float = 0.0
    sample_rate: int = 0
    channels: int = 0
    codec_v: str = ""
    codec_a: str = ""


def probe(path: Path) -> MediaInfo:
    cmd = ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        data = json.loads(subprocess.check_output(cmd, text=True))
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Could not read {path.name}: is it a valid media file?") from e
    info = MediaInfo(str(path), float(data.get("format", {}).get("duration", 0) or 0), False, False)
    for s in data.get("streams", []):
        if s.get("codec_type") == "video" and not info.has_video and s.get("disposition", {}).get("attached_pic", 0) == 0:
            info.has_video = True
            info.width, info.height = int(s.get("width", 0)), int(s.get("height", 0))
            num, _, den = (s.get("avg_frame_rate") or "0/1").partition("/")
            info.fps = float(num) / float(den or 1) if float(den or 1) else 0.0
            info.codec_v = s.get("codec_name", "")
        elif s.get("codec_type") == "audio" and not info.has_audio:
            info.has_audio = True
            info.sample_rate, info.channels = int(s.get("sample_rate", 0)), int(s.get("channels", 0))
            info.codec_a = s.get("codec_name", "")
    return info


def extract_audio(src: Path, dst: Path, sr: int = 16000, mono: bool = True, start: float = 0.0,
                  duration: float | None = None) -> Path:
    """Decode audio to 16-bit PCM WAV (what Whisper and pyannote want)."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += ["-vn", "-ac", "1" if mono else "2", "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


def _load_pcm(path: Path, sr: int = 8000) -> np.ndarray:
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr),
           "-f", "s16le", "-"]
    raw = subprocess.check_output(cmd)
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _envelope(x: np.ndarray, sr: int, frame_ms: int = 50) -> np.ndarray:
    n = int(sr * frame_ms / 1000)
    m = len(x) // n
    e = np.sqrt((x[: m * n].reshape(m, n) ** 2).mean(axis=1))
    e = np.log1p(e * 100)
    return (e - e.mean()) / (e.std() + 1e-9)


def _xcorr_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> tuple[int, float]:
    """Return lag (samples of b relative to a; positive = b starts later) and confidence."""
    n = len(a) + len(b)
    nfft = 1 << (n - 1).bit_length()
    fa, fb = np.fft.rfft(a, nfft), np.fft.rfft(b, nfft)
    cc = np.fft.irfft(fa * np.conj(fb), nfft)
    cc = np.concatenate([cc[-max_lag:], cc[: max_lag + 1]])  # lags -max_lag..max_lag
    k = int(np.argmax(cc))
    lag = k - max_lag
    peak = cc[k]
    rest = np.delete(cc, k)
    conf = float((peak - rest.mean()) / (rest.std() + 1e-9))   # z-score of the peak
    return lag, conf


def _windowed_xcorr(a: np.ndarray, b: np.ndarray, max_lag: int, win: int, n_windows: int = 6,
                    absolute: bool = False) -> tuple[int, float]:
    """Sum normalised cross-correlation curves over several windows of `a` against the
    corresponding (+-max_lag) region of `b`.  Robust to long files and local dropouts."""
    acc = None
    L = min(len(a), len(b))
    if L <= win + 2 * max_lag:
        starts = [0]
    else:
        starts = [int(x) for x in np.linspace(max_lag, L - win - max_lag, n_windows)]
    for s in starts:
        sa = a[s:s + win]
        b0 = s - max_lag                      # may be negative: pad with zeros so negative lags are searched
        sb = b[max(0, b0):s + win + max_lag]
        if b0 < 0:
            sb = np.concatenate([np.zeros(-b0, dtype=sb.dtype), sb])
        if len(sa) < win // 2 or len(sb) < len(sa):
            continue
        sa = (sa - sa.mean()) / (sa.std() + 1e-9)
        sb = (sb - sb.mean()) / (sb.std() + 1e-9)
        nfft = 1 << (len(sa) + len(sb) - 1).bit_length()
        cc = np.fft.irfft(np.fft.rfft(sb, nfft) * np.conj(np.fft.rfft(sa, nfft)), nfft)[: len(sb) - len(sa) + 1]
        # index k means sa matches sb starting at k, i.e. b-time = (b0 + k), a-time = s  -> lag(b rel a) = b0 + k - s
        lags = np.arange(len(cc)) + b0 - s
        full = np.zeros(2 * max_lag + 1)
        valid = (lags >= -max_lag) & (lags <= max_lag)
        full[lags[valid] + max_lag] += cc[valid] / len(sa)
        acc = full if acc is None else acc + full
    if acc is None:
        return 0, 0.0
    curve = np.abs(acc) if absolute else acc
    k = int(np.argmax(curve))
    rest = np.delete(curve, k)
    conf = float((curve[k] - rest.mean()) / (rest.std() + 1e-9))
    return k - max_lag, conf


def sync_offset(reference: Path, other: Path, max_offset_s: float = 120.0) -> tuple[float, float]:
    """Seconds to *delay* `other` so it lines up with `reference` (negative = trim its start).
    Returns (offset_seconds, confidence_zscore).

    Two estimators, best confidence wins:
    * raw waveform (2 kHz lowpass) - locks onto microphone bleed / shared room sound
    * log-energy envelope with |correlation| - for remote per-person tracks with no bleed,
      where the envelopes are *anti*-correlated (people take turns) at the true alignment
    """
    sr = 8000
    a, b = _load_pcm(reference, sr), _load_pcm(other, sr)
    max_lag = int(max_offset_s * sr)
    # (1) raw waveform, windows of 45 s
    lag_raw, conf_raw = _windowed_xcorr(a, b, max_lag, win=sr * 45)
    # (2) envelope
    frame_ms = 20
    ea, eb = _envelope(a, sr, frame_ms), _envelope(b, sr, frame_ms)
    fpers = 1000 // frame_ms
    lag_env, conf_env = _windowed_xcorr(ea, eb, int(max_offset_s * fpers), win=60 * fpers, absolute=True)
    # estimators give "b-time relative to a-time"; the delay to apply to b is the negative
    if conf_raw >= conf_env:
        return -lag_raw / sr, conf_raw
    return -lag_env / fpers, conf_env      # 20 ms frames: within one video frame


def mix_down(inputs: list[Path], offsets: list[float], dst: Path, sr: int = 48000) -> Path:
    """Mix several synced tracks into one WAV (used for silence detection and the final mix)."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for p in inputs:
        cmd += ["-i", str(p)]
    parts = []
    for i, off in enumerate(offsets):
        delay = max(0, int(off * 1000))
        trim = max(0.0, -off)
        parts.append(f"[{i}:a]atrim=start={trim:.3f},asetpts=PTS-STARTPTS,adelay={delay}|{delay},aresample={sr}[a{i}]")
    chain = ";".join(parts) + ";" + "".join(f"[a{i}]" for i in range(len(inputs))) + f"amix=inputs={len(inputs)}:normalize=0[out]"
    cmd += ["-filter_complex", chain, "-map", "[out]", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le", str(dst)]
    subprocess.run(cmd, check=True)
    return dst


def info_to_json(m: MediaInfo) -> dict:
    return asdict(m)
