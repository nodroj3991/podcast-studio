#!/bin/bash
# Double-click this file on a Mac to build "Podcast Studio.dmg" (takes 10-20 minutes the first time).
# It installs nothing system-wide except Node.js (official installer) if it is missing.
cd "$(dirname "$0")/.."
set -e
echo "== Podcast Studio - macOS build =="
if ! command -v node >/dev/null 2>&1; then
  echo "Node.js is needed to build the app. Opening the download page - install it, then double-click this file again."
  open "https://nodejs.org/en/download"
  exit 1
fi
if ! command -v python3 >/dev/null 2>&1; then
  echo "A system python3 is needed only to run the bundling script (the app ships its own). Install Xcode Command Line Tools:"
  xcode-select --install || true
  exit 1
fi
echo "1/3  Downloading the private Python runtime, packages and FFmpeg (one-off, ~1.5 GB)..."
python3 scripts/bundle_runtime.py
echo "2/3  Installing Electron..."
( cd app && npm install --no-audit --no-fund )
echo "3/3  Building the installer..."
( cd app && npx electron-builder --mac --publish never )
echo
echo "Done. Your installer is in:  $(pwd)/dist"
open dist
