const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

app.whenReady().then(() => {
  const win = new BrowserWindow({
    width: 1200, height: 760, show: true,
    webPreferences: { contextIsolation: true, backgroundThrottling: false },
  });
  win.loadURL('http://localhost:5173/?window=console');
  win.webContents.once('did-finish-load', () => {
    setTimeout(async () => {
      const clicks = (process.env.JARVIS_CLICKS || '').split(',').filter(Boolean);
      for (const sel of clicks) {
        // eslint-disable-next-line no-await-in-loop
        await win.webContents.executeJavaScript(sel);
        // eslint-disable-next-line no-await-in-loop
        await new Promise((r) => setTimeout(r, 350));
      }
      const wait = Number(process.env.JARVIS_CLICK_WAIT || 500);
      setTimeout(async () => {
        const img = await win.webContents.capturePage();
        const name = process.env.JARVIS_SHOT_NAME || 'console_test';
        fs.writeFileSync(path.join(process.env.JARVIS_SCREENSHOT_DIR, `${name}.png`), img.toPNG());
        app.quit();
      }, wait);
    }, 700);
  });
});
