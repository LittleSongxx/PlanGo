import { app, shell, BrowserWindow, session as electronSession } from 'electron'
import { join } from 'path'
import { registerIpc } from './ipc'
import { loadMockDb } from './data/mockdb'
import { seedIfEmpty } from './brain/memory'
import { proactive } from './proactive'
import { getWeChatBridge } from './im/bridge'
import { runAgent } from './agent'
import { detectLocation } from './location'
import { startShareServer, stopShareServer } from './share/server'

let mainWindow: BrowserWindow | null = null

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
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  if (process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// 给 <webview> 内的美团/点评一个真实持久指纹（复用登录态）
function hardenSession(): void {
  const ua = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36'
  electronSession.defaultSession.setUserAgent(ua)
  // 允许定位权限（高德 JS Geolocation WiFi 定位需要；这样开梯子也能按真实位置定位）。
  // 桌面工作台场景下统一放行，避免 webview 登录/定位被弹窗阻断。
  electronSession.defaultSession.setPermissionRequestHandler((_wc, _permission, cb) => cb(true))
  try {
    electronSession.defaultSession.setPermissionCheckHandler(() => true)
  } catch {
    /* older electron */
  }
}

app.whenReady().then(() => {
  try {
    loadMockDb()
  } catch (e) {
    console.error('[mockdb] 装载失败：', e)
  }
  try {
    // 首启播种"已用一两个月"的记忆画像（仅空记忆时），让越懂你/主动关心立刻可演示
    if (seedIfEmpty()) console.log('[memory] 已播种演示画像')
  } catch (e) {
    console.error('[memory] 播种失败：', e)
  }
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
  startShareServer().then((p) => console.log(p ? `[share] 局域网分享服务已启动 :${p}` : '[share] 启动失败')).catch(() => {})

  // 主动关心引擎（opt-in，默认开，可在设置关）
  proactive.start()

  // 微信入站消息 → 直接交给小悠处理（现场用"模拟入站"触发）
  getWeChatBridge().onMessage(async (m) => {
    try {
      await runAgent(m.text, [])
    } catch {
      /* ignore */
    }
  })

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('before-quit', () => stopShareServer())

export function getMainWindow(): BrowserWindow | null {
  return mainWindow
}
