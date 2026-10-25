"""Create a synthetic ~2-minute two-speaker episode for testing.

Produces, in <out>/:
    camA.mp4   host camera   (1280x720, own audio track, starts at t=0)
    camB.mp4   guest camera  (1280x720, own audio track, starts 1.30 s LATER -> tests sync)
    single.mp4 one wide camera with both voices mixed (for the punch-in test)
    script.json  the ground-truth dialogue with intended silences and fillers

Speech is synthesised with Piper (two different voices) so Whisper has real words to find.
Usage: python tests/make_sample.py <out_dir> --voices <dir with piper .onnx files>
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

SR = 22050

# (speaker, text, pause_after_seconds)  - fillers and long pauses are deliberate
DIALOGUE = [
    ("A", "Welcome back to the show. Today I am talking with Doctor Maya Lindqvist from the University of Edinburgh. Thanks for joining me.", 0.4),
    ("B", "Thank you, it is um, really lovely to be here.", 0.3),
    ("A", "So let us start at the beginning. Where did your academic journey begin?", 0.5),
    ("B", "Well, I did my undergraduate degree in physics, and honestly I did not expect to stay in academia at all. You know, I thought I would go into industry.", 1.8),
    ("B", "But then I did a summer project on ocean modelling and I was completely hooked.", 0.3),
    ("A", "And that led to the PhD?", 0.3),
    ("B", "Yes. I started my PhD in two thousand and twelve, and my first paper came out two years later. Um, it was a very small paper, but it mattered a lot to me.", 2.2),
    ("A", "Let us talk about citations. When did your work start to get noticed?", 0.4),
    ("B", "Around two thousand and seventeen, when we published the sea ice study. That paper is still the most cited thing I have written.", 0.6),
    ("A", "Who were the collaborators on that?", 0.3),
    ("B", "Mostly my supervisor, and uh, two colleagues from Bergen. I have worked with them ever since.", 1.5),
    ("A", "You moved to Edinburgh after that. What prompted the move?", 0.4),
    ("B", "I was offered a lectureship, and you know, the group there was doing exactly the kind of coupled modelling I wanted to do. So I moved to Edinburgh in two thousand and nineteen.", 0.5),
    ("A", "What are the big research questions now?", 0.3),
    ("B", "I mean, the main topic is how quickly the Arctic responds to warming, and what that means for the rest of the climate system.", 2.0),
    ("A", "That is a good place to end. Thank you so much, Maya.", 0.3),
    ("B", "Thank you, this was great.", 0.8),
]


def tts(text: str, model: Path, out: Path) -> np.ndarray:
    subprocess.run([sys.executable, "-m", "piper", "-m", str(model), "-f", str(out), "--sentence-silence", "0.25"],
                   input=text.encode(), check=True, capture_output=True)
    with wave.open(str(out)) as w:
        sr = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
    if sr != SR:
        idx = np.linspace(0, len(data) - 1, int(len(data) * SR / sr))
        data = np.interp(idx, np.arange(len(data)), data)
    return data


def write_wav(path: Path, x: np.ndarray) -> None:
    with wave.open(str(path), "w") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes((np.clip(x, -1, 1) * 32767).astype(np.int16).tobytes())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out"); ap.add_argument("--voices", required=True)
    a = ap.parse_args()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    voices = Path(a.voices)
    models = {"A": next(voices.rglob("en_GB-alan-medium.onnx")), "B": next(voices.rglob("en_US-lessac-medium.onnx"))}
    tracks = {"A": [], "B": []}
    script = []
    t = 1.0
    tmp = out / "tmp"; tmp.mkdir(exist_ok=True)
    lead = np.zeros(int(SR * t))
    for k in tracks:
        tracks[k].append(lead)
    for i, (spk, text, pause) in enumerate(DIALOGUE):
        x = tts(text, models[spk], tmp / f"{i}.wav") * 0.7
        other = "B" if spk == "A" else "A"
        # a little room bleed on the other mic (what a real two-mic setup has)
        tracks[spk].append(x)
        tracks[other].append(x * 0.04 + np.random.randn(len(x)) * 0.002)
        script.append({"speaker": spk, "text": text, "start": round(t, 3), "end": round(t + len(x) / SR, 3), "pause_after": pause})
        t += len(x) / SR
        gap = np.random.randn(int(SR * pause)) * 0.002
        for k in tracks:
            tracks[k].append(gap)
        t += pause
    tail = np.zeros(int(SR * 1.5))
    for k in tracks:
        tracks[k].append(tail)
    A = np.concatenate(tracks["A"]); B = np.concatenate(tracks["B"])
    offset_b = 1.30
    write_wav(out / "A.wav", A)
    write_wav(out / "B.wav", B[int(SR * offset_b):])          # B's recording started 1.3 s later
    write_wav(out / "mix.wav", A + B)
    dur = len(A) / SR
    for name, wav, label, color in (("camA", "A.wav", "CAMERA A - HOST", "0x3b6ea5"), ("camB", "B.wav", "CAMERA B - GUEST", "0xa5523b")):
        d = dur - (offset_b if name == "camB" else 0)
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c={color}:s=1280x720:r=30:d={d:.3f}",
                        "-i", str(out / wav), "-vf", f"drawtext=text='{label}':fontsize=64:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2,"
                        f"drawtext=text='%{{pts\\:hms}}':fontsize=40:fontcolor=white:x=40:y=40",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-shortest", str(out / f"{name}.mp4")], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"color=c=0x2a2a2a:s=1280x720:r=30:d={dur:.3f}",
                    "-i", str(out / "mix.wav"), "-vf", "drawtext=text='WIDE SHOT':fontsize=64:fontcolor=white:x=(w-text_w)/2:y=(h-text_h)/2",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-c:a", "aac", "-shortest", str(out / "single.mp4")], check=True)
    (out / "script.json").write_text(json.dumps({"offset_b": offset_b, "duration": dur, "turns": script}, indent=2))
    print(f"sample written to {out}: {dur:.1f}s, camB offset {offset_b}s")


if __name__ == "__main__":
    main()
