import { BrowserWindow, WebContentsView, session, type WebContents, type Rectangle } from 'electron'
import { randomUUID } from 'node:crypto'
import { browserUrl, allowedBrowserSite } from '../shared/browser'
import { IPC } from '../shared/ipc'
import type { BrowserActivity, BrowserIntent, BrowserLayout, BrowserViewState } from '../shared/browserView'
import { bookingPreviewProtection, installBookingPreviewGuard, protectBookingPreview } from './bookingPreviewGuard'

type Tab = { id: string; contents: WebContents; view?: WebContentsView; popup?: BrowserWindow; url: string; visible: boolean; visibility: AbortController; closing?: boolean; error?: string }
const tabs = new Map<string, Tab>()
const loads = new WeakMap<WebContents, AbortController>()
let host: BrowserWindow | undefined
let activeId: string | null = null
let layout: BrowserLayout = { x: 0, y: 0, width: 0, height: 0, visible: false }
let seq = 0
let popupHandler: ((parentId: string, childId: string) => void) | undefined

export function onBrowserPopup(handler: (parentId: string, childId: string) => void): void { popupHandler = handler }

export async function loadBrowserURL(contents: WebContents, raw: string, signal?: AbortSignal, navigate: (url: string) => Promise<unknown> = url => contents.loadURL(url)): Promise<void> {
  const url = browserUrl(raw)
  if (contents.isDestroyed()) throw new Error('tab_closed')
  protectBookingPreview(contents, url)
  if (signal?.aborted) throw new Error('navigation_cancelled')
  loads.get(contents)?.abort()
  const controller = new AbortController()
  loads.set(contents, controller)
  const bounded = AbortSignal.any([controller.signal, AbortSignal.timeout(15000), ...(signal ? [signal] : [])])
  await new Promise<void>((resolve, reject) => {
    const cleanup = (): void => { bounded.removeEventListener('abort', abort); if (loads.get(contents) === controller) loads.delete(contents) }
    const abort = (): void => {
      if (loads.get(contents) === controller && !contents.isDestroyed()) contents.stop()
      cleanup()
      reject(new Error(signal?.aborted ? 'navigation_cancelled' : controller.signal.aborted ? 'navigation_superseded' : 'navigation_timeout'))
    }
    bounded.addEventListener('abort', abort, { once: true })
    try { void navigate(url).then(() => { cleanup(); resolve() }, error => { cleanup(); reject(error) }) }
    catch (error) { cleanup(); reject(error) }
  })
}

export function getBrowserState(): BrowserViewState {
  return { seq, activeTabId: activeId, tabs: [...tabs.values()].filter(tab => !tab.contents.isDestroyed()).map(tab => ({
    id: tab.id, url: tab.url, title: tab.contents.getTitle() || '新标签', loading: tab.contents.isLoading(),
    canGoBack: tab.contents.navigationHistory.canGoBack(), canGoForward: tab.contents.navigationHistory.canGoForward(),
    zoom: tab.contents.getZoomFactor(), popup: !!tab.popup, error: tab.error, previewProtected: bookingPreviewProtection(tab.contents).enabled
  })) }
}
function publish(): void {
  seq++
  if (host && !host.isDestroyed() && !host.webContents.isDestroyed()) host.webContents.send(IPC.browserState, getBrowserState())
}
export function notifyBrowserActivity(value: BrowserActivity): void {
  if (host && !host.isDestroyed()) host.webContents.send(IPC.browserActivity, value)
}
function viewBounds(): Rectangle {
  if (!host || host.isDestroyed()) return { x: 0, y: 0, width: 0, height: 0 }
  const content = host.getContentBounds(), zoom = host.webContents.getZoomFactor()
  const x = Math.max(0, Math.min(content.width, Math.round(layout.x * zoom)))
  const y = Math.max(0, Math.min(content.height, Math.round(layout.y * zoom)))
  return { x, y, width: Math.max(0, Math.min(content.width - x, Math.round(layout.width * zoom))), height: Math.max(0, Math.min(content.height - y, Math.round(layout.height * zoom))) }
}
function setTabVisibility(tab: Tab, visible: boolean, reason = 'browser_not_visible'): void {
  if (visible && !tab.visible) tab.visibility = new AbortController()
  tab.visible = visible
  if (!visible) tab.visibility.abort(new Error(reason))
}
function applyLayout(): void {
  const bounds = viewBounds()
  for (const tab of tabs.values()) {
    const visible = layout.visible && tab.id === activeId && !tab.error && !tab.closing && bounds.width > 0 && bounds.height > 0 && !!host?.isVisible() && !host.isMinimized() && !tab.popup?.isMinimized()
    setTabVisibility(tab, visible)
    if (tab.view) { tab.view.setBounds(bounds); tab.view.setVisible(visible) }
    if (tab.popup && !tab.popup.isDestroyed()) {
      if (visible && !tab.popup.isVisible()) tab.popup.showInactive()
      else if (!visible && tab.popup.isVisible()) tab.popup.hide()
    }
  }
}
export function setBrowserLayout(value: BrowserLayout): void { layout = value; applyLayout() }
export function getBrowserTab(id: string): WebContents | undefined {
  const tab = tabs.get(id)
  return tab && !tab.closing && !tab.contents.isDestroyed() ? tab.contents : undefined
}
export function getBrowserTabSignal(id: string): AbortSignal { return tabs.get(id)?.visibility.signal || AbortSignal.abort(new Error('tab_closed')) }
export function getActiveBrowserTab(): { id: string; contents: WebContents } | undefined {
  const contents = activeId ? getBrowserTab(activeId) : undefined
  return contents && activeId ? { id: activeId, contents } : undefined
}
export function isOwnedBrowserContents(id: number): boolean { return [...tabs.values()].some(tab => tab.contents.id === id && !tab.contents.isDestroyed()) }
export function isBrowserTabVisible(id: string): boolean {
  const tab = tabs.get(id)
  return !!tab && !tab.contents.isDestroyed() && !tab.error && layout.visible && activeId === id && !!host?.isVisible() && !host.isMinimized()
    && tab.visible && (tab.popup ? tab.popup.isVisible() && !tab.popup.isMinimized() : true)
}
export function getBrowserTabBounds(id: string): Rectangle | undefined {
  const tab = tabs.get(id)
  return tab?.popup && !tab.popup.isDestroyed() ? { ...tab.popup.getContentBounds(), x: 0, y: 0 } : tab?.view?.getBounds()
}
export function activateBrowserTab(id: string): void {
  const tab = tabs.get(id)
  if (!tab || tab.contents.isDestroyed()) throw new Error('浏览器标签已关闭')
  activeId = id
  applyLayout()
  if (isBrowserTabVisible(id)) { tab.popup?.focus(); tab.contents.focus() }
  publish()
}
function attach(contents: WebContents, view?: WebContentsView, popup?: BrowserWindow): Tab {
  const visibility = new AbortController()
  visibility.abort(new Error('browser_not_visible'))
  const tab: Tab = { id: 'tab_' + randomUUID(), contents, view, popup, url: contents.getURL(), visible: false, visibility }
  tabs.set(tab.id, tab)
  if (popup) {
    popup.on('hide', () => setTabVisibility(tab, false))
    popup.on('minimize', () => setTabVisibility(tab, false))
    popup.on('restore', applyLayout)
    popup.on('show', () => { if (layout.visible) activeId = tab.id; applyLayout() })
    popup.on('focus', () => { if (activeId !== tab.id) { activeId = tab.id; applyLayout(); publish() } })
    popup.on('close', () => { tab.closing = true; setTabVisibility(tab, false, 'tab_closed') })
  }
  const update = (): void => { if (!contents.isDestroyed()) { tab.url = contents.getURL() || tab.url; publish() } }
  contents.on('will-prevent-unload', () => { tab.closing = false; applyLayout(); publish() })
  contents.on('did-start-loading', () => { update(); applyLayout() })
  contents.on('did-finish-load', () => { tab.error = undefined; update(); applyLayout() })
  contents.on('did-stop-loading', update)
  contents.on('did-navigate', update)
  contents.on('did-navigate-in-page', update)
  contents.on('page-title-updated', update)
  contents.on('did-fail-load', (_event, code, description, _url, mainFrame) => {
    if (!mainFrame || code === -3) return
    tab.error = `页面加载失败：${description}。可修改网址或重新加载。`; applyLayout(); publish()
  })
  contents.on('render-process-gone', () => { tab.error = '浏览器页面已崩溃，请重新加载；未确认的提交不会重试。'; setTabVisibility(tab, false, 'browser_crashed'); applyLayout(); publish() })
  contents.once('destroyed', () => {
    setTabVisibility(tab, false, 'tab_closed')
    tabs.delete(tab.id)
    if (view && host && !host.isDestroyed()) { try { host.contentView.removeChildView(view) } catch {} }
    if (activeId === tab.id) activeId = [...tabs.keys()].at(-1) || null
    applyLayout(); publish()
  })
  contents.on('will-navigate', (event, url) => { try { browserUrl(url) } catch { event.preventDefault() } })
  contents.on('will-redirect', (event, url) => { try { browserUrl(url) } catch { event.preventDefault() } })
  contents.setWindowOpenHandler(({ url }) => {
    if (!allowedBrowserSite(url)) return { action: 'deny' }
    // Returning a custom WCV from createWindow stalls this Electron/CDP build. Native popups retain their opener and session.
    return { action: 'allow', overrideBrowserWindowOptions: { show: false, title: 'PlanGo · 浏览器', autoHideMenuBar: true,
      webPreferences: { session: contents.session, sandbox: true, contextIsolation: true, nodeIntegration: false, webviewTag: false } } }
  })
  contents.on('did-create-window', child => {
    const next = attach(child.webContents, undefined, child)
    popupHandler?.(tab.id, next.id)
    activateBrowserTab(next.id)
  })
  return tab
}
export async function createBrowserTab(raw: string, signal?: AbortSignal): Promise<{ id: string; contents: WebContents }> {
  if (!host || host.isDestroyed()) throw new Error('桌面窗口尚未就绪')
  const url = browserUrl(raw)
  const view = new WebContentsView({ webPreferences: { session: session.fromPartition('persist:plango'), sandbox: true, contextIsolation: true, nodeIntegration: false, webviewTag: false } })
  host.contentView.addChildView(view)
  const tab = attach(view.webContents, view)
  tab.url = url
  activateBrowserTab(tab.id)
  try { await loadBrowserURL(tab.contents, url, signal) } catch (error) {
    if ((error as Error).message !== 'navigation_superseded') { tab.error ||= '页面加载已停止，可修改网址或重新加载。'; applyLayout(); publish() }
    throw error
  }
  return { id: tab.id, contents: tab.contents }
}
export async function handleBrowserIntent(intent: BrowserIntent): Promise<BrowserViewState> {
  if (intent.kind === 'state') return getBrowserState()
  if (intent.kind === 'create') { await createBrowserTab(intent.url); return getBrowserState() }
  const tab = tabs.get(intent.id)
  if (!tab || tab.contents.isDestroyed()) throw new Error('浏览器标签已关闭')
  switch (intent.kind) {
    case 'activate': activateBrowserTab(tab.id); break
    case 'focus': if (isBrowserTabVisible(tab.id)) { tab.popup?.focus(); tab.contents.focus() } break
    case 'close': tab.closing = true; setTabVisibility(tab, false, 'tab_closed'); tab.popup ? tab.popup.close() : tab.contents.close(); break
    case 'zoom': tab.contents.setZoomFactor(intent.factor); break
    case 'back': if (tab.contents.navigationHistory.canGoBack()) tab.contents.navigationHistory.goBack(); break
    case 'forward': if (tab.contents.navigationHistory.canGoForward()) tab.contents.navigationHistory.goForward(); break
    case 'reload': tab.error = undefined; tab.contents.reload(); break
    case 'navigate':
      tab.url = browserUrl(intent.url); tab.error = undefined
      try { await loadBrowserURL(tab.contents, tab.url) } catch (error) { if ((error as Error).message !== 'navigation_superseded') tab.error ||= '页面加载已停止，可修改网址或重新加载。' }
      break
  }
  applyLayout(); publish(); return getBrowserState()
}
export function initializeBrowserViews(window: BrowserWindow): void {
  host = window
  installBookingPreviewGuard(session.fromPartition('persist:plango'), publish)
  window.on('resize', applyLayout)
  window.on('show', applyLayout)
  window.on('hide', applyLayout)
  window.on('minimize', applyLayout)
  window.on('restore', applyLayout)
  window.on('close', () => { for (const tab of tabs.values()) { tab.closing = true; setTabVisibility(tab, false, 'tab_closed') } })
  window.webContents.on('did-finish-load', publish)
  window.once('closed', () => {
    host = undefined
    for (const tab of [...tabs.values()]) {
      setTabVisibility(tab, false, 'tab_closed')
      if (!tab.contents.isDestroyed()) tab.popup ? tab.popup.destroy() : tab.contents.close({ waitForBeforeUnload: false })
    }
    tabs.clear(); activeId = null; layout.visible = false
  })
}
