import { app, shell, BrowserWindow, session as electronSession } from 'electron'
import { join } from 'path'
import { registerIpc } from './ipc'
import { detectLocation } from './location'
import { startShareServer, stopShareServer } from './share/server'
import { harnessStatus, stopHarness } from './harness'
import { allowedBrowserSite } from '../shared/browser'
import { getHarnessEnvironment } from './config'

let mainWindow: BrowserWindow | null = null
const browserContents = new Set<number>()

export function ownsBrowserContents(id: number): boolean { return browserContents.has(id) }

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1500,
    height: 940,
    minWidth: 1180,
    minHeight: 740,
    show: false,
    autoHideMenuBar: true,
    title: '小悠 · AI 本地生活浏览器',
    backgroundColor: '#faf9f7',
    titleBarStyle: process.platform === 'darwin' ? 'hiddenInset' : 'default',
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: true,
      contextIsolation: true,
      nodeIntegration: false,
      webviewTag: true // 内置浏览器视图（美团/点评）复用登录态
    }
  })

  mainWindow.on('ready-to-show', () => mainWindow?.show())

  // 外链走系统浏览器
  mainWindow.webContents.setWindowOpenHandler((details) => {
    if (/^https?:\/\//i.test(details.url)) void shell.openExternal(details.url)
    return { action: 'deny' }
  })
  mainWindow.webContents.on('will-attach-webview', (event, preferences, params) => {
    if (!/^https?:\/\//i.test(params.src || '') || params.partition !== 'persist:xiaonian') {
      event.preventDefault()
      return
    }
    delete preferences.preload
    preferences.nodeIntegration = false
    preferences.contextIsolation = true
    preferences.sandbox = true
    preferences.webSecurity = true
  })
  mainWindow.webContents.on('did-attach-webview', (_event, contents) => {
    browserContents.add(contents.id)
    contents.once('destroyed', () => browserContents.delete(contents.id))
  })

  if (process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// Apply permissions to the actual persistent browser partition, not just the host window.
function hardenSession(): void {
  for (const session of [electronSession.defaultSession, electronSession.fromPartition('persist:xiaonian')]) {
    session.setPermissionRequestHandler((wc, permission, cb, details) => {
      const origin = details.requestingUrl || wc.getURL()
      cb(permission === 'geolocation' && allowedBrowserSite(origin))
    })
    session.setPermissionCheckHandler((_wc, permission, origin) => permission === 'geolocation' && allowedBrowserSite(origin))
  }
  app.on('web-contents-created', (_event, contents) => {
    if (contents.getType() !== 'webview') return
    contents.on('will-navigate', (event, url) => { if (!/^https?:\/\//i.test(url)) event.preventDefault() })
    contents.setWindowOpenHandler(({ url }) => {
      if (!allowedBrowserSite(url)) return { action: 'deny' }
      return { action: 'allow', overrideBrowserWindowOptions: { webPreferences: { partition: 'persist:xiaonian', sandbox: true, contextIsolation: true, nodeIntegration: false } } }
    })
  })
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
  Promise.resolve().then(() => startShareServer(8799, getHarnessEnvironment().YOYU_DATA_DIR || join(app.getPath('userData'), 'harness'))).then((p) => console.log(p ? `[share] 局域网分享服务已启动 :${p}` : '[share] 启动失败')).catch(() => {})

  // The renderer reports configuration errors; startup never seeds fabricated user history.
  void harnessStatus()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => { stopShareServer(); stopHarness() })

export function getMainWindow(): BrowserWindow | null {
  return mainWindow
}
