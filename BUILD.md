# Building the installers

The installer must be built on the platform it targets (a Mac for the .dmg, Windows for the .exe)
because it bundles a native Python runtime, PyTorch and FFmpeg. Two ways, both free:

## Option A – cloud build (recommended: no terminal, gives you updates too)

1. Create a **public** GitHub repository and upload this folder (GitHub Desktop or the web uploader).
2. Edit `app/package.json` → `"publish"` → put your GitHub username in `owner` (the repo name is `podcast-studio`; change it if yours differs).
3. On GitHub: **Releases → Draft a new release → Choose a tag → type `v0.2.0` → Create new tag → Publish release**.
   The workflow in `.github/workflows/build.yml` starts automatically (≈ 25 min) and attaches
   `PodcastStudio-0.2.0-mac-arm64.dmg`, `…-mac-x64.dmg` and `…-win-x64.exe` to the release.
4. Download the installer for your machine and follow `INSTALL.md`.
5. **Updates**: whenever a new tag `v0.2.1`, `v0.3.0`… is published, the installed app's *Help → Check for updates* finds it.

(Workflow details: `macos-14` runners are Apple Silicon, `macos-13` are Intel; `windows-latest` for the .exe. GitHub Actions is free for public repos.)

## Option B – build on your own machine

* **Mac**: double-click `scripts/build-mac.command`. It asks you to install Node.js (one click on nodejs.org) if missing, then downloads the Python runtime + FFmpeg (~1.5 GB, once), and writes the .dmg to `dist/`.
* **Windows**: double-click `scripts/build-win.bat` (needs Node.js and Python 3 from python.org – both one-click installers).

## What goes into the installer

| part | source | size |
|---|---|---|
| Electron shell | `app/` | ~90 MB |
| Python 3.12 runtime | python-build-standalone (PSF licence) | ~60 MB |
| Python packages | `engine/requirements.txt` (faster-whisper, torch CPU/MPS, pyannote, Pillow, python-docx, vl-convert) | ~450–650 MB |
| FFmpeg + ffprobe | static builds (see `scripts/bundle_runtime.py` for URLs and licences) | ~80 MB |
| Whisper / pyannote models | **not** in the installer – downloaded on first launch to the app data folder | 145 MB – 3 GB |

NVIDIA acceleration (Windows): PyPI's cuBLAS + cuDNN wheels add ~800 MB, so they are not bundled.
Settings → *Enable NVIDIA acceleration* installs them into the app's private runtime once; faster-whisper
then uses the GPU (`device=cuda`), and `h264_nvenc` is picked up for rendering automatically.

## Developer mode (no installer)

```
pip install -r engine/requirements.txt      # or just: numpy faster-whisper Pillow python-docx vl-convert-python
cd app && npm install && npm start           # launches Electron, which starts `python3 -m engine serve`
```
`PODCAST_STUDIO_SCREENSHOT=/tmp/x.png npm start` runs a headless smoke test (used by CI).
