"""Automatic chapter markers from topic shifts, with editable suggested titles.

Method (no network, no ML model):
1. Split the transcript into blocks of ~45 s ending at sentence boundaries.
2. Build a bag-of-words vector for each block (stop words removed, light stemming).
3. Score each boundary by the cosine similarity between the 3 blocks before and the 3
   blocks after (TextTiling-style "depth score").
4. Keep local minima below the mean - k * std, enforce a minimum chapter length (default
   4 min), and never start a chapter mid-sentence or on a word that is cut.
5. Title = the 2-4 most distinctive terms of the chapter (TF-IDF against the whole episode),
   title-cased, plus a "question" fallback: if the host's sentence at the boundary is a
   question, use a trimmed form of it ("Why did you move to Edinburgh?").
Titles are suggestions; the review screen lets the host edit them.
"""
from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, asdict
from typing import Any

from .schema import Transcript

STOP = set("""a an the and or but if then so of to in on at by for with from as is are was were be been being am
i you he she it we they me him her us them my your his its our their this that these those there here what which who
whom whose when where why how do does did doing done have has had having not no nor yes yeah okay ok right well just
really very quite also too either neither because about into over under again further once all any both each few more
most other some such only own same than can could will would should may might must shall like um uh know mean think
thing things sort kind lot lots get got going go went come came say said says one two three first thought actually
people time way year years bit much many little long still even back now ever never always sometimes maybe
thank thanks great good nice lovely sure exactly definitely absolutely yes yep okay alright today show talk talking
it's that's there's here's what's who's he's she's let's i'm i've i'd i'll you're you've you'd you'll we're we've
we'd we'll they're they've they'd they'll don't doesn't didn't can't couldn't won't wouldn't shouldn't isn't aren't
wasn't weren't haven't hasn't hadn't gonna wanna gotta kinda sorta stuff anyway obviously basically literally
""".split())

# "So, like, how do we, how do we do this?" -> "How do we do this?"
LEAD_IN = re.compile(r"^(?:(?:so|and|but|okay|ok|right|well|now|yeah|yes|like|um|uh|erm|i mean|you know|i guess|i suppose)[,.]?\s+)+", re.I)
FILLER = re.compile(r"\b(?:um|uh|erm)\b,?\s*|,\s*(?:you know|i mean|like|sort of|kind of),\s*", re.I)
REPEAT = re.compile(r"\b((?:[\w']+,?\s+){0,3}[\w']+),?\s+\1\b", re.I)
QUESTION_OPENERS = {"what", "how", "why", "when", "where", "who", "whom", "whose", "which", "do", "does", "did", "is",
                    "are", "was", "were", "can", "could", "would", "will", "should", "have", "has", "had", "tell", "talk",
                    "walk", "describe", "explain", "any", "anything", "in", "to", "for", "from", "on"}


@dataclass
class Chapter:
    id: str
    start: float          # source time
    title: str
    keywords: list[str]
    word_id: int
    auto: bool = True


def _pairs(text: str) -> list[tuple[str, str]]:
    """(stem, word as spoken) for every content word - the stem drives the similarity maths,
    the spoken form is what a title may show ("culture", never the stem "cultur")."""
    out = []
    for raw in re.findall(r"[a-z][a-z'-]{2,}", text.lower()):
        word = raw.strip("'-")
        if word in STOP or len(word) < 3 or re.search(r"'(s|re|ve|ll|d|m|t)$", word):
            continue
        t = word
        for suf in ("ing", "ies", "ed", "es", "s"):
            if t.endswith(suf) and len(t) - len(suf) >= 4:
                t = t[: -len(suf)] + ("y" if suf == "ies" else "")
                break
        out.append((t, word))
    return out


def _tokens(text: str) -> list[str]:
    return [stem for stem, _ in _pairs(text)]


def _cap(word: str) -> str:
    return word[:1].upper() + word[1:]              # str.title() would turn "it's" into "It'S"


def keyword_title(keywords: list[str], surface: dict[str, Counter] | None = None, n: int = 3) -> str:
    """Readable form of the chapter's most distinctive stems: each shown as its most common
    spoken form, capitalised once."""
    words = []
    for k in keywords[:n]:
        forms = (surface or {}).get(k)
        words.append(_cap(forms.most_common(1)[0][0] if forms else k))
    return " ".join(words)


def clean_question(text: str) -> str:
    """Strip the spoken noise off a host question so it can stand as a chapter title."""
    t = " ".join(text.split())
    t = LEAD_IN.sub("", t)
    t = FILLER.sub("", t)
    for _ in range(4):                              # "how do we, how do we do" collapses in passes
        t2 = REPEAT.sub(r"\1", t)
        if t2 == t:
            break
        t = t2
    t = re.sub(r"\s+([,.?!])", r"\1", re.sub(r"\s+", " ", t)).strip(" ,")
    return (t[:1].upper() + t[1:]) if t else ""


def _cos(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    num = sum(v * b.get(k, 0) for k, v in a.items())
    return num / (math.sqrt(sum(v * v for v in a.values())) * math.sqrt(sum(v * v for v in b.values())) + 1e-9)


def _blocks(tr: Transcript, target_s: float = 45.0) -> list[tuple[int, int]]:
    """(first_word_index, last_word_index+1) blocks ending at sentence ends."""
    words = tr.words
    blocks, start = [], 0
    for i, w in enumerate(words):
        if w.start - words[start].start >= target_s and w.text.rstrip().endswith((".", "?", "!")):
            blocks.append((start, i + 1)); start = i + 1
    if start < len(words):
        blocks.append((start, len(words)))
    return blocks


def find_chapters(tr: Transcript, min_chapter_s: float = 240.0, sensitivity: float = 0.5,
                  max_chapters: int = 12, host_label: str | None = None) -> list[Chapter]:
    words = tr.words
    if not words:
        return []
    blocks = _blocks(tr, target_s=max(10.0, min(45.0, tr.duration / 12)))
    vecs = [Counter(_tokens(" ".join(w.text for w in words[a:b]))) for a, b in blocks]
    n = len(blocks)
    if n < 4:
        return [Chapter("ch000", words[0].start, "Introduction", [], words[0].id)]
    win = 3
    scores = []
    for i in range(1, n):
        left = Counter(); right = Counter()
        for v in vecs[max(0, i - win):i]:
            left.update(v)
        for v in vecs[i:i + win]:
            right.update(v)
        scores.append(_cos(left, right))
    mean = sum(scores) / len(scores)
    std = math.sqrt(sum((s - mean) ** 2 for s in scores) / len(scores)) or 1e-9
    thresh = mean - (1.0 - sensitivity) * std
    candidates = []
    for i, s in enumerate(scores):
        if s <= thresh and (i == 0 or s <= scores[i - 1]) and (i == len(scores) - 1 or s <= scores[i + 1]):
            candidates.append((s, i + 1))     # boundary before block i+1
    candidates.sort()
    chosen: list[int] = [0]
    for _, bi in candidates:
        t = words[blocks[bi][0]].start
        if all(abs(t - words[blocks[c][0]].start) >= min_chapter_s for c in chosen) and tr.duration - t >= min_chapter_s * 0.6:
            chosen.append(bi)
        if len(chosen) >= max_chapters:
            break
    chosen.sort()

    # titles + snap each boundary to the host question that opens the topic (within +-20 s)
    df = Counter()
    for v in vecs:
        df.update(set(v))
    surface: dict[str, Counter] = defaultdict(Counter)
    for a, b in blocks:
        for stem, word in _pairs(" ".join(w.text for w in words[a:b])):
            surface[stem][word] += 1
    questions = host_questions(tr, host_label)
    chapters: list[Chapter] = []
    for ci, bi in enumerate(chosen):
        end_bi = chosen[ci + 1] if ci + 1 < len(chosen) else n
        tf = Counter()
        for v in vecs[bi:end_bi]:
            tf.update(v)
        scored = sorted(((cnt * math.log(1 + n / df[t]), t) for t, cnt in tf.items() if cnt >= 2), reverse=True)
        keywords = [t for _, t in scored[:4]]
        wi = blocks[bi][0]
        t0 = words[wi].start
        q = min((q for q in questions if abs(q["start"] - t0) <= 20.0), key=lambda q: abs(q["start"] - t0), default=None)
        if q and ci > 0:
            wi, t0 = q["word_index"], q["start"]
        title = (q["title"] if q else None) or question_title(tr, wi, host_label) or keyword_title(keywords, surface) or f"Part {ci + 1}"
        if ci == 0:
            title = "Introduction"
            wi, t0 = 0, words[0].start
        chapters.append(Chapter(f"ch{ci:03d}", t0, title, keywords, words[wi].id))
    return chapters


def host_questions(tr: Transcript, host_label: str | None) -> list[dict]:
    """Start of every host turn that contains a question: [{"start","word_index","title"}]."""
    out = []
    words = tr.words
    i = 0
    while i < len(words):
        j = i
        while j < len(words) and words[j].speaker == words[i].speaker:
            j += 1
        if host_label is None or words[i].speaker == host_label:
            # a question anywhere in the turn; use the sentence that ends with '?'
            k = i
            sent_start = i
            while k < j:
                if words[k].text.endswith("?"):
                    t = question_title(tr, sent_start, host_label)
                    if t:
                        out.append({"start": words[i].start, "word_index": i, "title": t})
                    break
                if words[k].text.endswith((".", "!")):
                    sent_start = k + 1
                k += 1
        i = j
    return out


def question_title(tr: Transcript, word_index: int, host_label: str | None, max_words: int = 9) -> str | None:
    """If the first sentence of the block is a question (usually the host's), use it."""
    words = tr.words
    sent = []
    for w in words[word_index:word_index + 40]:
        sent.append(w.text)
        if w.text.endswith(("?", ".", "!")):
            break
    text = " ".join(sent).strip()
    if not text.endswith("?") or len(sent) > 25:
        return None
    if host_label and words[word_index].speaker != host_label:
        return None
    text = clean_question(text)
    toks = text.split()
    # a fragment ("That you invited?") or a one-word "Really?" is not a title; the keyword
    # fallback reads better than a question that does not open like one
    if len(toks) < 4 or toks[0].lower().strip(",") not in QUESTION_OPENERS:
        return None
    if len(toks) > max_words:
        text = " ".join(toks[:max_words]).rstrip(",") + "…"
    return text


def chapters_to_json(items: list[Chapter]) -> list[dict[str, Any]]:
    return [asdict(c) for c in items]
