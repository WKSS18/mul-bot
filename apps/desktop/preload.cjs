const {contextBridge} = require('electron');
contextBridge.exposeInMainWorld('tongpinDesktop', Object.freeze({platform: 'electron', version: 1}));
