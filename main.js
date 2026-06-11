const { app, BrowserWindow, Tray, Menu, globalShortcut, nativeImage, nativeTheme, screen, ipcMain } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

// ─── 配置 ───
const FLASK_PORT = 5000;
const FLASK_HOST = '127.0.0.1';
const FLASK_URL = `http://${FLASK_HOST}:${FLASK_PORT}`;
let mainWindow = null;    // 卡片主窗口
let miniWindow = null;    // 浮动 CL 徽标（关闭后显示）
let tray = null;          // 系统托盘
let flaskProcess = null;  // Python Flask 子进程
let screenshotMode = false; // 是否在截图模式
let restoreAfterOverlay = false;

// ─── 1. 启动 Python Flask 后端 ───
function startFlask() {
    // 优先使用 PyInstaller 冻结的独立 exe（打包后）
    const fs = require('fs');
    // resourcesPath = C:\cliplearn\resources (打包后) 或 undefined (开发)
    const resourcesDir = process.resourcesPath || path.join(__dirname, 'resources');
    const frozenExe = path.join(resourcesDir, 'ClipLearn-server.exe');

    if (fs.existsSync(frozenExe)) {
        // 生产环境：CWD 设为安装根目录（resources 的上级），保证 DB/audio 可写
        const installRoot = path.dirname(resourcesDir); // C:\cliplearn
        console.log('[Main] 启动冻结后端:', frozenExe);
        console.log('[Main] 工作目录:', installRoot);
        flaskProcess = spawn(frozenExe, [], {
            cwd: installRoot,
            env: { ...process.env, FLASK_DEBUG: '0' },
            stdio: ['ignore', 'pipe', 'pipe']
        });
    } else {
        // 开发模式：使用 .venv 中的 Python
        const venvPython = process.platform === 'win32'
            ? path.join(__dirname, '.venv', 'Scripts', 'python.exe')
            : path.join(__dirname, '.venv', 'bin', 'python3');

        console.log('[Main] 启动开发后端:', venvPython);
        flaskProcess = spawn(venvPython, ['server/app.py'], {
            cwd: __dirname,
            env: { ...process.env, FLASK_DEBUG: '0' },
            stdio: ['ignore', 'pipe', 'pipe']
        });
    }

    flaskProcess.stdout.on('data', (data) => {
        console.log(`[Flask] ${data.toString().trim()}`);
    });

    flaskProcess.stderr.on('data', (data) => {
        console.log(`[Flask] ${data.toString().trim()}`);
    });

    flaskProcess.on('error', (err) => {
        console.error('[Flask] 启动失败:', err.message);
    });

    flaskProcess.on('close', (code) => {
        console.log(`[Flask] 进程退出，退出码: ${code}`);
    });
}

// ─── 2. 等待 Flask 就绪 ───
function waitForFlask(retries = 30, interval = 500) {
    return new Promise((resolve, reject) => {
        let attempts = 0;
        const check = () => {
            attempts++;
            const req = http.get(`${FLASK_URL}/api/status`, (res) => {
                if (res.statusCode === 200) {
                    resolve(true);
                } else {
                    console.log(`[Main] Flask 返回状态码 ${res.statusCode}，继续等待...`);
                    if (attempts >= retries) {
                        reject(new Error('Flask 后端未能在预期时间内启动'));
                    } else {
                        setTimeout(check, interval);
                    }
                }
                res.resume();
            });
            req.on('error', () => {
                if (attempts >= retries) {
                    reject(new Error('Flask 后端未能在预期时间内启动'));
                } else {
                    setTimeout(check, interval);
                }
            });
        };
        check();
    });
}

// ─── 3. 创建浮动 CL 徽标窗口（最小化/关闭后显示） ───
function createMiniWindow() {
    const { width: screenW } = screen.getPrimaryDisplay().workAreaSize;

    miniWindow = new BrowserWindow({
        width: 28,
        height: 28,
        x: screenW - 32,
        y: Math.floor(screen.getPrimaryDisplay().workAreaSize.height * 0.35),
        type: 'toolbar',
        frame: false,
        transparent: true,
        alwaysOnTop: true,
        resizable: false,
        skipTaskbar: true,
        focusable: true,
        acceptFirstMouse: true,
        hasShadow: false,
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.js')
        }
    });

    miniWindow.loadFile(path.join(__dirname, 'server', 'static', 'mini-badge.html'));
    miniWindow.setAlwaysOnTop(true, 'floating');

    miniWindow.on('close', (e) => {
        e.preventDefault();
        miniWindow.hide();
    });

    miniWindow.hide();
}

// ─── 4. 创建卡片主窗口 ───
function getWindowSize() {
    const { width: screenW, height: screenH } = screen.getPrimaryDisplay().workAreaSize;
    const windowWidth = Math.floor(screenW * 0.35);
    const windowHeight = screenH;
    return { windowWidth, windowHeight, screenW, screenH };
}

function createMainWindow() {
    const { windowWidth, windowHeight, screenW, screenH } = getWindowSize();

    mainWindow = new BrowserWindow({
        width: windowWidth,
        height: windowHeight,
        x: screenW - windowWidth,
        y: 0,
        frame: true,
        transparent: false,
        resizable: true,
        skipTaskbar: false,
        show: false,
        backgroundColor: '#1F1F1F',
        title: 'ClipLearn',
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.js')
        }
    });

    // 页面加载完成后才显示窗口，避免白屏闪烁
    mainWindow.webContents.on('did-finish-load', () => {
        if (mainWindow && !mainWindow.isDestroyed()) {
            mainWindow.show();
            mainWindow.focus();
        }
    });

    // 页面加载失败时的处理
    mainWindow.webContents.on('did-fail-load', (event, errorCode, errorDescription) => {
        console.error('[Main] 页面加载失败:', errorCode, errorDescription);
        // 稍后重试一次
        setTimeout(() => {
            if (mainWindow && !mainWindow.isDestroyed()) {
                console.log('[Main] 重试加载页面...');
                mainWindow.loadURL(FLASK_URL);
            }
        }, 2000);
    });

    // 屏幕分辨率变化时自动调整窗口位置与大小 (右侧 1/3 屏)
    const repositionWindow = () => {
        if (!mainWindow || mainWindow.isDestroyed()) return;
        const sz = getWindowSize();
        const bounds = mainWindow.getBounds();
        const newW = sz.windowWidth;
        const newH = sz.windowHeight;
        const newX = sz.screenW - newW;
        const newY = 0;
        if (bounds.x !== newX || bounds.y !== newY || bounds.width !== newW || bounds.height !== newH) {
            mainWindow.setBounds({ x: newX, y: newY, width: newW, height: newH });
        }
    };
    screen.on('display-metrics-changed', repositionWindow);
    mainWindow.on('resize', repositionWindow);

    mainWindow.loadURL(FLASK_URL);

    mainWindow.on('close', (e) => {
        if (app.isQuitting) {
            return;
        }

        // 关闭 → 变成浮动徽标
        e.preventDefault();
        hideMainToMini();
    });
}

// ─── 5. 系统托盘 ───
function hideMainToMini() {
    if (mainWindow && !mainWindow.isDestroyed()) {
        mainWindow.hide();
    }

    if (miniWindow && !miniWindow.isDestroyed()) {
        miniWindow.show();
    }
}

function restoreMainWindow() {
    if (miniWindow && !miniWindow.isDestroyed()) {
        miniWindow.hide();
    }

    if (!mainWindow || mainWindow.isDestroyed()) {
        createMainWindow();
    }

    if (mainWindow.isMinimized()) {
        mainWindow.restore();
    }

    // 面板定位到屏幕右侧（右侧 1/3 屏）
    const { windowWidth, windowHeight, screenW } = getWindowSize();
    const x = screenW - windowWidth;
    const y = 0;
    mainWindow.setBounds({ x, y, width: windowWidth, height: windowHeight });

    mainWindow.show();
    mainWindow.focus();
}

function createTray() {
    // 用代码生成 16x16 的 CL 图标（Data URL）
    const iconPath = path.join(__dirname, 'server', 'static', 'logo.png');
    let trayIcon;

    // 如果没有合适的 icon 文件，生成一个简单的
    try {
        trayIcon = nativeImage.createFromPath(iconPath);
        if (trayIcon.isEmpty()) throw new Error('empty');
        trayIcon = trayIcon.resize({ width: 16, height: 16 });
    } catch (e) {
        // 创建一个最小的 16x16 图标
        trayIcon = nativeImage.createEmpty();
    }

    tray = new Tray(trayIcon);
    tray.setToolTip('ClipLearn.ai');

    const contextMenu = Menu.buildFromTemplate([
        {
            label: '显示主窗口',
            click: () => restoreMainWindow()
        },
        {
            label: '选区截图',
            accelerator: 'Ctrl+Shift+C',
            click: () => startScreenshotMode()
        },
        { type: 'separator' },
        {
            label: '退出 ClipLearn.ai',
            click: () => {
                app.isQuitting = true;
                app.quit();
            }
        }
    ]);

    tray.setContextMenu(contextMenu);

    tray.on('double-click', () => {
        restoreMainWindow();
    });
}

// ─── 6. 全局快捷键：选区截图 ───
function registerGlobalShortcuts() {
    const ok = globalShortcut.register('CommandOrControl+Shift+C', () => {
        console.log('[Shortcut] Ctrl+Shift+C triggered');
        startScreenshotMode();
    });

    if (ok) {
        console.log('[Shortcut] Registered Ctrl+Shift+C');
    } else {
        console.warn('[Shortcut] Failed to register Ctrl+Shift+C');
    }
}

// ─── 7. 选区截图模式 ───
function startScreenshotMode() {
    if (screenshotMode) return;

    console.log('[Screenshot] Starting overlay');

    restoreAfterOverlay = !!(mainWindow && !mainWindow.isDestroyed() && mainWindow.isVisible());
    if (restoreAfterOverlay) {
        mainWindow.hide();
    }

    const display = screen.getPrimaryDisplay();
    const { x, y, width: screenW, height: screenH } = display.bounds;
    const scaleFactor = display.scaleFactor;

    const overlayWin = new BrowserWindow({
        width: screenW,
        height: screenH,
        x,
        y,
        frame: false,
        transparent: true,
        alwaysOnTop: true,
        resizable: false,
        skipTaskbar: true,
        focusable: true,
        hasShadow: false,
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true,
            preload: path.join(__dirname, 'preload.js')
        }
    });

    overlayWin.setAlwaysOnTop(true, 'screen-saver');
    overlayWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });

    // 给 overlay 发送 scaleFactor
    overlayWin.webContents.on('did-finish-load', () => {
        console.log('[Screenshot] Overlay loaded');
        overlayWin.webContents.send('screenshot-mode', { scaleFactor, screenW, screenH });
        overlayWin.focus();
    });

    overlayWin.loadFile(path.join(__dirname, 'server', 'static', 'screenshot-overlay.html'));

    overlayWin.on('closed', () => {
        console.log('[Screenshot] Overlay closed');
        screenshotMode = false;
        if (restoreAfterOverlay) {
            restoreAfterOverlay = false;
            restoreMainWindow();
        }
    });

    screenshotMode = true;
}

// ─── 8. IPC 处理 ───
function setupIPC() {
    ipcMain.handle('get-screenshot', async () => {
        console.log('[Screenshot] Capturing screen');
        const { desktopCapturer } = require('electron');
        const display = screen.getPrimaryDisplay();
        const scaleFactor = display.scaleFactor;
        const sources = await desktopCapturer.getSources({
            types: ['screen'],
            thumbnailSize: {
                width: Math.round(display.bounds.width * scaleFactor),
                height: Math.round(display.bounds.height * scaleFactor)
            }
        });
        const source = sources[0];
        return source ? { dataUrl: source.thumbnail.toDataURL(), scaleFactor } : null;
    });

    ipcMain.on('mini-click', () => {
        restoreMainWindow();
    });

    ipcMain.on('start-screenshot', () => {
        console.log('[Screenshot] Requested from renderer');
        startScreenshotMode();
    });

    ipcMain.on('screenshot-captured', async (event, dataUrl) => {
        console.log('[Screenshot] Selection captured');
        // 通过 webContents 发送到主窗口处理
        if (mainWindow && !mainWindow.isDestroyed()) {
            restoreAfterOverlay = false;
            restoreMainWindow();
            mainWindow.webContents.send('screenshot-captured', dataUrl);
        }
    });

    // 自定义 close → 最小化为浮动图标
    ipcMain.on('close-to-mini', () => {
        hideMainToMini();
    });
}

// ─── 9. App 入口 ───

// 设置 Windows AppUserModelId (必须在启动时调用)
if (process.platform === 'win32') {
    app.setAppUserModelId('com.cliplearn.desktop');
}

// 获取命令行参数
const isAutoStart = process.argv.includes('--autostart');

app.whenReady().then(async () => {
    // 强制深色 Windows 原生标题栏
    nativeTheme.themeSource = 'dark';

    // 打印路径信息（便于排查安装后路径问题）
    console.log('[Main] exe路径:', process.execPath);
    console.log('[Main] resourcesPath:', process.resourcesPath);
    console.log('[Main] __dirname:', __dirname);
    console.log('[Main] isPackaged:', app.isPackaged);

    startFlask();

    try {
        await waitForFlask();
        console.log('[Main] Flask 后端已就绪');
    } catch (err) {
        // 后端启动失败：弹系统对话框提醒用户
        const { dialog } = require('electron');
        dialog.showErrorBox(
            'ClipLearn 启动失败',
            '后端服务未能启动，请重新安装。\n\n' +
            '可能原因：\n' +
            '1. 安装目录权限不足\n' +
            '2. 端口 5000 被占用\n' +
            '3. 杀毒软件拦截了 ClipLearn-server.exe\n\n' +
            '详情: ' + err.message
        );
        console.error('[Main]', err.message);
        app.quit();
        return;
    }

    createMiniWindow();
    createTray();
    setupIPC();
    registerGlobalShortcuts();

    if (isAutoStart) {
        // 开机自启模式：显示浮动 CL 徽标，不弹出主窗口
        console.log('[Main] 自启动模式 — 仅显示 CL 浮动图标');
        miniWindow.show();
    } else {
        createMainWindow();
    }
});

// ─── 10. 退出清理 ───
app.on('window-all-closed', () => {
    // 不退出，保持托盘运行
});

app.on('before-quit', () => {
    app.isQuitting = true;
    globalShortcut.unregisterAll();

    if (flaskProcess) {
        flaskProcess.kill();
        flaskProcess = null;
    }
});

app.on('activate', () => {
    restoreMainWindow();
});
