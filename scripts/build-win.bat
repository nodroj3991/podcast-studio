@echo off
REM Double-click to build "Podcast Studio Setup.exe" on Windows (10-20 minutes the first time).
cd /d "%~dp0\.."
echo == Podcast Studio - Windows build ==
where node >nul 2>nul || (echo Node.js is needed. Opening the download page - install it, then run this again. & start https://nodejs.org/en/download & pause & exit /b 1)
where python >nul 2>nul || (echo Python 3 is needed only to run the bundling script. Opening python.org - tick "Add python.exe to PATH" when installing. & start https://www.python.org/downloads/windows/ & pause & exit /b 1)
echo 1/3  Downloading the private Python runtime, packages and FFmpeg (one-off, ~1.5 GB)...
python scripts\bundle_runtime.py || (pause & exit /b 1)
echo 2/3  Installing Electron...
cd app && call npm install --no-audit --no-fund || (pause & exit /b 1)
echo 3/3  Building the installer...
call npx electron-builder --win --publish never || (pause & exit /b 1)
cd ..
echo.
echo Done. Your installer is in: %cd%\dist
start "" "%cd%\dist"
pause
