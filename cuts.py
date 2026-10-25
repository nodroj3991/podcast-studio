"""Turn decisions (silences, fillers, text deletions, corrections) into kept time ranges.

``decisions.json``::

    {
      "settings": {"min_silence": 1.2, "remove_fillers": true, "pad": 0.25},
      "silences": [Silence...],          # each has "enabled"
      "fillers":  [Filler...],           # each has "enabled"
      "deletions": [{"id": "del0001", "word_ids": [120, ..., 160], "note": "tangent about parking"}],
      "corrections": {"word_id": "new text", ...},
      "speakers": {"SPEAKER_00": "Jordon", ...},
      "chapters": [...], "camera": {...}, "titles": {...}
    }

Rules
* A deletion/filler removes the words' time span, extended to the midpoint of the gaps on
  either side so no fragment of the neighbouring word is left behind, but never more than
  ``pad`` into that gap.
* Nothing is ever cut inside a word (ranges are built from word boundaries).
* Ranges shorter than ``min_keep`` (0.3 s) are dropped - they are audible glitches.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .schema import Transcript

MIN_KEEP = 0.3


@dataclass
class Range:
    start: float
    end: float
    reason: str = "keep"

    @property
    def dur(self) -> float:
        return self.end - self.start


def removed_ranges(tr: Transcript, decisions: dict[str, Any]) -> list[Range]:
    pad = float(decisions.get("settings", {}).get("pad", 0.25))
    words = tr.words
    by_id = {str(w.id): w for w in words}
    out: list[Range] = []

    def span_for_words(ids: list, reason: str) -> Range | None:
        found = sorted((by_id[str(i)] for i in ids if str(i) in by_id), key=lambda w: w.idx)
        if not found:
            return None
        first, last = found[0], found[-1]
        # extend into the neighbouring gaps (half the gap, capped at pad)
        prev_end = words[first.idx - 1].end if first.idx > 0 else first.start
        next_start = words[last.idx + 1].start if last.idx + 1 < len(words) else last.end
        start = first.start - min(pad, max(0.0, (first.start - prev_end) / 2))
        end = last.end + min(pad, max(0.0, (next_start - last.end) / 2))
        return Range(start, end, reason)

    for s in decisions.get("silences", []):
        if s.get("enabled", True):
            out.append(Range(float(s["start"]), float(s["end"]), "silence"))
    if decisions.get("settings", {}).get("remove_fillers", True):
        for f in decisions.get("fillers", []):
            if f.get("enabled", True):
                r = span_for_words(f["word_ids"], "filler")
                if r:
                    out.append(r)
    for d in decisions.get("deletions", []):
        r = span_for_words(d["word_ids"], "deleted")
        if r:
            out.append(r)
    out.sort(key=lambda r: r.start)
    # merge overlaps
    merged: list[Range] = []
    for r in out:
        if merged and r.start <= merged[-1].end + 0.01:
            merged[-1].end = max(merged[-1].end, r.end)
            if merged[-1].reason != r.reason:
                merged[-1].reason = "mixed"
        else:
            merged.append(Range(r.start, r.end, r.reason))
    return merged


def kept_ranges(tr: Transcript, decisions: dict[str, Any], total: float | None = None) -> list[Range]:
    total = total or tr.duration
    removed = removed_ranges(tr, decisions)
    kept: list[Range] = []
    cursor = 0.0
    for r in removed:
        if r.start > cursor:
            kept.append(Range(cursor, min(r.start, total)))
        cursor = max(cursor, r.end)
    if cursor < total:
        kept.append(Range(cursor, total))
    # drop glitch-length fragments by merging them into the removal
    kept = [k for k in kept if k.dur >= MIN_KEEP]
    # guard: never start/end inside a word
    for k in kept:
        for w in tr.words:
            if w.start < k.start < w.end - 0.02:
                k.start = w.start
            if w.start + 0.02 < k.end < w.end:
                k.end = w.end
    return kept


def source_to_edited(t: float, kept: list[Range]) -> float | None:
    """Map a source time to edited-timeline time (None if it was cut)."""
    acc = 0.0
    for k in kept:
        if k.start <= t <= k.end:
            return acc + (t - k.start)
        if t < k.start:
            return None
        acc += k.dur
    return None


def source_to_edited_nearest(t: float, kept: list[Range]) -> float:
    acc = 0.0
    for k in kept:
        if t <= k.end:
            return acc + max(0.0, t - k.start)
        acc += k.dur
    return acc


def edited_duration(kept: list[Range]) -> float:
    return sum(k.dur for k in kept)


_COMMON = set("""the a an and but so or it we they he she this that there then what when where which who how is was are
were not to of in on for with as at by from you your my our his her its one two if because about into after before
just really very also still even again""".split())


def words_in_edit(tr: Transcript, kept: list[Range]) -> list[dict[str, Any]]:
    """Words that survive the edit, with *edited-timeline* times. Used for captions/exports.

    If a removed span ended a sentence ("...lectureship, and [you know.] The group...") the next
    word keeps Whisper's capital; common words are lower-cased so the join reads naturally."""
    out = []
    removed_tail = None          # last removed word's text since the previous kept word
    for w in tr.words:
        s = source_to_edited(w.start + 0.01, kept)
        e = source_to_edited(w.end - 0.01, kept)
        if s is None or e is None:
            removed_tail = w.text
            continue
        text = w.text
        if removed_tail and out and removed_tail.rstrip().endswith((".", "?", "!")) \
                and not out[-1]["text"].rstrip().endswith((".", "?", "!")) and text[:1].isupper() and text.lower().strip(",.") in _COMMON:
            text = text[:1].lower() + text[1:]
        removed_tail = None
        out.append({"id": w.id, "text": text, "start": round(s, 3), "end": round(max(e, s + 0.02), 3),
                    "speaker": w.speaker, "src_start": w.start, "src_end": w.end})
    return out
