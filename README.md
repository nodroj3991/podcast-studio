# Podcast Studio

Turns raw interview recordings into a finished episode on your own computer: an edited 1080p MP4
at −14 LUFS, a speaker-labelled transcript (.docx/.txt), captions (.srt/.vtt), chapters, a
thumbnail, research charts about the guest, free licensed music with attribution, and a QA report.

Double-click the app → **New episode** → drop in the files → **Edit My Episode**.

```
podcast-studio/
├── app/            Electron desktop shell (main.js, preload.js, renderer/)
├── engine/         Python processing engine (local HTTP API + CLI)
│   ├── core/       project folders, resumable stage runner, hardware detection & time estimates
│   ├── ingest/     media probe, waveform sync, faster-whisper transcription, speaker detection, exports
│   ├── edit/       silences, fillers, text-based cuts & corrections, camera plan, chapters, graphics, audio chain, EDL
│   ├── music/      Jamendo + Openverse search, licence gate, attribution
│   ├── visuals/    OpenAlex / Crossref / Semantic Scholar, Vega-Lite charts (PNG + animated MP4), placement
│   ├── render/     timeline, per-clip encode, xfade junctions, audio mix + ducking + loudness, captions, QA, packaging
│   ├── server.py   the local API the app talks to (127.0.0.1:5890)
│   └── canva.py    Canva Connect API (OAuth + asset upload + design creation)
├── scripts/        bundle_runtime.py (Python + FFmpeg into the installer), build-mac.command, build-win.bat
├── tests/          make_sample.py (synthetic 2-minute episode), test_engine.py
├── docs/           ARCHITECTURE, INSTALL (first-run warnings), BUILD, TEST_CHECKLIST, SETUP_KEYS
└── .github/workflows/build.yml   cloud build of .dmg / .exe → GitHub Releases (feeds the in-app updater)
```

* **Docs:** [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · [docs/INSTALL.md](docs/INSTALL.md) · [docs/BUILD.md](docs/BUILD.md) · [docs/TEST_CHECKLIST.md](docs/TEST_CHECKLIST.md) · [docs/SETUP_KEYS.md](docs/SETUP_KEYS.md)
* **Developer quick start** (no installer): `pip install -r engine/requirements.txt`, then `cd app && npm install && npm start`.
* **CLI** (same engine, no UI): `python -m engine new <folder> --input a.mp4 --input b.mp4 --guest "Name"` then `python -m engine run <folder>`.

Everything runs locally; the only network calls are the music search (Jamendo/Openverse), the research
lookups (OpenAlex/Crossref/Semantic Scholar), the one-time model download, optional Canva upload and the update check.
