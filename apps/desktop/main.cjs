const {app, BrowserWindow, shell, session, protocol} = require('electron');
const path = require('node:path');
const {pathToFileURL} = require('node:url');

const DEV_URL = 'http://127.0.0.1:5174/';
const isDev = !app.isPackaged;
protocol.registerSchemesAsPrivileged([{scheme:'app',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);

function createWindow() {
  const win = new BrowserWindow({
    width: 1360, height: 860, minWidth: 960, minHeight: 640,
    title: '同频 AI',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
    },
  });
  win.webContents.setWindowOpenHandler(({url}) => {
    if (url.startsWith('https://')) shell.openExternal(url);
    return {action: 'deny'};
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith(isDev ? DEV_URL : 'app://ui/')) event.preventDefault();
  });
  if (isDev) win.loadURL(DEV_URL);
  else win.loadURL('app://ui/index.html');
}

app.whenReady().then(() => {
  session.defaultSession.setPermissionRequestHandler((_webContents, _permission, callback) => callback(false));
  if (!isDev) {
    const root = path.resolve(__dirname, '../../dist/workbench');
    protocol.handle('app', request => {
      const url = new URL(request.url);
      if (url.hostname !== 'ui') return new Response('Forbidden', {status: 403});
      const file = path.resolve(root, '.' + decodeURIComponent(url.pathname));
      if (!file.startsWith(root + path.sep)) return new Response('Forbidden', {status: 403});
      return require('electron').net.fetch(pathToFileURL(file).toString());
    });
  }
  createWindow();
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
