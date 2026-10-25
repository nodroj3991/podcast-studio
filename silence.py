"""Silence detection.

Two sources are combined:

1. **Word gaps** from the transcript - the gap between one word's end and the next word's
   start.  Cheap, and exactly aligned with the text, so a cut never lands inside a word.
2. **Audio silence** (ffmpeg ``silencedetect``) on the mixed audio - catches long pauses
   that Whisper swallowed into a segment, and *vetoes* word gaps that actually contain
   laughter, "mm-hm", or crosstalk (anything louder than the noise floor).

A gap becomes a cut candidate when it is longer than ``min_gap`` (default 1.2 s).  We keep
``pad`` seconds of natural breathing room on each side so speech never sounds clipped.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from .schema import Transcript


@dataclass
class Silence:
    id: str
    start: float          # start of the region that will be removed
    end: float
    gap: float            # original gap length
    after_word: int | None
    before_word: int | None
    source: str           # "words", "audio", "both"
    enabled: bool = True


def audio_silences(audio_path: Path, noise_db: float = -35.0, min_len: float = 0.6) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect and return (start, end) pairs."""
    cmd = ["ffmpeg", "-hide_banner", "-nostats", "-i", str(audio_path), "-af",
           f"silencedetect=noise={noise_db}dB:d={min_len}", "-f", "null", "-"]
    out = subprocess.run(cmd, capture_output=True, text=True).stderr
    starts = [float(m) for m in re.findall(r"silence_start: ([\d.]+)", out)]
    ends = [float(m) for m in re.findall(r"silence_end: ([\d.]+)", out)]
    return list(zip(starts, ends))


def find_silences(tr: Transcript, min_gap: float = 1.2, pad: float = 0.25,
                  audio_path: Path | None = None, use_audio: bool = True) -> list[Silence]:
    found: list[Silence] = []
    words = tr.words

    # 1. word-gap candidates
    for i in range(len(words) - 1):
        a, b = words[i], words[i + 1]
        gap = b.start - a.end
        if gap >= min_gap:
            found.append(Silence(f"sil{len(found):04d}", a.end + pad, b.start - pad, gap, a.id, b.id, "words"))

    # leading / trailing dead air
    if words and words[0].start >= min_gap:
        found.insert(0, Silence("sil_head", 0.0, max(0.0, words[0].start - pad), words[0].start, None, words[0].id, "words"))
    if words and tr.duration - words[-1].end >= min_gap:
        found.append(Silence("sil_tail", words[-1].end + pad, tr.duration, tr.duration - words[-1].end, words[-1].id, None, "words"))

    # 2. audio verification / augmentation
    if use_audio and audio_path and Path(audio_path).exists():
        regions = audio_silences(Path(audio_path), min_len=max(0.4, min_gap * 0.5))
        verified: list[Silence] = []
        for s in found:
            # a word gap counts as silence only if audio agrees for most of it
            covered = sum(max(0.0, min(s.end, e) - max(s.start, b)) for b, e in regions)
            if s.gap <= 0 or covered / max(s.end - s.start, 1e-6) >= 0.6:
                s.source = "both"
                verified.append(s)
            else:
                s.enabled = False          # keep it in the list, but off: user can re-enable
                s.source = "words-only(audio has sound)"
                verified.append(s)
        found = verified

        # long audio silences that fall *inside* a transcript segment (no word gap)
        covered_by_words = [(s.start, s.end) for s in found]
        for b, e in regions:
            if e - b < min_gap:
                continue
            if any(abs(b - cs) < 0.5 and abs(e - ce) < 0.5 for cs, ce in covered_by_words):
                continue
            # don't cut if a word overlaps this region (Whisper thinks someone is talking)
            if any(w.start < e - pad and w.end > b + pad for w in words):
                continue
            found.append(Silence(f"sila{len(found):04d}", b + pad, e - pad, e - b, None, None, "audio"))

    found = [s for s in found if s.end - s.start > 0.1]
    found.sort(key=lambda s: s.start)
    return found


def silences_to_json(items: list[Silence]) -> list[dict]:
    return [asdict(s) for s in items]
