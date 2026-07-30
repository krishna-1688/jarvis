const path = require('path');
const fs = require('fs');
process.env.ELECTRON_START_URL = 'http://localhost:5173';

const { app, BrowserWindow } = require('electron');
const mainModulePath = path.join(__dirname, 'main.js');

app.whenReady().then(() => {
  setTimeout(async () => {
    const wins = BrowserWindow.getAllWindows();
    const dir = process.env.JARVIS_SCREENSHOT_DIR;
    for (const win of wins) {
      const url = win.webContents.getURL();
      const name = url.includes('window=widget') ? 'widget' : 'console';
      // eslint-disable-next-line no-await-in-loop
      const img = await win.webContents.capturePage();
      fs.writeFileSync(path.join(dir, `fullapp_${name}.png`), img.toPNG());
    }
    app.quit();
  }, 2500);
});

require(mainModulePath);
