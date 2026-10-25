const { contextBridge, ipcRenderer, webUtils } = require("electron");

contextBridge.exposeInMainWorld("studio", {
  isElectron: true,
  pickFiles: () => ipcRenderer.invoke("pick-files"),
  pickFolder: () => ipcRenderer.invoke("pick-folder"),
  pathFor: (file) => { try { return webUtils.getPathForFile(file); } catch (_) { return file.path || null; } },
  setBusy: (b) => ipcRenderer.invoke("set-busy", b),
  notify: (title, body) => ipcRenderer.invoke("notify", title, body),
  openExternal: (url) => ipcRenderer.invoke("open-external", url),
  showInFolder: (p) => ipcRenderer.invoke("show-in-folder", p),
  openPath: (p) => ipcRenderer.invoke("open-path", p),
  appInfo: () => ipcRenderer.invoke("app-info"),
  checkUpdate: () => ipcRenderer.invoke("check-update"),
  downloadUpdate: () => ipcRenderer.invoke("download-update"),
  installUpdate: () => ipcRenderer.invoke("install-update"),
  onUpdateStatus: (cb) => ipcRenderer.on("update-status", (_e, s) => cb(s)),
  onUpdateProgress: (cb) => ipcRenderer.on("update-progress", (_e, p) => cb(p)),
});
