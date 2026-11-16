"""Pure helpers behind the QA stage (kept free of ffmpeg so they can be unit-tested)."""
from __future__ import annotations

from typing import Any, Iterable

# ffmpeg's MP4 demuxer prints this when it meets the chapter-track reference that ffmpeg's own
# muxer writes alongside the chapters we add; the streams still decode cleanly end to end.
BENIGN_DECODE = ("Referenced QT chapter track not found",)


def real_decode_errors(stderr: str) -> list[str]:
    return [l for l in stderr.splitlines() if l.strip() and not any(b in l for b in BENIGN_DECODE)]


def cut_points(clips: list[dict[str, Any]], tol: float = 1e-3) -> list[float]:
    """Source times where material is actually removed.

    A clip boundary where the next clip starts exactly where this one ends is a camera change
    (zoom or angle), not a cut: no audio is dropped there, so a word running across it is fine.
    The edit planner places those 0.3 s before the next speaker's first word, which usually
    lands inside the previous speaker's last word - and used to fail QA on every episode."""
    pts: list[float] = []
    for i, c in enumerate(clips):
        prev = clips[i - 1] if i else None
        nxt = clips[i + 1] if i + 1 < len(clips) else None
        if prev is None or abs(prev["src_out"] - c["src_in"]) > tol:
            pts.append(c["src_in"])
        if nxt is None or abs(nxt["src_in"] - c["src_out"]) > tol:
            pts.append(c["src_out"])
    return pts


def mid_word_cuts(clips: list[dict[str, Any]], words: Iterable[Any], slack: float = 0.03) -> list[str]:
    """Human-readable list of real cut points that fall inside a word (each reported once)."""
    words = list(words)
    out: list[str] = []
    seen: set[tuple[float, str]] = set()
    for b in cut_points(clips):
        for w in words:
            if w.start + slack < b < w.end - slack:
                key = (round(b, 2), w.text)
                if key not in seen:
                    seen.add(key)
                    out.append(f"{b:.2f}s inside '{w.text}'")
                break
    return out
