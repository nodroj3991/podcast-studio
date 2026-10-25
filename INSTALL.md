# Installing Podcast Studio

The installers are not code-signed (Apple and Microsoft charge for that), so the first launch shows
a warning. This is expected; here is how to get past it once. After that the app opens normally.

## macOS (.dmg)

1. Open `PodcastStudio-<version>-mac-arm64.dmg` (Apple Silicon: M1/M2/M3/M4) or `…-x64.dmg` (Intel).
2. Drag **Podcast Studio** into **Applications**.
3. First launch — one of these will happen:
   * **"Podcast Studio can't be opened because Apple cannot check it for malicious software"** (macOS 14 and earlier):
     click **OK**, open **System Settings → Privacy & Security**, scroll down to *"Podcast Studio was blocked…"* and click **Open Anyway**, then **Open**.
   * **macOS 15 Sequoia**: the dialog has no "Open Anyway". Click **Done**, open **System Settings → Privacy & Security**, scroll to the **Security** section, click **Open Anyway** next to Podcast Studio, then confirm with your password.
   * Older trick that still works everywhere: **right-click** (or Control-click) the app in Applications → **Open** → **Open**.
4. If you get *"the app is damaged and can't be opened"*, macOS quarantined the download. Open **Terminal** (once, copy-paste this single line) and press Return:
   `xattr -dr com.apple.quarantine "/Applications/Podcast Studio.app"`
   then open the app again.
5. The app appears in the Dock and the menu bar (a small bars icon). Closing the window while a job runs keeps it working in the background; **Quit** is in the menu bar icon.

## Windows (.exe)

1. Double-click `PodcastStudio-<version>-win-x64.exe`.
2. **"Windows protected your PC"** (SmartScreen): click **More info**, then **Run anyway**.
3. Follow the installer (it creates a Desktop shortcut and a Start menu entry). The app also sits in the system tray while it works.
4. If your antivirus quarantines `ffmpeg.exe` or `python.exe` inside the app folder, add the install folder to its exclusions — they are the standard FFmpeg and CPython builds (see `third_party/ffmpeg/SOURCE.txt`).

## First launch

* The app detects your hardware and shows how long a 90-minute episode will take on this machine.
* It asks to download the transcription model once (145 MB – 1.5 GB depending on your machine) with a progress bar. Nothing else is downloaded unless you use music search, research charts, Canva, or updates.
* Optional one-time keys (Hugging Face for better speaker detection, Jamendo for more music, Canva) are under **Settings**, each with step-by-step instructions. The app works without any of them.

## Updates

**Help → Check for updates**. New versions come from the project's GitHub Releases page; the app downloads the new installer and swaps itself on restart. Your episodes, presets and keys are untouched (they live in Documents/Podcast Episodes and the app data folder).

## Uninstall

macOS: drag the app to the Bin; app data is in `~/Library/Application Support/PodcastStudio`.
Windows: Settings → Apps → Podcast Studio → Uninstall; app data is in `%APPDATA%\PodcastStudio`.
Episodes in `Documents/Podcast Episodes` are never deleted by the app.
