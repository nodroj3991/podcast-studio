"""Engine checks that run in a few seconds without models (plus an optional full pipeline run).

    python tests/test_engine.py                 # unit checks
    python tests/test_engine.py --full <sample_dir>   # also runs the whole pipeline on tests/make_sample.py output
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.music.license import check, normalise
from engine.render.captions import build_cues, check_cues
from engine.edit.schema import parse_transcript
from engine.edit.cuts import kept_ranges, words_in_edit
from engine.edit.fillers import find_fillers
from engine.ingest.transcribe import apply_vocabulary

fails = 0


def ok(cond: bool, msg: str) -> None:
    global fails
    print(("PASS " if cond else "FAIL ") + msg)
    fails += 0 if cond else 1


# ---- licence gate
ok(check("by", "https://creativecommons.org/licenses/by/4.0/").ok, "CC BY allowed")
ok(not check("by-nc", "").ok, "CC BY-NC blocked")
ok(not check("by-nd", "").ok, "CC BY-ND blocked (sync = adaptation)")
ok(not check("by-sa", "").ok and check("by-sa", "", allow_sharealike=True).ok, "CC BY-SA blocked unless opted in")
ok(check("cc0", "").ok and check(None, "https://creativecommons.org/publicdomain/zero/1.0/").code == "cc0", "CC0 allowed")
ok(not check("some weird licence", "").ok, "unknown licence blocked")
ok(normalise(None, "https://creativecommons.org/licenses/by-nc-nd/3.0/") == "by-nc-nd", "licence normalisation from URL")

# ---- transcript -> cuts
tr = parse_transcript({"duration": 10.0, "speakers": {"SPEAKER_00": "Host"}, "segments": [
    {"start": 0, "end": 4, "speaker": "SPEAKER_00", "words": [
        {"word": "Hello", "start": 0.5, "end": 0.9}, {"word": "um,", "start": 1.0, "end": 1.2}, {"word": "welcome.", "start": 1.3, "end": 1.9}]},
    {"start": 4, "end": 9, "speaker": "SPEAKER_01", "words": [
        {"word": "Thank", "start": 4.5, "end": 4.8}, {"word": "you", "start": 4.85, "end": 5.0}, {"word": "very", "start": 5.1, "end": 5.3}, {"word": "much.", "start": 5.35, "end": 5.8}]}]})
fillers = find_fillers(tr)
ok(len(fillers) == 1 and fillers[0].phrase == "um", "filler detection finds 'um'")
dec = {"settings": {"pad": 0.25, "remove_fillers": True}, "fillers": [{"id": "f", "word_ids": [1], "enabled": True}],
       "silences": [{"id": "s", "start": 2.15, "end": 4.25, "enabled": True}], "deletions": [{"id": "d", "word_ids": [5, 6]}]}
kept = kept_ranges(tr, dec)
ok(all(not (w.start + 0.02 < k.start < w.end - 0.02 or w.start + 0.02 < k.end < w.end - 0.02) for k in kept for w in tr.words), "no cut lands inside a word")
we = words_in_edit(tr, kept)
ok([w["text"] for w in we] == ["Hello", "welcome.", "Thank", "you"], f"surviving words correct: {[w['text'] for w in we]}")
ok(we[1]["start"] < we[2]["start"] and abs(we[2]["start"] - we[1]["end"]) < 1.0, "edited timeline is continuous")

# ---- vocabulary
words = [{"word": "imperial", "start": 0, "end": 1}, {"word": "colledge", "start": 1, "end": 2}, {"word": "london.", "start": 2, "end": 3}]
n = apply_vocabulary(words, ["Imperial College London"])
ok(n == 1 and words[0]["word"] == "Imperial College London." and words[0]["end"] == 3, "multi-word vocabulary correction merges timing")

# ---- captions
ws = [{"text": t, "start": 1 + i * 0.4, "end": 1.3 + i * 0.4, "speaker": "A" if i < 12 else "B"} for i, t in enumerate(
    "Today I am talking with Dr. Maya Lindqvist from the University of Edinburgh, thanks for joining me. Thank you it is lovely to be here.".split())]
cues = build_cues(ws)
ok(not check_cues(cues), f"{len(cues)} cues within 2x42 chars and 1-7 s")
ok(all("Dr." not in c["lines"][-1].split()[-1:] for c in cues), "no cue ends on the abbreviation 'Dr.'")
ok(all(len({w['speaker'] for w in ws if c['start'] - 0.01 <= w['start'] <= c['end'] + 0.01}) == 1 for c in cues), "no cue mixes two speakers")

print(f"\n{'ALL PASSED' if not fails else f'{fails} FAILED'}")
if "--full" in sys.argv:
    import subprocess
    sample = Path(sys.argv[sys.argv.index("--full") + 1])
    proj = sample / "project_test"
    subprocess.run([sys.executable, "-m", "engine", "new", str(proj), "--input", str(sample / "camA.mp4"), "--input", str(sample / "camB.mp4"),
                    "--guest", "Maya Lindqvist", "--institution", "University of Edinburgh", "--title", "Full test", "--host", "Jordon"], check=True)
    r = subprocess.run([sys.executable, "-m", "engine", "run", str(proj)], capture_output=True, text=True)
    print(r.stdout[-600:])
    rep = (proj / "Output" / "qa_report.txt").read_text() if (proj / "Output" / "qa_report.txt").exists() else "no report"
    print(rep)
    fails += 0 if "QA PASSED" in rep else 1
sys.exit(1 if fails else 0)
