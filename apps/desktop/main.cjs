const {app, BrowserWindow, shell, session, protocol, ipcMain, net, dialog, screen} = require('electron');
const path = require('node:path');
const fs = require('node:fs');
const {pathToFileURL} = require('node:url');

const DEV_URL = 'http://127.0.0.1:5174/';
const isDev = !app.isPackaged;
const configFile = path.join(__dirname, 'public-config.json');
const publicConfig = fs.existsSync(configFile) ? JSON.parse(fs.readFileSync(configFile, 'utf8')) : {apiBaseUrl:'http://127.0.0.1:8000/api/v1',wsUrl:'ws://127.0.0.1:8000/api/v1/realtime'};
if (app.isPackaged && !fs.existsSync(configFile)) throw new Error('Missing desktop public configuration');
const apiBase = publicConfig.apiBaseUrl;
let accessToken = '';
let mainWindow;
const hasSingleInstanceLock = app.requestSingleInstanceLock();
if (!hasSingleInstanceLock) app.quit();
protocol.registerSchemesAsPrivileged([{scheme:'app',privileges:{standard:true,secure:true,supportFetchAPI:true}}]);

function windowStatePath() { return path.join(app.getPath('userData'), 'window-state.json'); }
function readWindowState() {
  try {
    const state = JSON.parse(fs.readFileSync(windowStatePath(), 'utf8'));
    if ([state.x,state.y,state.width,state.height].every(Number.isFinite) && state.width >= 960 && state.height >= 640) {
      const visible = screen.getAllDisplays().some(display => {
        const area = display.workArea;
        return state.x < area.x + area.width && state.x + state.width > area.x && state.y < area.y + area.height && state.y + state.height > area.y;
      });
      if (visible) return state;
    }
  } catch {}
  return {width:1360,height:860};
}
function saveWindowState(win) {
  if (!win || win.isDestroyed() || win.isMinimized()) return;
  const bounds = win.isMaximized() ? win.getNormalBounds() : win.getBounds();
  fs.writeFileSync(windowStatePath(), JSON.stringify({...bounds,maximized:win.isMaximized()}));
}

async function apiRequest(requestPath, method='GET', body) {
  if (typeof requestPath !== 'string' || !/^\/(?:auth|me|workspaces|rooms|bots|bot-test-runs|runs|records|uploads|files|invitations|realtime)(?:\/|\?|$)/.test(requestPath) || requestPath.includes('..') || requestPath.includes('://') || !['GET','POST','PATCH','PUT','DELETE'].includes(method)) throw new Error('Invalid API request');
  const headers = {'Content-Type':'application/json'};
  if (accessToken) headers.Authorization = `Bearer ${accessToken}`;
  if (requestPath === '/auth/refresh' || requestPath === '/auth/logout') {
    const cookies = await session.defaultSession.cookies.get({url:apiBase, name:'tp_csrf'});
    if (cookies[0]) headers['X-CSRF-Token'] = cookies[0].value;
  }
  const response = await net.fetch(apiBase.replace(/\/$/,'') + requestPath, {method,headers,body:body || undefined});
  const data = await response.json().catch(()=>({}));
  if (response.ok && ['/auth/login','/auth/register','/auth/refresh'].includes(requestPath) && data.data?.access_token) {
    accessToken = data.data.access_token;
    data.data.access_token = 'desktop-session';
  }
  if (requestPath === '/auth/logout') accessToken = '';
  return {status:response.status,data};
}

function requireSuccess(result) {
  if (result.status < 200 || result.status >= 300) throw new Error(result.data?.error?.message || `API error ${result.status}`);
  return result.data.data;
}

function createWindow() {
  const saved = readWindowState();
  const win = new BrowserWindow({
    ...saved, minWidth: 960, minHeight: 640,
    title: '同频 AI',
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
    },
  });
  mainWindow = win;
  if (saved.maximized) win.maximize();
  win.on('close', () => saveWindowState(win));
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
  ipcMain.on('tp:public-config', event => {if (event.sender === mainWindow?.webContents) event.returnValue = {wsUrl:publicConfig.wsUrl};});
  ipcMain.handle('tp:request', async (event, input) => {
    if (event.sender !== mainWindow?.webContents) throw new Error('Invalid sender');
    return apiRequest(input?.path, input?.method || 'GET', input?.body);
  });
  ipcMain.handle('tp:upload-file', async (event, workspaceId) => {
    if (event.sender !== mainWindow?.webContents || !accessToken || typeof workspaceId !== 'string' || !/^[a-f0-9-]{36}$/i.test(workspaceId)) throw new Error('Invalid upload request');
    const selection = await dialog.showOpenDialog(mainWindow, {properties:['openFile'],filters:[{name:'Documents and images',extensions:['pdf','docx','txt','md','png','jpg','jpeg']}]});
    if (selection.canceled || !selection.filePaths[0]) return null;
    const selected = selection.filePaths[0];
    const info = await fs.promises.stat(selected);
    if (!info.isFile() || info.size < 1 || info.size > 20*1024*1024) throw new Error('File must be between 1 byte and 20 MB');
    const filename = path.basename(selected);
    const mime = ({'.pdf':'application/pdf','.docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document','.txt':'text/plain','.md':'text/markdown','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg'})[path.extname(filename).toLowerCase()];
    if (!mime) throw new Error('Unsupported file type');
    const init = requireSuccess(await apiRequest('/uploads/init','POST',JSON.stringify({workspace_id:workspaceId,filename,mime,size:info.size})));
    const form = new FormData();
    for (const [key,value] of Object.entries(init.upload.fields)) form.append(key,value);
    form.append('file',new Blob([await fs.promises.readFile(selected)],{type:mime}),filename);
    const destination = new URL(init.upload.url);
    if (destination.protocol !== 'https:' || destination.hostname !== `${publicConfig.ossBucket || 'wh-syyoss'}.oss-cn-beijing.aliyuncs.com`) throw new Error('Unexpected upload destination');
    const uploaded = await net.fetch(destination.toString(),{method:'POST',body:form});
    if (!uploaded.ok) throw new Error(`OSS upload failed (${uploaded.status})`);
    requireSuccess(await apiRequest(`/uploads/${init.file_id}/complete`,'POST'));
    return {id:init.file_id,filename,mime};
  });
  ipcMain.handle('tp:save-file', async (event, fileId, filename) => {
    if (event.sender !== mainWindow?.webContents || !accessToken || typeof fileId !== 'string' || !/^[a-f0-9-]{36}$/i.test(fileId)) throw new Error('Invalid file request');
    const access = requireSuccess(await apiRequest(`/files/${fileId}/access?mode=download`));
    const destination = new URL(access.url);
    if (destination.protocol !== 'https:' || destination.hostname !== `${publicConfig.ossBucket || 'wh-syyoss'}.oss-cn-beijing.aliyuncs.com`) throw new Error('Unexpected download source');
    const choice = await dialog.showSaveDialog(mainWindow,{defaultPath:path.basename(String(filename || 'download'))});
    if (choice.canceled || !choice.filePath) return false;
    const downloaded = await net.fetch(destination.toString());
    if (!downloaded.ok) throw new Error(`Download failed (${downloaded.status})`);
    const target = choice.filePath;
    const temporary = `${target}.part`;
    try {
      await fs.promises.writeFile(temporary,Buffer.from(await downloaded.arrayBuffer()));
      await fs.promises.rename(temporary,target);
    } catch (error) {
      await fs.promises.rm(temporary,{force:true});
      throw error;
    }
    return true;
  });
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
app.on('second-instance', () => {
  if (!mainWindow) return;
  if (mainWindow.isMinimized()) mainWindow.restore();
  mainWindow.show();
  mainWindow.focus();
});
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
