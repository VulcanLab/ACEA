const { app, BrowserWindow } = require('electron')
const path = require('path')

const DEV_URL = 'http://localhost:3030'

function createWindow() {
  const win = new BrowserWindow({
    width: 1280,
    height: 720,
    resizable: false,
    title: 'Ghost Signal',
    backgroundColor: '#0a0a0f',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      nodeIntegration: false,
      contextIsolation: true,
    },
  })

  win.setMenuBarVisibility(false)
  win.loadURL(DEV_URL)
}

app.whenReady().then(createWindow)
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })
app.on('activate', () => { if (BrowserWindow.getAllWindows().length === 0) createWindow() })
