const { app, BrowserWindow } = require('electron');
const path = require('path');
const fs = require('fs');

app.whenReady().then(() => {
  const win = new BrowserWindow({
    width: 1100, height: 2000, show: true,
    webPreferences: { contextIsolation: true, backgroundThrottling: false },
  });
  win.loadURL('http://localhost:5173/?window=scratch');
  win.webContents.once('did-finish-load', () => {
    setTimeout(async () => {
      const label = process.env.JARVIS_CLICK_LABEL;
      if (label) {
        await win.webContents.executeJavaScript(`
          [...document.querySelectorAll('button')]
            .find(b => b.textContent.trim().toLowerCase() === '${label.toLowerCase()}')
            ?.click();
        `);
      }
      const wait = Number(process.env.JARVIS_CLICK_WAIT || 600);
      setTimeout(async () => {
        const img = await win.webContents.capturePage();
        const name = process.env.JARVIS_SHOT_NAME || 'scratch';
        fs.writeFileSync(path.join(process.env.JARVIS_SCREENSHOT_DIR, `${name}.png`), img.toPNG());
        app.quit();
      }, wait);
    }, 700);
  });
});
