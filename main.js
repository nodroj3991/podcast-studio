// Podcast Studio - Electron main process.
// Starts the bundled Python engine (local HTTP API on 127.0.0.1), opens the window on it,
// keeps the machine awake while jobs run, shows a desktop notification when a job finishes,
// keeps running in the tray when the window is closed, and checks GitHub Releases for updates.
const { app, BrowserWindow, Tray, Menu, Notification, powerSaveBlocker, ipcMain, dialog, shell, nativeImage } = require("electron");
const { spawn } = require("child_process");
const http = require("http");
const path = require("path");
const fs = require("fs");

const PORT = 5890;
const isDev = !app.isPackaged;
let win = null, tray = null, engine = null, blockerId = null, quitting = false;

// ------------------------------------------------------------------ engine process
function enginePaths() {
  // Packaged: resources/runtime/<python>  +  resources/engine
  // Dev:      system python3 + ../engine
  const res = process.resourcesPath;
  if (!isDev) {
    const py = process.platform === "win32"
      ? path.join(res, "runtime", "python", "python.exe")
      : path.join(res, "runtime", "python", "bin", "python3");
    const ffdir = path.join(res, "third_party", "ffmpeg");
    return { python: py, cwd: res, ffmpegDir: ffdir, models: path.join(app.getPath("userData"), "models") };
  }
  return { python: process.platform === "win32" ? "python" : "python3", cwd: path.join(__dirname, ".."), ffmpegDir: null,
           models: path.join(app.getPath("userData"), "models") };
}

function startEngine() {
  const p = enginePaths();
  const env = { ...process.env, PYTHONUNBUFFERED: "1", PODCAST_STUDIO_DATA: app.getPath("userData"),
                PYTHONIOENCODING: "utf-8", HF_HOME: path.join(app.getPath("userData"), "models", "hf") };
  if (p.ffmpegDir) env.PATH = p.ffmpegDir + path.delimiter + env.PATH;   // bundled ffmpeg/ffprobe first
  const episodes = path.join(app.getPath("documents"), "Podcast Episodes");
  engine = spawn(p.python, ["-m", "engine", "serve", "--port", String(PORT), "--episodes", episodes], { cwd: p.cwd, env });
  const log = fs.createWriteStream(path.join(app.getPath("userData"), "engine-stdout.log"), { flags: "a" });
  engine.stdout.pipe(log); engine.stderr.pipe(log);
  engine.on("exit", (code) => {
    log.write(`\n[engine exited with ${code}]\n`);
    if (!quitting) {
      dialog.showErrorBox("Podcast Studio stopped", "The processing engine stopped unexpectedly. Please reopen the app. " +
        "If it keeps happening, send the log file from Settings > Help.");
    }
  });
}

function waitForEngine(tries = 120) {
  return new Promise((resolve, reject) => {
    const tick = () => {
      http.get({ host: "127.0.0.1", port: PORT, path: "/api/hw", timeout: 2000 }, (r) => { r.resume(); resolve(); })
        .on("error", () => (tries-- > 0 ? setTimeout(tick, 500) : reject(new Error("engine did not start"))));
    };
    tick();
  });
}

// ------------------------------------------------------------------ window & tray
function createWindow() {
  win = new BrowserWindow({
    width: 1240, height: 820, minWidth: 980, minHeight: 640, show: false,
    title: "Podcast Studio", backgroundColor: "#0f1b2d",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, nodeIntegration: false },
  });
  win.loadURL(`http://127.0.0.1:${PORT}/`);
  win.once("ready-to-show", () => win.show());
  if (process.env.PODCAST_STUDIO_SCREENSHOT) {           // automated smoke test: capture the window and quit
    win.webContents.once("did-finish-load", async () => {
      await new Promise((r) => setTimeout(r, 3000));
      const img = await win.webContents.capturePage();
      fs.writeFileSync(process.env.PODCAST_STUDIO_SCREENSHOT, img.toPNG());
      const ok = await win.webContents.executeJavaScript("!!(window.studio && window.studio.isElectron) && !!document.querySelector('h1')");
      console.log("SMOKE", ok ? "OK" : "FAIL");
      quitting = true; app.quit();
    });
  }
  win.on("close", (e) => { if (!quitting && blockerId !== null) { e.preventDefault(); win.hide(); } });   // keep job running when closed mid-job
  win.on("closed", () => (win = null));
  win.webContents.setWindowOpenHandler(({ url }) => { shell.openExternal(url); return { action: "deny" }; });
}

function createTray() {
  const iconPath = path.join(__dirname, "build", process.platform === "win32" ? "icon.ico" : "trayTemplate.png");
  const img = fs.existsSync(iconPath) ? nativeImage.createFromPath(iconPath) : nativeImage.createEmpty();
  tray = new Tray(img);
  tray.setToolTip("Podcast Studio");
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: "Open Podcast Studio", click: () => (win ? win.show() : createWindow()) },
    { type: "separator" },
    { label: "Quit", click: () => { quitting = true; app.quit(); } },
  ]));
  tray.on("click", () => (win ? win.show() : createWindow()));
}

// ------------------------------------------------------------------ ipc from renderer
ipcMain.handle("pick-files", async () => {
  const r = await dialog.showOpenDialog(win, { properties: ["openFile", "multiSelections"],
    filters: [{ name: "Recordings", extensions: ["mp4", "mov", "mkv", "m4v", "wav", "mp3", "m4a", "aac", "flac"] }] });
  return r.canceled ? [] : r.filePaths;
});
ipcMain.handle("pick-folder", async () => {
  const r = await dialog.showOpenDialog(win, { properties: ["openDirectory", "createDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("set-busy", (_e, busy) => {
  if (busy && blockerId === null) blockerId = powerSaveBlocker.start("prevent-app-suspension");
  if (!busy && blockerId !== null) { powerSaveBlocker.stop(blockerId); blockerId = null; }
  if (tray) tray.setToolTip(busy ? "Podcast Studio - working" : "Podcast Studio");
  return blockerId !== null;
});
ipcMain.handle("notify", (_e, title, body) => {
  if (Notification.isSupported()) {
    const n = new Notification({ title, body });
    n.on("click", () => { if (win) { win.show(); win.focus(); } else createWindow(); });
    n.show();
  }
  return true;
});
ipcMain.handle("open-external", (_e, url) => shell.openExternal(url));
ipcMain.handle("show-in-folder", (_e, p) => shell.showItemInFolder(p));
ipcMain.handle("open-path", (_e, p) => shell.openPath(p));
ipcMain.handle("app-info", () => ({ version: app.getVersion(), platform: process.platform, userData: app.getPath("userData"),
  engineLog: path.join(app.getPath("userData"), "engine-stdout.log") }));
ipcMain.handle("check-update", async () => checkForUpdates(true));

// ------------------------------------------------------------------ updates (GitHub Releases, no code signing needed for the check)
let updateStatus = { state: "idle" };
async function checkForUpdates(manual) {
  try {
    const { autoUpdater } = require("electron-updater");
    autoUpdater.autoDownload = false;
    autoUpdater.on("update-available", (info) => { updateStatus = { state: "available", version: info.version }; if (win) win.webContents.send("update-status", updateStatus); });
    autoUpdater.on("update-not-available", () => { updateStatus = { state: "none" }; if (win) win.webContents.send("update-status", updateStatus); });
    autoUpdater.on("error", (err) => { updateStatus = { state: "error", message: String(err && err.message || err) }; if (win) win.webContents.send("update-status", updateStatus); });
    const r = await autoUpdater.checkForUpdates();
    return { checked: true, version: r && r.updateInfo ? r.updateInfo.version : null, ...updateStatus };
  } catch (e) {
    return { checked: false, state: "error", message: String(e && e.message || e) };
  }
}
ipcMain.handle("download-update", async () => {
  const { autoUpdater } = require("electron-updater");
  autoUpdater.on("download-progress", (p) => win && win.webContents.send("update-progress", p));
  autoUpdater.on("update-downloaded", () => { win && win.webContents.send("update-status", { state: "downloaded" }); });
  await autoUpdater.downloadUpdate();
  return true;
});
ipcMain.handle("install-update", () => { quitting = true; require("electron-updater").autoUpdater.quitAndInstall(); });

// ------------------------------------------------------------------ lifecycle
app.whenReady().then(async () => {
  startEngine();
  try { await waitForEngine(); } catch (e) {
    dialog.showErrorBox("Podcast Studio could not start", "The processing engine did not start. See engine-stdout.log in the app data folder (Settings > Help).");
  }
  createWindow();
  createTray();
  if (!isDev) setTimeout(() => checkForUpdates(false), 8000);
  app.on("activate", () => { if (BrowserWindow.getAllWindows().length === 0) createWindow(); else if (win) win.show(); });
});
app.on("window-all-closed", () => { /* keep running in tray while a job runs; Quit from the tray menu */ if (process.platform !== "darwin" && blockerId === null) { quitting = true; app.quit(); } });
app.on("before-quit", () => { quitting = true; if (engine) { try { engine.kill(); } catch (_) {} } });
