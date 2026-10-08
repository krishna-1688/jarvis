const { app, BrowserWindow, globalShortcut, ipcMain, screen, shell } = require('electron');
const fs = require('fs');
const path = require('path');

// The backend only answers browser-context requests that carry this
// install's token (backend/core/security.py), so a web page open in the
// user's browser can't read their data or send commands. The renderer
// gets it from here via the preload; pages on the web have no way to.
const TOKEN_PATH = path.join(__dirname, '..', '..', 'backend', 'data', '.api_token');
// Dev (npm run electron) sets ELECTRON_START_URL and uses the Vite server;
// otherwise the prebuilt dist/ is loaded, which is how the backend's
// "open dashboard" and run.py's Ctrl+J start it — no dev server needed.
const DEV_URL = process.env.ELECTRON_START_URL;
const isDev = Boolean(DEV_URL);
const isOwnPage = (url) =>
  typeof url === 'string' && (url.startsWith('file://') || (isDev && url.startsWith(DEV_URL)));

ipcMain.on('get-api-token', (event) => {
  if (!isOwnPage(event.senderFrame?.url)) { event.returnValue = ''; return; }
  try {
    event.returnValue = fs.readFileSync(TOKEN_PATH, 'utf8').trim();
  } catch {
    event.returnValue = '';
  }
});


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
  // The console is designed at 1500x950; shrink to fit smaller screens.
  const area = screen.getPrimaryDisplay().workAreaSize;
  consoleWin = new BrowserWindow({
    width: Math.min(1500, Math.round(area.width * 0.92)),
    height: Math.min(950, Math.round(area.height * 0.92)),
    minWidth: 900,
    minHeight: 600,
    frame: false,
    show: false,
    backgroundColor: '#0B0A09',
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
// The dashboard window holds the API token, so it must never load anyone
// else's page: links and window.open go to the user's real browser.
app.on('web-contents-created', (_event, contents) => {
  contents.on('will-navigate', (event, url) => {
    if (isOwnPage(url)) return;
    event.preventDefault();
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
  });
  contents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) shell.openExternal(url);
    return { action: 'deny' };
  });
});

ipcMain.handle('open-external', (_event, url) => {
  if (typeof url === 'string' && /^https?:\/\//i.test(url)) shell.openExternal(url);
});

ipcMain.handle('open-console', () => {
  if (!consoleWin) return;
  consoleWin.show();
  consoleWin.focus();
});
