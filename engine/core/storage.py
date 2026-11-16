"""Disk pre-flight: free space, files macOS has evicted to iCloud, and cheap APFS clones.

Why this exists: a finished 75-minute episode was lost to four package-stage failures in a row,
each after 1-2 hours of "Operation timed out" (errno 60). The Episodes folder sat in Documents with
iCloud's "Desktop & Documents" sync on and the disk 90% full, so macOS had quietly evicted the
multi-GB renders to the cloud (placeholder files: full size, zero blocks on disk). Every read then
blocked on a re-download, and ffmpeg/copy calls timed out. Checking for that *before* a stage runs
turns hours of silent stalling into a 2-second error with a fix.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Iterable

MB = 1024 ** 2
GB = 1024 ** 3

# Disk written per minute of source, measured on a 78-minute 640x360 Zoom recording rendered
# to 1080p: preview 6 MB/min (+ its clip cache), final 31 MB/min (+ clip cache), burned-in
# captions 40 MB/min, 48 kHz work WAVs 15 MB/min. Output copies are APFS clones, so free.
PER_MINUTE = {
    "ingest": 5 * MB, "transcribe": 2 * MB, "render_preview": 15 * MB, "render_final": 65 * MB,
    "export_burn_in": 40 * MB, "qa": 1 * MB, "package": 1 * MB,
}
HEADROOM = 2 * GB


def free_bytes(path: Path) -> int:
    p = Path(path)
    while not p.exists() and p.parent != p:
        p = p.parent
    return shutil.disk_usage(p).free


def bytes_needed(duration_s: float, stages: Iterable[str], burn_in: bool = False) -> int:
    minutes = max(1.0, float(duration_s or 0) / 60.0)
    per_min = 0
    for s in stages:
        per_min += PER_MINUTE.get(s, 0)
        if s == "export" and burn_in:
            per_min += PER_MINUTE["export_burn_in"]
    return int(HEADROOM + minutes * per_min)


def is_evicted(path: Path) -> bool:
    """True for an iCloud placeholder: the file claims a size but has no blocks on disk (macOS only)."""
    if platform.system() != "Darwin":
        return False
    try:
        st = os.stat(path)
    except OSError:
        return False
    return st.st_size > 0 and getattr(st, "st_blocks", 1) == 0


def evicted_files(roots: Iterable[Path], skip_dirs: Iterable[str] = ("Output", "logs")) -> list[tuple[Path, int]]:
    """Every evicted file under the given files/folders, as (path, size)."""
    if platform.system() != "Darwin":
        return []
    out: list[tuple[Path, int]] = []
    skip = set(skip_dirs)
    for root in roots:
        root = Path(root)
        if root.is_file():
            if is_evicted(root):
                out.append((root, root.stat().st_size))
            continue
        if not root.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip]
            for fn in filenames:
                p = Path(dirpath) / fn
                if is_evicted(p):
                    out.append((p, p.stat().st_size))
    return out


def request_download(paths: Iterable[Path]) -> None:
    """Ask iCloud to start fetching the files back (what Finder's "Download Now" does). Best effort."""
    if platform.system() != "Darwin" or not shutil.which("brctl"):
        return
    for p in paths:
        try:
            subprocess.run(["brctl", "download", str(p)], capture_output=True, timeout=10)
        except Exception:  # noqa: BLE001
            pass


def in_icloud(path: Path) -> bool:
    """Folder synced by iCloud Drive: inside the iCloud container, or under Documents/Desktop while
    the "Desktop & Documents Folders" option is on (macOS then mirrors those two folders into the
    container). Same rule as the desktop shell's startup warning."""
    if platform.system() != "Darwin":
        return False
    home = Path.home()
    r = Path(path).resolve()
    mobile = home / "Library" / "Mobile Documents"
    if str(r).startswith(str(mobile)):
        return True
    cloud = mobile / "com~apple~CloudDocs"
    for name in ("Documents", "Desktop"):
        base = home / name
        if (r == base or str(r).startswith(str(base) + os.sep)) and (cloud / name).exists():
            return True
    return False


def try_clone(src: Path, dst: Path) -> bool:
    """APFS clone (instant, shares blocks with the source, no extra space). False if not possible."""
    if platform.system() != "Darwin":
        return False
    try:
        if os.stat(src).st_dev != os.stat(dst.parent).st_dev:
            return False                           # clones only work inside one volume
        if dst.exists():
            dst.unlink()                           # re-cloning is instant; never trust a stale copy
        r = subprocess.run(["cp", "-c", "-p", str(src), str(dst)], capture_output=True)
        return r.returncode == 0 and dst.exists()
    except OSError:
        return False


def clone_or_copy(src: Path, dst: Path) -> None:
    """Copy that is free on APFS and a normal copy everywhere else; keeps the source's mtime."""
    if not try_clone(src, dst):
        shutil.copy2(src, dst)


def fmt_bytes(n: float) -> str:
    return f"{n / GB:.1f} GB" if n >= GB else f"{n / MB:.0f} MB"
