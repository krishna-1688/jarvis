const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvis', {
  windowControl: (windowName, action) => ipcRenderer.invoke('window-control', { window: windowName, action }),
  openConsole:   () => ipcRenderer.invoke('open-console'),
  openExternal:  (url) => ipcRenderer.invoke('open-external', url),
  // Read fresh each call: the backend creates the token on its first start,
  // which can be after this window opened.
  apiToken:      () => ipcRenderer.sendSync('get-api-token'),
});
