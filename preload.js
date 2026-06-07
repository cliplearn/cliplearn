// ClipLearn preload script for Electron
// Exposes electronAPI to the renderer process via contextBridge
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
    // Window controls
    closeToMini: () => ipcRenderer.send('close-to-mini'),
    miniClick: () => ipcRenderer.send('mini-click'),

    // Screenshot
    startScreenshot: () => ipcRenderer.send('start-screenshot'),
    onScreenshotCaptured: (callback) => ipcRenderer.on('screenshot-captured', (event, dataUrl) => callback(dataUrl)),
    getScreenshot: () => ipcRenderer.invoke('get-screenshot'),
    sendScreenshotCaptured: (dataUrl) => ipcRenderer.send('screenshot-captured', dataUrl),
    onScreenshotMode: (callback) => ipcRenderer.on('screenshot-mode', (event, data) => callback(data)),
});
