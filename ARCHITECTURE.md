# Architecture, stack, specs, timings, services

## 1. Shape of the app

```
┌──────────────── Podcast Studio.app / .exe ───────────────────────────────┐
│  Electron shell (app/)                                                    │
│   • window + tray, drag-and-drop, notifications, powerSaveBlocker,        │
│     auto-update check (GitHub Releases)                                   │
│   • spawns the engine and opens http://127.0.0.1:5890                     │
│                                                                           │
│  Python engine (engine/, bundled CPython 3.12 in resources/runtime/)      │
│   • local HTTP API + job queue (one episode at a time, others queued)     │
│   • 11 resumable stages, each writes its result + status into project.json│
│   • FFmpeg/ffprobe static binaries in resources/third_party/ffmpeg/       │
│   • models in <app data>/models (downloaded once, with progress)          │
└───────────────────────────────────────────────────────────────────────────┘
          │ reads/writes
          ▼
  ~/Documents/Podcast Episodes/<episode>/   one folder per episode (re-openable)
      project.json  media/ transcript/ edit/ music/ charts/ renders/ captions/ work/ logs/ Output/
```

**Why Electron rather than Tauri.** Both were viable. Electron won on three practical points for a
non-programmer-maintained app: (1) `electron-builder` + `electron-updater` give a .dmg/.exe installer
and a GitHub-Releases update check with zero extra services; (2) `powerSaveBlocker`, native
notifications, tray and file dialogs are first-party APIs with years of edge cases handled; (3) the UI
is plain HTML/JS with no build step, so a fix is a text edit. Tauri would give a ~10 MB smaller
shell, but the installer is dominated by the Python runtime and FFmpeg anyway (~700 MB), so the
saving is irrelevant, and Tauri's sidecar/update story needs more Rust-side code to maintain.

**Why faster-whisper instead of full WhisperX.** WhisperX = faster-whisper + wav2vec2 alignment +
pyannote. faster-whisper alone gives word timestamps accurate to ~50 ms, enough to cut on word
boundaries with a 0.25 s pad. Skipping the alignment model saves 1.2 GB and a hard torch dependency
for transcription; if `whisperx` is installed, the engine uses its alignment automatically.

## 2. Stages (all resumable)

| # | stage | what it does | output |
|---|---|---|---|
| 1 | ingest | probe files, sync separate recordings by waveform cross-correlation (bleed-based + envelope anti-correlation, ±1 frame), mix | `work/mix_16k.wav`, offsets in project.json |
| 2 | transcribe | faster-whisper (model chosen for the hardware), vocabulary prompt + fuzzy post-correction, speakers from per-person tracks → pyannote → built-in basic clustering | `transcript/transcript.json` (+ .txt/.docx) |
| 3 | edit_plan | silences > 1.2 s (word gaps verified by `silencedetect`), filler review list, topic-change chapters snapped to the host's question, camera plan (active speaker / punch-ins), title card, lower-third, chapter cards, thumbnail | `edit/decisions.json`, `edit/graphics/` |
| 4 | music | mood/length search on Jamendo + Openverse, licence gate (blocks NC, ND, SA-by-default), download, attribution | `music/music.json`, `attribution.txt` |
| 5 | charts | OpenAlex profile (+ Crossref/Semantic Scholar cross-checks), custom CSV/pasted data, Vega-Lite PNG + 3–6 s MP4 in brand colours, placement by transcript cues | `charts/*.png|.mp4`, `placements.json` |
| 6 | edl | kept ranges from decisions → clips split at camera changes → chapters/transitions/charts on the edited timeline | `edit/edl.json|.txt|.csv` |
| 7 | render_preview | 854×480 ultrafast | `renders/preview.mp4` |
| 8 | render_final | 1080p H.264 (VideoToolbox / NVENC / libx264), AAC 192k | `renders/episode_1080p.mp4` |
| 9 | export | captions (2×42 chars, 1–7 s, phrase-aware, retimed), edited transcript .docx/.txt, chapters, description with credits, optional burned-in version | `captions/`, `Output/` |
| 10 | qa | codec/duration/decode, −14 ±1 LUFS, true peak ≤ −1 dBTP, black frames, cuts-inside-words, caption spec + sync | `Output/qa_report.txt` |
| 11 | package | copies everything into `Output/` and zips it | `<title>_outputs.zip` |

Render design: every clip is encoded **once** with its cut, zoom, fades and overlays baked in; the join
is a stream-copy concat. Chapter transitions (`xfade`) re-encode only the 2 s junction. Audio is built
separately from the kept ranges (voice chain → cuts/cards/crossfades → music with sidechain ducking →
gain → true-peak limiter → residual gain) and muxed at the end. A resumed render skips finished clips.

## 3. Minimum and recommended computer

| | minimum | recommended |
|---|---|---|
| macOS | 12 Monterey, Apple Silicon M1 or Intel 2018+, 8 GB RAM, 20 GB free | M1 Pro/M2+ with 16 GB (uses VideoToolbox + distil-large-v3) |
| Windows | 10 64-bit, 4-core CPU, 8 GB RAM, 20 GB free | 8-core + NVIDIA GPU (6 GB VRAM) + 16 GB RAM |
| disk per episode | ~3× the raw footage size (intermediates are in `work/`, safe to delete after) | SSD |

## 4. How long a 90-minute episode takes (measured ballparks; the app shows a live estimate for *your* machine)

| machine | transcribe | speakers | preview | final render | total |
|---|---|---|---|---|---|
| Intel laptop, no GPU (4 cores, 8 GB) | 25–30 min (small) | 30 min (pyannote CPU) / 3 min basic | 20 min | 80 min | **~2.5 h** |
| Apple M1/M2 (8–16 GB) | 10 min (distil-large-v3, int8 CPU) | 15 min (MPS) | 7 min | 22 min | **~1 h** |
| Windows + NVIDIA RTX 3060 | 2 min | 3 min | 5 min | 14 min | **~25 min** |

Transcription dominates on CPU; the final render dominates once a GPU encoder exists. The queue lets
you line up several episodes overnight; each finishes with a notification and the machine is kept awake.

## 5. Services and licences (all free / free-tier)

| service | used for | key? | cost | licence / terms that matter |
|---|---|---|---|---|
| faster-whisper + Systran model weights | transcription | no | free | MIT (code), MIT (converted weights) |
| pyannote.audio 3.1 | speaker detection | free HF token + accept model terms | free | MIT code; model gated (you accept once) |
| Hugging Face Hub | model downloads | token only for pyannote | free | ToS: fine for local use |
| FFmpeg static builds | all audio/video | no | free | LGPL/GPL – binaries shipped with licence text and source link |
| OpenAlex | publications, citations, co-authors, topics, institutions | no (email for polite pool) | free, 100k calls/day | CC0 data |
| Crossref | cross-check of paper counts | no (email for polite pool) | free | open metadata |
| Semantic Scholar | citation counts, h-index cross-check | optional key | free (100 req / 5 min anon) | data CC BY-NC-SA → used only for cross-checks, every chart is labelled "Source" |
| Jamendo API | music search with licence flags | free client ID | free for non-commercial *API use* (music itself per-track CC) | we block CC-NC / CC-ND tracks; CC-BY credited in `attribution.txt` |
| Openverse API | second music source (Jamendo/Freesound/Wikimedia) | none (registration raises rate limits) | free | per-item CC licence, same gate |
| Canva Connect API | upload charts/title cards/thumbnail, create a thumbnail design | free integration (client ID + secret) | free | Canva Developer Terms; private integration for your own account |
| vl-convert (Vega-Lite) | charts | no | free | BSD |
| GitHub Releases | installers + update feed | GitHub account | free for public repos | — |

Creative Commons note used by the licence gate: syncing music to video is an **Adaptation** under CC 4.0
§1, so ND tracks are never allowed; NC is incompatible with a monetized channel; SA would require the
episode to be CC BY-SA (off by default, switchable in Settings).
