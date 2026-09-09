import { app, shell, screen, BrowserWindow, session as electronSession } from 'electron'
import { join } from 'path'
import { pathToFileURL } from 'node:url'
import { registerIpc } from './ipc'
import { detectLocation } from './location'
import { startShareServer, stopShareServer } from './share/server'
import { harnessStatus, stopHarness } from './harness'
import { getHarnessEnvironment } from './config'
import { prepareDesktopStorage } from './storageMigration'
import { allowsGeolocation, sameRendererDocument } from './permissions'
import { initializeBrowserViews, isOwnedBrowserContents } from './browserView'
import { browserUrl } from '../shared/browser'

app.commandLine.appendSwitch('remote-debugging-address', '127.0.0.1')
app.commandLine.appendSwitch('remote-debugging-port', '0')

try {
  const desktopData = prepareDesktopStorage(app.getPath('appData'), app.getPath('userData'))
  app.setPath('userData', desktopData)
  app.setPath('sessionData', desktopData)
  app.setName('PlanGo')
  app.setAppUserModelId('plango')
} catch (error) {
  console.error('[plango] Desktop initialization failed:', (error as Error).message)
  app.exit(1)
}

let mainWindow: BrowserWindow | null = null
const rendererUrl = process.env.ELECTRON_RENDERER_URL || pathToFileURL(join(__dirname, '../renderer/index.html')).href

export function isTrustedRendererUrl(url: string): boolean { return sameRendererDocument(url, rendererUrl) }

function createWindow(): void {
  const { width, height } = screen.getPrimaryDisplay().workAreaSize
  mainWindow = new BrowserWindow({
    width: Math.min(1800, width),
    height: Math.min(1120, height),
    minWidth: Math.min(1180, width),
    minHeight: Math.min(740, height),
    show: false,
    autoHideMenuBar: true,
    title: 'PlanGo · AI 本地生活浏览器',
    backgroundColor: '#faf9f7',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: false
    }
  })

  mainWindow.on('ready-to-show', () => mainWindow?.show())
  initializeBrowserViews(mainWindow)

  // 外链走系统浏览器
  mainWindow.webContents.setWindowOpenHandler((details) => {
    if (mainWindow && isTrustedRendererUrl(mainWindow.webContents.getURL()) && /^https?:\/\//i.test(details.url)) {
      try { void shell.openExternal(browserUrl(details.url)) } catch { /* Invalid external URL remains blocked. */ }
    }
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-navigate', (event, url) => { if (!isTrustedRendererUrl(url)) event.preventDefault() })
  mainWindow.webContents.on('will-redirect', (event, url) => { if (!isTrustedRendererUrl(url)) event.preventDefault() })
  mainWindow.webContents.on('will-attach-webview', event => event.preventDefault())

  if (process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// Apply permissions to the actual persistent browser partition, not just the host window.
function hardenSession(): void {
  for (const session of [electronSession.defaultSession, electronSession.fromPartition('persist:plango')]) {
    session.setPermissionRequestHandler((wc, permission, cb, details) => {
      cb(allowsGeolocation({ permission, isMainFrame: details.isMainFrame, requestingUrl: details.requestingUrl,
        pageUrl: wc.getURL(), hostUrl: rendererUrl, isHost: wc === mainWindow?.webContents,
        isBrowser: session === electronSession.fromPartition('persist:plango') && wc.session === session && isOwnedBrowserContents(wc.id) }))
    })
    session.setPermissionCheckHandler((wc, permission, origin, details) => allowsGeolocation({ permission, isMainFrame: details.isMainFrame,
      requestingUrl: details.requestingUrl || origin, pageUrl: wc?.getURL() || '', hostUrl: rendererUrl,
      isHost: !!wc && wc === mainWindow?.webContents, isBrowser: !!wc && session === electronSession.fromPartition('persist:plango') && wc.session === session && isOwnedBrowserContents(wc.id) }))
  }
}

app.whenReady().then(() => {
  hardenSession()
  registerIpc()
  createWindow()

  // 自动定位：确定当前城市（跑在用户机器上时走真实 IP）→ 写回配置 → 通知渲染层
  detectLocation()
    .then((loc) => {
      const win = getMainWindow()
      if (win && !win.isDestroyed()) win.webContents.send('location:update', loc)
    })
    .catch(() => {})

  // 局域网分享协作服务（同 WiFi 手机扫码看方案/投票/留评语）
  Promise.resolve().then(() => startShareServer(8799, getHarnessEnvironment().PLANGO_DATA_DIR || join(app.getPath('userData'), 'harness'))).then((p) => console.log(p ? `[share] 局域网分享服务已启动 :${p}` : '[share] 启动失败')).catch(() => {})

  // The renderer reports configuration errors; startup never seeds fabricated user history.
  void harnessStatus()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
}).catch((error) => { console.error('[plango] Desktop startup failed:', (error as Error).message); app.exit(1) })

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => { stopShareServer(); stopHarness() })

export function getMainWindow(): BrowserWindow | null {
  return mainWindow
}
