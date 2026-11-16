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

# ---- captions: the builder keeps its own limits (found on episode 75: 43-char lines, 0.5 s cues)
ws = [{"text": t, "start": i * 0.3, "end": i * 0.3 + 0.25, "speaker": "A"} for i, t in enumerate(["a" * 25, "b" * 25, "c" * 30])]
cues = build_cues(ws)
ok(all(len(l) <= 42 for c in cues for l in c["lines"]), f"an unsplittable 82-char run is cut before the line limit: {[c['lines'] for c in cues]}")
ws = [{"text": t, "start": 0.0 + i * 0.22, "end": 0.2 + i * 0.22, "speaker": "A"} for i, t in enumerate("That is a very good point.".split())]
ws += [{"text": "Thanks.", "start": 1.5, "end": 1.8, "speaker": "A"}, {"text": "Sure.", "start": 2.0, "end": 2.3, "speaker": "B"},
       {"text": "Shall", "start": 2.4, "end": 2.6, "speaker": "B"}, {"text": "we", "start": 2.6, "end": 2.7, "speaker": "B"}, {"text": "go?", "start": 2.7, "end": 3.0, "speaker": "B"}]
cues = build_cues(ws)
ok(any("Thanks." in " ".join(c["lines"]) and "point." in " ".join(c["lines"]) for c in cues), f"a cue too short to show joins the same speaker's previous cue: {[c['lines'] for c in cues]}")
ok(all(c["end"] - c["start"] >= 0.95 for c in cues) and not check_cues(cues), f"no cue under 1 s: {[(c['start'], c['end']) for c in cues]}")

# ---- QA: only real cuts count, camera changes are not cuts
from types import SimpleNamespace as NS
from engine.render.qa_checks import mid_word_cuts, cut_points, real_decode_errors
words_ = [NS(start=9.5, end=10.5, text="years"), NS(start=19.8, end=20.4, text="is")]
camera = [{"src_in": 0.0, "src_out": 10.0}, {"src_in": 10.0, "src_out": 20.0}]
ok(cut_points(camera) == [0.0, 20.0], f"a zoom change where clips meet is not a cut point: {cut_points(camera)}")
ok(mid_word_cuts(camera, words_) == ["20.00s inside 'is'"], f"mid-word check reports the real cut once: {mid_word_cuts(camera, words_)}")
real = [{"src_in": 0.0, "src_out": 10.0}, {"src_in": 12.0, "src_out": 19.0}]
ok(mid_word_cuts(real, words_) == ["10.00s inside 'years'"], f"a real cut inside a word is reported: {mid_word_cuts(real, words_)}")
ok(real_decode_errors("[mov,mp4 @ 0x1] Referenced QT chapter track not found\n") == [] and real_decode_errors("Invalid NAL unit\n") == ["Invalid NAL unit"], "benign ffmpeg chapter-track note is not a decode error")

# ---- chapters: readable titles
from collections import Counter
from engine.edit.chapters import clean_question, keyword_title, question_title, _pairs
ok(clean_question("So, like, how do we, how do we do this?") == "How do we do this?", f"question cleaned: {clean_question('So, like, how do we, how do we do this?')!r}")
ok(keyword_title(["cultur", "mermaid", "it's"], {"cultur": Counter({"culture": 3, "cultures": 1})}) == "Culture Mermaid It's", "keywords shown as spoken words, capitalised once")
ok("it's" not in [w for _, w in _pairs("it's a cultural thing about mermaids")], "contractions are not keywords")
tq = parse_transcript({"duration": 10.0, "speakers": {"H": "Host"}, "segments": [
    {"start": 0, "end": 2, "speaker": "H", "words": [{"word": w, "start": 0.1 * i, "end": 0.1 * i + 0.08} for i, w in enumerate("That you invited?".split())]},
    {"start": 2, "end": 4, "speaker": "H", "words": [{"word": w, "start": 2 + 0.1 * i, "end": 2 + 0.1 * i + 0.08} for i, w in enumerate("What is the book about?".split())]}]})
ok(question_title(tq, 0, "H") is None, "a question fragment is not used as a title")
ok(question_title(tq, 3, "H") == "What is the book about?", f"a real host question is the title: {question_title(tq, 3, 'H')!r}")

# ---- interrupted stages come back as pending
import tempfile, time
from engine.core.project import Project, read_json
with tempfile.TemporaryDirectory() as td:
    pr = Project(Path(td) / "ep").ensure()
    pr.set_stage("ingest", "done"); pr.set_stage("transcribe", "running"); (pr.work / "CANCEL").touch()
    hit = pr.reset_interrupted()
    ok(hit == ["transcribe"] and pr.stage_status("transcribe")["state"] == "pending" and pr.is_done("ingest"), "running stage reset to pending, done stage kept")
    ok(read_json(pr.work / "progress.json")["state"] == "interrupted" and not (pr.work / "CANCEL").exists(), "progress file says interrupted, stale CANCEL removed")

# ---- disk pre-flight sizing
from engine.core import storage as ST
need = ST.bytes_needed(78 * 60, ["render_final", "export", "qa", "package"], burn_in=True)
ok(ST.GB * 8 < need < ST.GB * 12, f"78-minute final render + burn-in needs ~10 GB: {ST.fmt_bytes(need)}")
ok(ST.bytes_needed(0, ["ingest"]) >= ST.HEADROOM and not ST.in_icloud(Path(td) / "nowhere"), "headroom always required; non-iCloud path detected")

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
