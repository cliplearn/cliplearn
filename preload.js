const { contextBridge, ipcRenderer } = require('electron');

// 向渲染进程暴露安全的 Electron API
contextBridge.exposeInMainWorld('electronAPI', {
    // 关闭主窗口 → 浮动徽标
    closeToMini: () => ipcRenderer.send('close-to-mini'),
    miniClick: () => ipcRenderer.send('mini-click'),
    startScreenshot: () => ipcRenderer.send('start-screenshot'),
    getScreenshot: () => ipcRenderer.invoke('get-screenshot'),
    sendScreenshotCaptured: (dataUrl) => ipcRenderer.send('screenshot-captured', dataUrl),
    onScreenshotMode: (callback) => {
        ipcRenderer.on('screenshot-mode', (event, data) => callback(data));
    },

    // 监听截图结果
    onScreenshotCaptured: (callback) => {
        ipcRenderer.on('process-screenshot', (event, dataUrl) => callback(dataUrl));
    },

    // 移除监听
    removeScreenshotListener: () => {
        ipcRenderer.removeAllListeners('process-screenshot');
    },

    // 平台信息
    platform: process.platform
});
