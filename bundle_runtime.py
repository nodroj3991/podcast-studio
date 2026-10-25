"""Build-time: put a self-contained Python + packages + FFmpeg next to the app.

    python scripts/bundle_runtime.py --target mac-arm64 | mac-x64 | win-x64

Creates  runtime/python/   (python-build-standalone CPython 3.12 + engine/requirements.txt)
         third_party/ffmpeg/ (static ffmpeg + ffprobe + LICENSE)
Both folders are copied into the installer by electron-builder (see app/package.json extraResources).

Sources (all free; URLs are pinned so a build is reproducible):
  CPython: https://github.com/astral-sh/python-build-standalone/releases  (PSF licence)
  FFmpeg : macOS  https://ffmpeg.martin-riedl.de  (LGPL/GPL static builds for arm64 and x86_64)
           Windows https://github.com/BtbN/FFmpeg-Builds (ffmpeg-master-latest-win64-lgpl.zip)
"""
from __future__ import annotations

import argparse
import io
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PBS = "20250317"   # python-build-standalone release tag
PY = "3.12.9"
PYTHON_URLS = {
    "mac-arm64": f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS}/cpython-{PY}+{PBS}-aarch64-apple-darwin-install_only.tar.gz",
    "mac-x64": f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS}/cpython-{PY}+{PBS}-x86_64-apple-darwin-install_only.tar.gz",
    "win-x64": f"https://github.com/astral-sh/python-build-standalone/releases/download/{PBS}/cpython-{PY}+{PBS}-x86_64-pc-windows-msvc-install_only.tar.gz",
}
FFMPEG_URLS = {
    "mac-arm64": ["https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffmpeg.zip",
                  "https://ffmpeg.martin-riedl.de/redirect/latest/macos/arm64/release/ffprobe.zip"],
    "mac-x64": ["https://ffmpeg.martin-riedl.de/redirect/latest/macos/amd64/release/ffmpeg.zip",
                "https://ffmpeg.martin-riedl.de/redirect/latest/macos/amd64/release/ffprobe.zip"],
    "win-x64": ["https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-lgpl.zip"],
}


def fetch(url: str) -> bytes:
    print("downloading", url, flush=True)
    req = urllib.request.Request(url, headers={"User-Agent": "podcast-studio-build"})
    with urllib.request.urlopen(req, timeout=600) as r:
        return r.read()


def bundle_python(target: str) -> Path:
    dst = ROOT / "runtime"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir()
    data = fetch(PYTHON_URLS[target])
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        tf.extractall(dst)        # extracts "python/"
    py = dst / "python" / ("python.exe" if target.startswith("win") else "bin/python3")
    subprocess.run([str(py), "-m", "pip", "install", "--upgrade", "pip", "--quiet"], check=True)
    extra = []
    if target.startswith("mac"):
        extra = []                                    # default torch wheels include MPS on arm64
    elif target.startswith("win"):
        extra = ["--index-url", "https://download.pytorch.org/whl/cpu", "--extra-index-url", "https://pypi.org/simple"]
    subprocess.run([str(py), "-m", "pip", "install", "--quiet", *extra, "-r", str(ROOT / "engine" / "requirements.txt")], check=True)
    # trim: tests and caches
    for p in (dst / "python").rglob("__pycache__"):
        shutil.rmtree(p, ignore_errors=True)
    for name in ("test", "tests", "idlelib", "tkinter", "turtledemo"):
        for p in (dst / "python").rglob(name):
            if p.is_dir() and "site-packages" not in str(p):
                shutil.rmtree(p, ignore_errors=True)
    print("python runtime ready:", py)
    return py


def bundle_ffmpeg(target: str) -> None:
    dst = ROOT / "third_party" / "ffmpeg"
    if dst.exists():
        shutil.rmtree(dst)
    dst.mkdir(parents=True)
    for url in FFMPEG_URLS[target]:
        data = fetch(url)
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            for m in z.infolist():
                base = Path(m.filename).name
                if base in ("ffmpeg", "ffprobe", "ffmpeg.exe", "ffprobe.exe", "LICENSE", "LICENSE.txt"):
                    (dst / base).write_bytes(z.read(m))
    for b in dst.iterdir():
        os.chmod(b, 0o755)
    (dst / "SOURCE.txt").write_text("FFmpeg static builds from:\n" + "\n".join(FFMPEG_URLS[target]) +
                                    "\nFFmpeg is licensed under the LGPL/GPL; source: https://ffmpeg.org/download.html\n")
    print("ffmpeg ready:", list(dst.iterdir()))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default=None)
    ap.add_argument("--skip-python", action="store_true")
    ap.add_argument("--skip-ffmpeg", action="store_true")
    a = ap.parse_args()
    target = a.target or {"Darwin": "mac-arm64" if platform.machine() == "arm64" else "mac-x64", "Windows": "win-x64"}.get(platform.system())
    if not target:
        sys.exit("Unsupported platform; pass --target")
    if not a.skip_python:
        bundle_python(target)
    if not a.skip_ffmpeg:
        bundle_ffmpeg(target)


if __name__ == "__main__":
    main()
