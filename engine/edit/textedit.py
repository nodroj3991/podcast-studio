"""Text-based editing operations on decisions.json (used by the review screen & CLI).

* ``delete_range(decisions, first_word_id, last_word_id, note)``  - highlight -> delete
* ``undelete(decisions, deletion_id)``
* ``correct(decisions, transcript, word_id, new_text)``            - fix a word; if the new
  text has several words the original timing is split proportionally by character count,
  if it is empty the word is removed from the transcript (not just cut), and the JSON
  transcript is updated so the export/captions use the corrected text and timings.
* ``rename_speaker(decisions, label, name)``
"""
from __future__ import annotations

import time
from typing import Any


def delete_range(dec: dict[str, Any], word_ids: list, note: str = "") -> dict[str, Any]:
    """word_ids: the ids of the highlighted words (contiguous in reading order)."""
    d = {"id": f"del{int(time.time() * 1000) % 10**9:09d}", "word_ids": list(word_ids), "note": note}
    dec.setdefault("deletions", []).append(d)
    return d


def undelete(dec: dict[str, Any], deletion_id: str) -> bool:
    before = len(dec.get("deletions", []))
    dec["deletions"] = [d for d in dec.get("deletions", []) if d["id"] != deletion_id]
    return len(dec["deletions"]) != before


def set_enabled(dec: dict[str, Any], kind: str, item_id: str, enabled: bool) -> bool:
    for item in dec.get(kind, []):
        if item["id"] == item_id:
            item["enabled"] = enabled
            return True
    return False


def rename_speaker(dec: dict[str, Any], label: str, name: str) -> None:
    dec.setdefault("speakers", {})[label] = name


def correct(transcript: dict[str, Any], word_id: int, new_text: str) -> list[dict[str, Any]]:
    """Apply a correction directly to the transcript JSON (source of truth).

    Returns the resulting word dicts.  Word ids are *not* renumbered (so decisions stay valid):
    a split produces ids like 120, "120.1", "120.2" ... and a removal leaves a tombstone
    ``{"id": 120, "word": "", "removed": true}`` that every consumer skips.
    """
    words = [w for seg in transcript["segments"] for w in seg["words"]]
    idx = next((i for i, w in enumerate(words) if str(w.get("id", i)) == str(word_id)), None)
    if idx is None:
        # transcripts straight from ingest have no ids: use positional index
        idx = int(word_id)
    seg, pos = _locate(transcript, idx)
    w = seg["words"][pos]
    new_text = new_text.strip()
    hist = transcript.setdefault("corrections", [])
    hist.append({"id": w.get("id", idx), "from": w["word"], "to": new_text, "time": time.time()})
    if not new_text:
        w.update(word="", removed=True)
        return [w]
    parts = new_text.split()
    if len(parts) == 1:
        w["word"] = new_text
        return [w]
    total = sum(len(p) for p in parts)
    span = w["end"] - w["start"]
    out, t = [], w["start"]
    base_id = w.get("id", idx)
    for k, p in enumerate(parts):
        dur = span * len(p) / total
        out.append({**w, "id": base_id if k == 0 else f"{base_id}.{k}", "word": p, "start": round(t, 3), "end": round(t + dur, 3)})
        t += dur
    seg["words"][pos:pos + 1] = out
    seg["text"] = " ".join(x["word"] for x in seg["words"] if not x.get("removed"))
    return out


def _locate(transcript: dict[str, Any], flat_index: int):
    n = 0
    for seg in transcript["segments"]:
        if flat_index < n + len(seg["words"]):
            return seg, flat_index - n
        n += len(seg["words"])
    raise IndexError(flat_index)


def ensure_word_ids(transcript: dict[str, Any]) -> None:
    """Give every word a stable id once (ingest output has none)."""
    i = 0
    for seg in transcript["segments"]:
        for w in seg["words"]:
            if "id" not in w:
                w["id"] = i
            i += 1
