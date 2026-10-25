"""Transcription (faster-whisper, word timestamps), vocabulary correction, diarization.

Why faster-whisper rather than WhisperX directly: WhisperX = faster-whisper + wav2vec2
alignment + pyannote.  faster-whisper already gives word timestamps via cross-attention
DTW, which is accurate to ~50 ms - good enough for cutting on word boundaries with a pad.
That avoids shipping the wav2vec2 alignment model (another 1.2 GB) and a hard torch
dependency on machines that will never use a GPU.  If WhisperX *is* installed, the
``align`` step is used to tighten timings; otherwise it is skipped.

Diarization strategy, in order of preference:
1. Separate file per speaker -> each file's words belong to that speaker (no model needed,
   and 100 % accurate).  The mixed track is transcribed for timing consistency, then each
   word is assigned to whichever track has the most energy during the word.
2. pyannote.audio 3.1 speaker-diarization (needs a free Hugging Face token + accepting the
   model terms).
3. Single speaker label if neither is possible, with a warning in the transcript.
"""
from __future__ import annotations

import difflib
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

import numpy as np

Progress = Callable[[float, str], None]


# ----------------------------------------------------------------------------- vocabulary
def load_vocabulary(path: Path | None) -> list[str]:
    if not path or not Path(path).exists():
        return []
    terms = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            terms.append(line)
    return terms


def vocabulary_prompt(terms: list[str], max_chars: int = 600) -> str:
    """Whisper's initial_prompt biases spelling. Keep it short (it eats context window)."""
    out, size = [], 0
    for t in terms:
        if size + len(t) + 2 > max_chars:
            break
        out.append(t); size += len(t) + 2
    return ", ".join(out) + "." if out else ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def apply_vocabulary(words: list[dict[str, Any]], terms: list[str], cutoff: float = 0.82) -> int:
    """Fuzzy-correct recognised words/phrases to the custom vocabulary.

    Multi-word terms ("Imperial College London") are matched against sliding windows of the
    same word count; the matched words are merged into one word keeping the first start and
    last end time.  Returns number of corrections.
    """
    if not terms or not words:
        return 0
    fixes = 0
    by_len: dict[int, list[str]] = {}
    for t in terms:
        by_len.setdefault(len(t.split()), []).append(t)
    i = 0
    while i < len(words):
        done = False
        for n in sorted(by_len, reverse=True):
            if i + n > len(words):
                continue
            window = " ".join(w["word"] for w in words[i:i + n])
            nw = _norm(window)
            if not nw:
                continue
            best, best_ratio = None, 0.0
            for t in by_len[n]:
                nt = _norm(t)
                if abs(len(nt) - len(nw)) > max(3, len(nt) // 3):
                    continue
                r = difflib.SequenceMatcher(None, nw, nt).ratio()
                if r > best_ratio:
                    best, best_ratio = t, r
            if best and best_ratio >= cutoff and _norm(best) != nw:
                trailing = re.search(r"[.,;:!?]+$", words[i + n - 1]["word"])
                merged = {**words[i], "word": best + (trailing.group(0) if trailing else ""),
                          "end": words[i + n - 1]["end"], "corrected_from": window}
                words[i:i + n] = [merged]
                fixes += 1
                done = True
                break
            if best and best_ratio >= cutoff:
                break
        i += 1
    return fixes


# ----------------------------------------------------------------------------- whisper
def transcribe(audio: Path, model_name: str, device: str, compute_type: str, language: str | None,
               vocabulary: list[str], progress: Progress, duration: float, models_dir: Path | None = None) -> dict[str, Any]:
    from faster_whisper import WhisperModel
    progress(0.0, f"Loading {model_name} model")
    model = WhisperModel(model_name, device=device if device == "cuda" else "cpu", compute_type=compute_type,
                         download_root=str(models_dir) if models_dir else None)
    prompt = vocabulary_prompt(vocabulary)
    # decode ourselves (16 kHz mono float32) - avoids faster-whisper's PyAV dependency
    pcm = subprocess.check_output(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio), "-ac", "1", "-ar", "16000",
                                   "-f", "f32le", "-"])
    samples = np.frombuffer(pcm, dtype=np.float32)
    segments_iter, info = model.transcribe(
        samples, language=language, word_timestamps=True, vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500}, initial_prompt=prompt or None,
        condition_on_previous_text=False, beam_size=5)
    segments: list[dict[str, Any]] = []
    for seg in segments_iter:
        words = [{"word": w.word.strip(), "start": float(w.start), "end": float(w.end), "score": float(w.probability)}
                 for w in (seg.words or []) if w.word.strip()]
        segments.append({"start": float(seg.start), "end": float(seg.end), "text": seg.text.strip(), "words": words})
        progress(min(0.98, float(seg.end) / max(duration, 1)), f"Transcribing {int(seg.end // 60)} min of {int(duration // 60)}")
    return {"language": info.language, "segments": segments, "model": model_name}


def maybe_whisperx_align(result: dict[str, Any], audio: Path, device: str) -> bool:
    try:
        import whisperx  # type: ignore
    except Exception:
        return False
    try:
        model_a, meta = whisperx.load_align_model(language_code=result["language"], device=device if device == "cuda" else "cpu")
        aligned = whisperx.align(result["segments"], model_a, meta, str(audio), device if device == "cuda" else "cpu")
        for seg, aseg in zip(result["segments"], aligned["segments"]):
            if aseg.get("words"):
                seg["words"] = [{"word": w["word"], "start": w.get("start", seg["start"]), "end": w.get("end", seg["end"]),
                                 "score": w.get("score")} for w in aseg["words"]]
        return True
    except Exception:
        return False


# ----------------------------------------------------------------------------- diarization
def _energy_series(wav: Path, hop: float = 0.02) -> tuple[np.ndarray, float]:
    sr = 8000
    raw = subprocess.check_output(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(wav), "-ac", "1", "-ar", str(sr),
                                   "-f", "s16le", "-"])
    x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    n = int(sr * hop)
    m = len(x) // n
    return (x[: m * n].reshape(m, n) ** 2).mean(axis=1), hop


def diarize_by_tracks(segments: list[dict[str, Any]], tracks: list[Path], labels: list[str]) -> None:
    """Assign each word to the track with the most energy during it."""
    series = [_energy_series(t) for t in tracks]
    hop = series[0][1]
    floors = [np.percentile(e, 20) + 1e-9 for e, _ in series]
    for seg in segments:
        for w in seg["words"]:
            a, b = int(w["start"] / hop), max(int(w["end"] / hop), int(w["start"] / hop) + 1)
            scores = []
            for (e, _), fl in zip(series, floors):
                chunk = e[a:b] if a < len(e) else np.zeros(1)
                scores.append(float(chunk.mean() / fl) if len(chunk) else 0.0)
            w["speaker"] = labels[int(np.argmax(scores))]
        seg["speaker"] = max(set(x["speaker"] for x in seg["words"]), key=[x["speaker"] for x in seg["words"]].count) if seg["words"] else labels[0]


def diarize_pyannote(segments: list[dict[str, Any]], audio: Path, hf_token: str, device: str,
                     num_speakers: int | None, progress: Progress) -> bool:
    try:
        import torch
        from pyannote.audio import Pipeline  # type: ignore
    except Exception:
        return False
    progress(0.0, "Loading speaker-detection model")
    pipe = Pipeline.from_pretrained("pyannote/speaker-diarization-3.1", use_auth_token=hf_token)
    if device == "cuda":
        pipe.to(torch.device("cuda"))
    elif device == "apple" and torch.backends.mps.is_available():
        pipe.to(torch.device("mps"))
    progress(0.1, "Detecting speakers")
    kw = {"num_speakers": num_speakers} if num_speakers else {}
    diar = pipe(str(audio), **kw)
    turns = [(t.start, t.end, spk) for t, _, spk in diar.itertracks(yield_label=True)]
    turns.sort()
    for seg in segments:
        for w in seg["words"]:
            mid = (w["start"] + w["end"]) / 2
            best, best_ov = None, 0.0
            for s, e, spk in turns:
                ov = min(e, w["end"]) - max(s, w["start"])
                if ov > best_ov or (best is None and s <= mid <= e):
                    best, best_ov = spk, ov
            w["speaker"] = best or (seg.get("speaker") or "SPEAKER_00")
        if seg["words"]:
            labels = [x["speaker"] for x in seg["words"]]
            seg["speaker"] = max(set(labels), key=labels.count)
    progress(1.0, "Speakers detected")
    return True


def diarize_basic(segments: list[dict[str, Any]], audio: Path, num_speakers: int = 2) -> bool:
    """No-model fallback: cluster words by voice timbre (log-mel spectrum) with k-means.

    Works well for two clearly different voices (most host/guest pairs); it is labelled
    "basic" in the UI with a prompt to add a Hugging Face token for pyannote.
    """
    sr = 16000
    raw = subprocess.check_output(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(audio), "-ac", "1", "-ar", str(sr), "-f", "f32le", "-"])
    x = np.frombuffer(raw, dtype=np.float32)
    n_fft, hop = 512, 160
    n_mels = 32
    # mel filterbank
    def hz2mel(f): return 2595 * np.log10(1 + f / 700)
    def mel2hz(m): return 700 * (10 ** (m / 2595) - 1)
    mel_pts = mel2hz(np.linspace(hz2mel(80), hz2mel(7000), n_mels + 2))
    bins = np.floor((n_fft + 1) * mel_pts / sr).astype(int)
    fb = np.zeros((n_mels, n_fft // 2 + 1))
    for m in range(1, n_mels + 1):
        a, b, c = bins[m - 1], bins[m], bins[m + 1]
        if b > a: fb[m - 1, a:b] = (np.arange(a, b) - a) / (b - a)
        if c > b: fb[m - 1, b:c] = (c - np.arange(b, c)) / (c - b)
    win = np.hanning(n_fft)

    def feat(t0: float, t1: float) -> np.ndarray | None:
        s, e = int(t0 * sr), int(t1 * sr)
        if e - s < n_fft * 2:
            return None
        frames = [x[i:i + n_fft] * win for i in range(s, e - n_fft, hop)]
        spec = np.abs(np.fft.rfft(np.array(frames), axis=1)) ** 2
        mel = np.log(spec @ fb.T + 1e-8)
        # keep only louder frames (voiced), mean over time; cepstral lifter: drop first coeff (loudness)
        energy = mel.mean(axis=1)
        keep = energy >= np.percentile(energy, 40)
        v = mel[keep].mean(axis=0)
        return v - v.mean()

    def pitch(t0: float, t1: float) -> float:
        """median F0 (Hz) by autocorrelation on 40 ms frames; 0 if unvoiced."""
        s, e = int(t0 * sr), int(t1 * sr)
        fl = 640
        f0s = []
        for i in range(s, e - fl, fl // 2):
            fr = x[i:i + fl] - x[i:i + fl].mean()
            if (fr ** 2).mean() < 1e-5:
                continue
            ac = np.correlate(fr, fr, "full")[fl - 1:]
            ac /= ac[0] + 1e-9
            lo, hi = sr // 400, sr // 70          # 70-400 Hz
            k = lo + int(np.argmax(ac[lo:hi]))
            if ac[k] > 0.5:
                f0s.append(sr / k)
        return float(np.median(f0s)) if f0s else 0.0

    # cluster whole Whisper segments (several seconds each): timbre averages are stable there
    feats, idx = [], []
    for i, seg in enumerate(segments):
        if not seg["words"]:
            continue
        f = feat(seg["start"], seg["end"])
        if f is None:
            continue
        p0 = pitch(seg["start"], seg["end"])
        feats.append(np.concatenate([f, [np.log(p0 + 1.0) * 6.0]]))   # pitch weighted strongly
        idx.append(i)
    if len(feats) < 4:
        return False
    X = np.array(feats)
    X = (X - X.mean(axis=0)) / (X.std(axis=0) + 1e-6)
    best, best_inertia = None, 1e18
    rng = np.random.default_rng(0)
    for _ in range(10):
        c = X[rng.choice(len(X), num_speakers, replace=False)]
        for _ in range(40):
            d = ((X[:, None, :] - c[None, :, :]) ** 2).sum(axis=2)
            lab = d.argmin(axis=1)
            new = np.array([X[lab == k].mean(axis=0) if (lab == k).any() else c[k] for k in range(num_speakers)])
            if np.allclose(new, c):
                break
            c = new
        inertia = ((X - c[lab]) ** 2).sum()
        if inertia < best_inertia:
            best, best_inertia = lab, inertia
    label_of = {seg_i: f"SPEAKER_{int(l):02d}" for seg_i, l in zip(idx, best)}
    last = "SPEAKER_00"
    for i, seg in enumerate(segments):
        lab = label_of.get(i, last)
        for w in seg["words"]:
            w["speaker"] = lab
        seg["speaker"] = lab
        last = lab
    return True


def smooth_speakers(segments: list[dict[str, Any]], min_words: int = 3) -> None:
    """Remove 1-2 word speaker flips inside a sentence (diarization jitter)."""
    words = [w for s in segments for w in s["words"]]
    i = 0
    while i < len(words):
        j = i
        while j < len(words) and words[j]["speaker"] == words[i]["speaker"]:
            j += 1
        if j - i < min_words and i > 0 and j < len(words) and words[i - 1]["speaker"] == words[j]["speaker"]:
            for k in range(i, j):
                words[k]["speaker"] = words[i - 1]["speaker"]
        i = j


def relabel_speakers(segments: list[dict[str, Any]]) -> dict[str, str]:
    """Rename labels to SPEAKER_00, SPEAKER_01 in order of first appearance; return mapping."""
    mapping: dict[str, str] = {}
    for seg in segments:
        for w in seg["words"]:
            old = w.get("speaker", "SPEAKER_00")
            if old not in mapping:
                mapping[old] = f"SPEAKER_{len(mapping):02d}"
            w["speaker"] = mapping[old]
        if seg["words"]:
            seg["speaker"] = seg["words"][0]["speaker"]
    return mapping
