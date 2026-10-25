"""Filler-word detection with a review list.

Nothing is cut automatically: every hit becomes a ``Filler`` with ``enabled=True`` and the
review screen shows each one in context so the host can untick the ones that are real
speech ("you know" as in "do you know her work?").

Default list is deliberately conservative.  "like" and "so" are *not* included by default
because they are usually meaningful in academic speech; add them in the project settings
if you want them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, asdict

from .schema import Transcript

DEFAULT_FILLERS = [
    "um", "umm", "uh", "uhh", "er", "erm", "ah", "hmm", "mm",
    "you know", "i mean", "sort of", "kind of", "like i said",
]

_norm_re = re.compile(r"[^a-z']+")


def _norm(t: str) -> str:
    return _norm_re.sub("", t.lower())


@dataclass
class Filler:
    id: str
    phrase: str
    word_ids: list[int]
    start: float
    end: float
    speaker: str
    context: str               # a few words either side, filler in [brackets]
    enabled: bool = True


def find_fillers(tr: Transcript, phrases: list[str] | None = None,
                 context_words: int = 5, max_gap: float = 0.6) -> list[Filler]:
    phrases = [p.lower().strip() for p in (phrases or DEFAULT_FILLERS) if p.strip()]
    patterns = sorted((p.split() for p in phrases), key=len, reverse=True)   # longest first
    words = tr.words
    norm = [_norm(w.text) for w in words]
    hits: list[Filler] = []
    i = 0
    while i < len(words):
        matched = None
        for pat in patterns:
            n = len(pat)
            if i + n <= len(words) and norm[i:i + n] == pat:
                # multi-word fillers must be spoken together, not across a pause
                if n > 1 and any(words[k + 1].start - words[k].end > max_gap for k in range(i, i + n - 1)):
                    continue
                # single-word fillers should not be the tail of a longer word: norm covers that
                matched = pat
                break
        if matched:
            n = len(matched)
            ids = [w.id for w in words[i:i + n]]
            left = " ".join(w.text for w in words[max(0, i - context_words):i])
            mid = " ".join(w.text for w in words[i:i + n])
            right = " ".join(w.text for w in words[i + n:i + n + context_words])
            hits.append(Filler(f"fil{len(hits):04d}", " ".join(matched), ids,
                               words[i].start, words[i + n - 1].end, words[i].speaker,
                               f"{left} [{mid}] {right}".strip()))
            i += n
        else:
            i += 1
    return hits


def fillers_to_json(items: list[Filler]) -> list[dict]:
    return [asdict(f) for f in items]
