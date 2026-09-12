import { BrowserWindow, dialog } from 'electron'
import { writeFile } from 'node:fs/promises'
import { carryOutFileStem, carryOutFromPlan, formatCarryOutHtml, formatCarryOutIcs } from '@shared/carryOut'
import type { Plan } from '@shared/types'
import { getMainWindow } from './index'

export async function saveCarryOutIcs(plan: Plan): Promise<{ ok: boolean; canceled?: boolean; path?: string; error?: string }> {
  const ics = formatCarryOutIcs(plan)
  if (!ics) return { ok: false, error: '请先补上出行日期，再加入日历。' }
  const target = await choosePath(`${carryOutFileStem(plan)}.ics`, [{ name: 'iCalendar', extensions: ['ics'] }])
  if (!target) return { ok: true, canceled: true }
  await writeFile(target, ics, 'utf8')
  return { ok: true, path: target }
}

export async function saveCarryOutImage(plan: Plan): Promise<{ ok: boolean; canceled?: boolean; path?: string; error?: string }> {
  const png = await renderCarryOutPng(formatCarryOutHtml(carryOutFromPlan(plan)))
  const target = await choosePath(`${carryOutFileStem(plan)}.png`, [{ name: 'PNG 图片', extensions: ['png'] }])
  if (!target) return { ok: true, canceled: true }
  await writeFile(target, png)
  return { ok: true, path: target }
}

async function choosePath(name: string, filters: { name: string; extensions: string[] }[]): Promise<string | undefined> {
  const options = { defaultPath: name, filters }
  const parent = getMainWindow()
  const result = parent ? await dialog.showSaveDialog(parent, options) : await dialog.showSaveDialog(options)
  return result.canceled || !result.filePath ? undefined : result.filePath
}

export async function renderCarryOutPng(html: string): Promise<Buffer> {
  const win = new BrowserWindow({
    show: false,
    width: 720,
    height: 900,
    useContentSize: true,
    backgroundColor: '#f8f7f3',
    webPreferences: { sandbox: true, contextIsolation: true, backgroundThrottling: false }
  })
  try {
    await win.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`)
    const height = Number(await win.webContents.executeJavaScript('Math.ceil(document.documentElement.scrollHeight)'))
    if (Number.isFinite(height) && height > 0) win.setContentSize(720, Math.min(2400, Math.max(400, height)))
    await win.webContents.executeJavaScript('new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
    const png = (await win.webContents.capturePage()).toPNG()
    if (png.byteLength < 80 || png.byteLength > 8_000_000) throw new Error('安排图片生成失败')
    return png
  } finally {
    if (!win.isDestroyed()) win.destroy()
  }
}
