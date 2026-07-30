const { app, BrowserWindow, globalShortcut, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');

const isDev  = !app.isPackaged;
const DEV_URL = process.env.ELECTRON_START_URL || 'http://localhost:5173';

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
    backgroundColor: '#E8E6E1',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  loadWindow(consoleWin, 'console');
  consoleWin.once('ready-to-show', () => consoleWin.show());

  // Hide (don't destroy) on close so Ctrl+J can bring it straight back
  // without a reload — the console holds live layout/log state.
  consoleWin.on('close', (e) => {
    if (!app.isQuitting) {
      e.preventDefault();
      consoleWin.hide();
    }
  });
}

function toggleConsole() {
  if (!consoleWin) return;
  if (consoleWin.isVisible() && consoleWin.isFocused()) {
    consoleWin.hide();
  } else {
    consoleWin.show();
    consoleWin.focus();
  }
}

function shutdownApp() {
  app.isQuitting = true;
  app.quit();
}

app.whenReady().then(() => {
  createConsoleWindow();
  globalShortcut.register('CommandOrControl+J', toggleConsole);
  // Full app shutdown — separate from Ctrl+J (which just hides/shows
  // the console) and from the close button (which also just hides it,
  // per the close handler above, to preserve live layout/log state).
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
  if (action === 'close') consoleWin.hide();
  if (action === 'shutdown') shutdownApp();
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
