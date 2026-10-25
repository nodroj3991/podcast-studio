"""Camera plan: active-speaker switching (multicam) or punch-in zooms (single camera).

Output is a list of *shots* on the **source** timeline (the renderer maps them through the
kept ranges).  A shot is ``{"start", "end", "source": <file index>, "zoom": 1.0|1.12, "speaker"}``.

Rules that keep it from looking twitchy:
* Minimum shot length (default 2.5 s): a back-channel "mm-hm" does not switch the camera.
* Switch on the *first word* of the new speaker, but hold the previous shot through
  interjections shorter than ``min_shot``.
* Every ~4th speaker change in multicam uses the wide/other angle for one beat if a third
  "wide" file was given (role = "wide").
* Single camera: alternate zoom 1.0 / 1.12 on speaker changes, centred slightly toward the
  side the speaker sits on if ``speaker_sides`` is known; a zoom lasts until the next change.
"""
from __future__ import annotations

from typing import Any

from .schema import Transcript


def speaker_turns(tr: Transcript, min_turn: float = 2.5) -> list[dict[str, Any]]:
    turns: list[dict[str, Any]] = []
    for w in tr.words:
        if turns and turns[-1]["speaker"] == w.speaker:
            turns[-1]["end"] = w.end
        else:
            turns.append({"speaker": w.speaker, "start": w.start, "end": w.end})
    # absorb short interjections into the surrounding turn
    merged: list[dict[str, Any]] = []
    for t in turns:
        if merged and t["end"] - t["start"] < min_turn and merged[-1]["speaker"] != t["speaker"]:
            merged[-1]["end"] = t["end"]            # hold the current shot
            continue
        if merged and merged[-1]["speaker"] == t["speaker"]:
            merged[-1]["end"] = t["end"]
        else:
            merged.append(dict(t))
    return merged


def plan_camera(tr: Transcript, files: list[dict[str, Any]], layout: str, min_shot: float = 2.5,
                zoom: float = 1.12) -> dict[str, Any]:
    video_files = [i for i, f in enumerate(files) if f.get("has_video")]
    turns = speaker_turns(tr, min_shot)
    labels = tr.speaker_labels()
    shots: list[dict[str, Any]] = []
    if layout == "multicam" and len(video_files) >= 2:
        # map speaker label -> camera index. Explicit mapping from ingest (file["speaker"]) wins.
        cam_for: dict[str, int] = {}
        for i in video_files:
            spk_name = files[i].get("speaker")
            if spk_name:
                for lab, name in tr.speakers.items():
                    if name == spk_name:
                        cam_for[lab] = i
        for k, lab in enumerate(labels):
            cam_for.setdefault(lab, video_files[k % len(video_files)])
        wide = next((i for i, f in enumerate(files) if f.get("role") == "wide" and f.get("has_video")), None)
        for n, t in enumerate(turns):
            src = cam_for.get(t["speaker"], video_files[0])
            if wide is not None and n % 4 == 3 and t["end"] - t["start"] > 6:
                shots.append({"start": t["start"], "end": t["start"] + 3.0, "source": wide, "zoom": 1.0, "speaker": t["speaker"]})
                shots.append({"start": t["start"] + 3.0, "end": t["end"], "source": src, "zoom": 1.0, "speaker": t["speaker"]})
            else:
                shots.append({"start": t["start"], "end": t["end"], "source": src, "zoom": 1.0, "speaker": t["speaker"]})
        mode = "multicam"
    elif video_files:
        src = video_files[0]
        for n, t in enumerate(turns):
            z = zoom if n % 2 == 1 else 1.0
            shots.append({"start": t["start"], "end": t["end"], "source": src, "zoom": z, "speaker": t["speaker"],
                          "pan": -0.25 if (z > 1 and labels.index(t["speaker"]) % 2 == 0) else (0.25 if z > 1 else 0.0)})
        mode = "punch-in"
    else:
        shots.append({"start": 0.0, "end": tr.duration, "source": None, "zoom": 1.0, "speaker": labels[0] if labels else ""})
        mode = "audio-only"
    # cut 0.3 s *before* the new speaker's first word (a natural lead), then make shots contiguous
    lead = 0.3
    if shots:
        shots[0]["start"] = 0.0
        for a, b in zip(shots, shots[1:]):
            b["start"] = max(a["start"] + min_shot * 0.5, b["start"] - lead)
            a["end"] = b["start"]
        shots[-1]["end"] = max(shots[-1]["end"], tr.duration)
    return {"mode": mode, "min_shot": min_shot, "zoom": zoom, "shots": shots}
