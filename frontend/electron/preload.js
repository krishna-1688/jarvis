const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvis', {
  windowControl: (windowName, action) => ipcRenderer.invoke('window-control', { window: windowName, action }),
  openConsole:   () => ipcRenderer.invoke('open-console'),
  openExternal:  (url) => ipcRenderer.invoke('open-external', url),
});
