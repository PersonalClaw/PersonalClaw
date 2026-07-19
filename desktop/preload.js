const { contextBridge, ipcRenderer } = require("electron");

// Bridge exposed to the dashboard renderer. `onStatus` lets the loading screen
// subscribe to backend startup status; the returned function unsubscribes.
contextBridge.exposeInMainWorld("electronAPI", {
  onStatus: (cb) => {
    const handler = (_e, msg) => cb(msg);
    ipcRenderer.on("status", handler);
    return () => ipcRenderer.removeListener("status", handler);
  },
});
