"""Captions from the corrected transcript, timed to the *final* video.

Rules: max 2 lines, <= 42 chars per line, 1-7 s per cue, never split mid-phrase.
Phrase boundaries, strongest first: sentence end (. ? !), speaker change, pause > 0.7 s,
clause punctuation (, ; : -), then before a conjunction/preposition ("and", "but", "that",
"which", "because", "so", "to", "of", "in", "with").  A cue is closed at the strongest
boundary found before the character limit is reached; if none exists (a 90-character run
with no punctuation) it breaks at the last word that fits.  Line wrapping inside a cue
prefers the same boundaries so the two lines read as two phrases.  A cue that would have
to vanish in under a second (the next phrase starts straight away) is merged into its
neighbour when the same speaker continues and the limits still hold.
"""
from __future__ import annotations

import re
from typing import Any

MAX_CHARS = 42
MAX_LINES = 2
MIN_DUR, MAX_DUR = 1.0, 7.0
GAP_BREAK = 0.7
CONJ = {"and", "but", "that", "which", "because", "so", "to", "of", "in", "with", "when", "where", "while", "or", "if", "as", "than"}


ABBREV = {"dr.", "mr.", "mrs.", "ms.", "prof.", "st.", "vs.", "e.g.", "i.e.", "etc.", "no.", "fig.", "al.", "jr.", "sr."}


def _boundary_strength(w: dict[str, Any], nxt: dict[str, Any] | None) -> int:
    t = w["text"]
    if nxt is None:
        return 5
    if nxt["speaker"] != w["speaker"]:
        return 6                                   # a speaker change always closes the cue
    if re.search(r"[.?!]['\")]*$", t) and t.lower() not in ABBREV and not re.fullmatch(r"[A-Z]\.", t):
        return 5
    if nxt["start"] - w["end"] > GAP_BREAK:
        return 3
    if re.search(r"[,;:—-]['\")]*$", t):
        return 2
    if nxt["text"].lower().strip(",.?!") in CONJ:
        return 1
    return 0


def _can_wrap(words: list[str]) -> bool:
    """Can these words be shown as at most two lines of MAX_CHARS?"""
    text = " ".join(words)
    if len(text) <= MAX_CHARS:
        return True
    return any(len(" ".join(words[:i])) <= MAX_CHARS and len(" ".join(words[i:])) <= MAX_CHARS for i in range(1, len(words)))


def _wrap(words: list[str]) -> list[str]:
    text = " ".join(words)
    if len(text) <= MAX_CHARS:
        return [text]
    # best split: closest to the middle, prefer after punctuation / before conjunction
    best, best_score = None, -1e9
    for i in range(1, len(words)):
        l, r = " ".join(words[:i]), " ".join(words[i:])
        if len(l) > MAX_CHARS or len(r) > MAX_CHARS:
            continue
        score = -abs(len(l) - len(r))
        if re.search(r"[,.;:?!]$", words[i - 1]):
            score += 15
        if words[i].lower() in CONJ:
            score += 8
        if score > best_score:
            best, best_score = i, score
    if best is None:
        best = len(words) // 2
    return [" ".join(words[:best]), " ".join(words[best:])]


def _chunks(words: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Split the word stream into cue-sized runs at the strongest phrase boundaries."""
    chunks: list[list[dict[str, Any]]] = []
    i = 0
    n = len(words)
    limit = MAX_CHARS * MAX_LINES
    while i < n:
        j = i
        best_cut, best_strength = None, -1
        chars = 0
        texts: list[str] = []
        while j < n:
            add = len(words[j]["text"]) + (1 if j > i else 0)
            # stop before a word that would push the cue over the character budget, the time
            # budget, or - the case the budget alone misses - past what two lines can hold
            # ("a 43-character phrase with no space near the middle" fits 84 chars but not 2x42)
            if j > i and (chars + add > limit or words[j]["end"] - words[i]["start"] > MAX_DUR
                          or not _can_wrap(texts + [words[j]["text"]])):
                break
            chars += add
            texts.append(words[j]["text"])
            s = _boundary_strength(words[j], words[j + 1] if j + 1 < n else None)
            # prefer strong boundaries, and among equals the later one (fuller cue)
            if s >= best_strength and (s > 0 or best_cut is None):
                best_cut, best_strength = j, s
            if s == 6 or (s == 5 and chars > limit * 0.3):
                break
            j += 1
        end = best_cut if best_cut is not None and best_strength > 0 else max(i, j - 1)
        chunks.append(words[i:end + 1])
        i = end + 1
    return chunks


def _mergeable(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> bool:
    return (a[0]["speaker"] == b[0]["speaker"] and b[-1]["end"] - a[0]["start"] <= MAX_DUR
            and _can_wrap([w["text"] for w in a + b]))


def _merge_short(chunks: list[list[dict[str, Any]]]) -> list[list[dict[str, Any]]]:
    """A chunk that cannot stay on screen for MIN_DUR before the next one is due joins a
    neighbour (same speaker, still within the limits) instead of flashing by in half a second."""
    i = 0
    while i < len(chunks):
        c = chunks[i]
        nxt_start = chunks[i + 1][0]["start"] if i + 1 < len(chunks) else None
        avail = (nxt_start - 0.05 if nxt_start is not None else c[-1]["end"] + MIN_DUR) - c[0]["start"]
        if avail < MIN_DUR - 0.05:
            if i + 1 < len(chunks) and _mergeable(c, chunks[i + 1]):
                chunks[i:i + 2] = [c + chunks[i + 1]]
                continue
            if i > 0 and _mergeable(chunks[i - 1], c):
                chunks[i - 1:i + 1] = [chunks[i - 1] + c]
                i -= 1
                continue
        i += 1
    return chunks


def build_cues(words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """words: [{text,start,end,speaker}] on the final timeline, in order."""
    chunks = _merge_short(_chunks(words))
    cues: list[dict[str, Any]] = []
    for k, chunk in enumerate(chunks):
        lines = _wrap([w["text"] for w in chunk])
        start = chunk[0]["start"]
        stop = max(chunk[-1]["end"], start + MIN_DUR)
        if k + 1 < len(chunks):
            nxt = chunks[k + 1][0]["start"]
            stop = min(stop, nxt - 0.05) if nxt - 0.05 > start + 0.5 else stop
        stop = min(stop, start + MAX_DUR)
        cues.append({"start": round(start, 3), "end": round(stop, 3), "lines": lines, "speaker": chunk[0]["speaker"]})
    # no overlaps
    for a, b in zip(cues, cues[1:]):
        if a["end"] > b["start"]:
            a["end"] = round(max(a["start"] + 0.3, b["start"] - 0.02), 3)
    return cues


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    return f"{ms // 3600000:02d}:{ms % 3600000 // 60000:02d}:{ms % 60000 // 1000:02d},{ms % 1000:03d}"


def _ts_vtt(t: float) -> str:
    return _ts_srt(t).replace(",", ".")


def to_srt(cues: list[dict[str, Any]]) -> str:
    out = []
    for i, c in enumerate(cues, 1):
        out.append(f"{i}\n{_ts_srt(c['start'])} --> {_ts_srt(c['end'])}\n" + "\n".join(c["lines"]) + "\n")
    return "\n".join(out)


def to_vtt(cues: list[dict[str, Any]], speaker_names: dict[str, str] | None = None, voice_tags: bool = False) -> str:
    out = ["WEBVTT", ""]
    for c in cues:
        body = "\n".join(c["lines"])
        if voice_tags and speaker_names:
            body = f"<v {speaker_names.get(c['speaker'], c['speaker'])}>" + body
        out.append(f"{_ts_vtt(c['start'])} --> {_ts_vtt(c['end'])}\n{body}\n")
    return "\n".join(out)


def check_cues(cues: list[dict[str, Any]]) -> list[str]:
    problems = []
    for i, c in enumerate(cues, 1):
        if len(c["lines"]) > MAX_LINES:
            problems.append(f"cue {i}: {len(c['lines'])} lines")
        if any(len(l) > MAX_CHARS for l in c["lines"]):
            problems.append(f"cue {i}: line over {MAX_CHARS} chars: {max(c['lines'], key=len)!r}")
        d = c["end"] - c["start"]
        if d < MIN_DUR - 0.05 or d > MAX_DUR + 0.05:
            problems.append(f"cue {i}: duration {d:.2f}s")
    return problems
