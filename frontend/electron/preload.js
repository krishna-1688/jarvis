const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvis', {
  windowControl: (windowName, action) => ipcRenderer.invoke('window-control', { window: windowName, action }),
  openConsole:   () => ipcRenderer.invoke('open-console'),
  loadLayout:    (rackId) => ipcRenderer.invoke('layout-load', rackId),
  saveLayout:    (rackId, data) => ipcRenderer.invoke('layout-save', rackId, data),
});
