const {contextBridge, ipcRenderer} = require('electron');
const publicConfig = ipcRenderer.sendSync('tp:public-config');
contextBridge.exposeInMainWorld('tongpinDesktop', Object.freeze({
  platform: 'electron', version: 1,
  wsUrl: publicConfig.wsUrl,
  request: (path, options={}) => ipcRenderer.invoke('tp:request', {path,method:options.method||'GET',body:options.body||null}),
  uploadFile: workspaceId => ipcRenderer.invoke('tp:upload-file', workspaceId),
  saveFile: (fileId, filename) => ipcRenderer.invoke('tp:save-file', fileId, filename),
}));
