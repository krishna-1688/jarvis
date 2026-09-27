const { app, BrowserWindow, globalShortcut, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');

// Dev (npm run electron) sets ELECTRON_START_URL and uses the Vite server;
// otherwise the prebuilt dist/ is loaded, which is how the backend's
// "open dashboard" and run.py's Ctrl+J start it — no dev server needed.
const DEV_URL = process.env.ELECTRON_START_URL;
const isDev = Boolean(DEV_URL);

// One dashboard at a time: a second launch just focuses the open window.
if (!app.requestSingleInstanceLock()) {
  app.quit();
}

let consoleWin = null;

function loadWindow(win, mode) {
  if (isDev) {
    win.loadURL(`${DEV_URL}/?window=${mode}`);
  } else {
    win.loadFile(path.join(__dirname, '../dist/index.html'), { search: `window=${mode}` });
  }
}

function createConsoleWindow() {
  consoleWin = new BrowserWindow({
    width: 1200,
    height: 760,
    minWidth: 900,
    minHeight: 600,
    frame: false,
    show: false,
    backgroundColor: '#04050A',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  loadWindow(consoleWin, 'console');
  consoleWin.once('ready-to-show', () => consoleWin.show());

  // Closing the window exits the app. Hiding it kept Chromium resident
  // (~370 MB measured) all day for a window that's rarely open; the
  // conversation is restored from the backend on the next open instead.
  consoleWin.on('closed', () => { consoleWin = null; });
}

function focusConsole() {
  if (!consoleWin) return;
  if (consoleWin.isMinimized()) consoleWin.restore();
  consoleWin.show();
  consoleWin.focus();
}

app.on('second-instance', focusConsole);

function shutdownApp() {
  app.isQuitting = true;
  app.quit();
}

app.whenReady().then(() => {
  createConsoleWindow();
  // Ctrl+J is owned by run.py (it must work while this app isn't running);
  // Ctrl+Shift+Q closes the dashboard from anywhere while it is open.
  globalShortcut.register('CommandOrControl+Shift+Q', shutdownApp);
});

app.on('before-quit', () => { app.isQuitting = true; });

app.on('will-quit', () => { globalShortcut.unregisterAll(); });

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

ipcMain.handle('window-control', (_event, { action }) => {
  if (!consoleWin) return;
  if (action === 'minimize') consoleWin.minimize();
  if (action === 'close') consoleWin.close();
  if (action === 'shutdown') shutdownApp();
});

// LMS/assignment links open in the system browser, never inside the app
// window, and only for http(s) URLs.
ipcMain.handle('open-external', (_event, url) => {
  if (typeof url === 'string' && /^https?:\/\//i.test(url)) shell.openExternal(url);
});

ipcMain.handle('open-console', () => {
  if (!consoleWin) return;
  consoleWin.show();
  consoleWin.focus();
});

// Rack layouts persist to userData JSON (not localStorage) per Section 1.4,
// so widget/console layouts survive independently of any web storage the
// renderer's origin might lose (e.g. a cache clear).
function layoutsDir() {
  const dir = path.join(app.getPath('userData'), 'layouts');
  if (!fs.existsSync(dir)) fs.mkdirSync(dir, { recursive: true });
  return dir;
}

ipcMain.handle('layout-load', (_event, rackId) => {
  try {
    const file = path.join(layoutsDir(), `${rackId}.json`);
    if (!fs.existsSync(file)) return null;
    return JSON.parse(fs.readFileSync(file, 'utf-8'));
  } catch {
    return null;
  }
});

ipcMain.handle('layout-save', (_event, rackId, data) => {
  try {
    const file = path.join(layoutsDir(), `${rackId}.json`);
    fs.writeFileSync(file, JSON.stringify(data));
    return true;
  } catch {
    return false;
  }
});
