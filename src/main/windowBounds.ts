import { app, screen, type BrowserWindow, type Rectangle } from 'electron'
import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const MIN_WIDTH = 1280
const MIN_HEIGHT = 840

export function largestWorkArea(): Rectangle {
  return screen.getAllDisplays().map(display => display.workArea).reduce((best, area) => (
    area.width * area.height > best.width * best.height ? area : best
  ), screen.getPrimaryDisplay().workArea)
}

export function defaultWindowBounds(): Rectangle {
  const work = largestWorkArea()
  const width = Math.max(Math.min(MIN_WIDTH, work.width), Math.round(work.width * 0.75))
  const height = Math.max(Math.min(MIN_HEIGHT, work.height), Math.round(work.height * 0.75))
  return {
    x: work.x + Math.round((work.width - width) / 2),
    y: work.y + Math.round((work.height - height) / 2),
    width,
    height
  }
}

function boundsPath(): string {
  return join(app.getPath('userData'), 'window-bounds.json')
}

function clampToWorkArea(raw: Rectangle): Rectangle {
  const displays = screen.getAllDisplays()
  const work = displays.map(display => display.workArea).find(area => (
    raw.x + raw.width > area.x && raw.x < area.x + area.width
    && raw.y + raw.height > area.y && raw.y < area.y + area.height
  )) || largestWorkArea()
  const width = Math.min(Math.max(Math.round(raw.width), Math.min(MIN_WIDTH, work.width)), work.width)
  const height = Math.min(Math.max(Math.round(raw.height), Math.min(MIN_HEIGHT, work.height)), work.height)
  return {
    x: Math.min(Math.max(Math.round(raw.x), work.x), work.x + work.width - width),
    y: Math.min(Math.max(Math.round(raw.y), work.y), work.y + work.height - height),
    width,
    height
  }
}

export function loadWindowState(): { bounds: Rectangle; maximized: boolean } {
  try {
    const raw = JSON.parse(readFileSync(boundsPath(), 'utf8')) as Partial<Rectangle> & { maximized?: boolean }
    if (![raw.x, raw.y, raw.width, raw.height].every(value => Number.isFinite(value))) {
      return { bounds: defaultWindowBounds(), maximized: false }
    }
    return {
      bounds: clampToWorkArea({ x: Number(raw.x), y: Number(raw.y), width: Number(raw.width), height: Number(raw.height) }),
      maximized: raw.maximized === true
    }
  } catch {
    return { bounds: defaultWindowBounds(), maximized: false }
  }
}

export function persistWindowBounds(win: BrowserWindow): void {
  if (win.isDestroyed() || win.isMinimized()) return
  const bounds = win.isMaximized() ? win.getNormalBounds() : win.getBounds()
  writeFileSync(boundsPath(), JSON.stringify({ ...bounds, maximized: win.isMaximized() }), { mode: 0o600 })
}

export function attachWindowBoundsPersistence(win: BrowserWindow): void {
  let timer: ReturnType<typeof setTimeout> | undefined
  const save = (): void => {
    clearTimeout(timer)
    timer = setTimeout(() => persistWindowBounds(win), 400)
  }
  win.on('resize', save)
  win.on('move', save)
  win.on('maximize', save)
  win.on('unmaximize', save)
  win.on('close', () => persistWindowBounds(win))
}
