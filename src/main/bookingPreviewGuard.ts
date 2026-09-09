import { webContents, type Session, type WebContents } from 'electron'
import { allowedBookingPreviewRequest } from '../shared/bookingPreview'

const sessions = new WeakMap<Session, { armed: boolean; tabs: Map<number, number>; changed: () => void }>()

export function protectBookingPreview(contents: WebContents, raw: string): void {
  const state = sessions.get(contents.session)
  if (!state || state.tabs.has(contents.id)) return
  try { if (!['szuo.com', 'www.szuo.com'].includes(new URL(raw).hostname)) return } catch { return }
  state.armed = true
  state.tabs.set(contents.id, 0)
  contents.once('destroyed', () => state.tabs.delete(contents.id))
  state.changed()
}

export function installBookingPreviewGuard(session: Session, changed: () => void): void {
  if (sessions.has(session)) return
  const state = { armed: false, tabs: new Map<number, number>(), changed }
  sessions.set(session, state)
  session.webRequest.onBeforeRequest((request, callback) => {
    let booking = false
    try { booking = ['szuo.com', 'www.szuo.com'].includes(new URL(request.url).hostname) } catch { /* invalid URLs cannot arm a preview */ }
    const id = request.webContentsId
    if (booking && request.resourceType === 'mainFrame' && id && !state.tabs.has(id)) {
      // Arm before the first document, including malformed parameters and redirects.
      const contents = request.webContents || webContents.fromId(id)
      if (contents) protectBookingPreview(contents, request.url)
    }
    const protectedTab = !!id && state.tabs.has(id)
    // Detached requests/workers can outlive a page. Keep this fail-closed until
    // the session exits; do not release protection merely because a tab closed.
    const unattributed = state.armed && (!id || !webContents.fromId(id))
    const cancel = (protectedTab || unattributed) && !allowedBookingPreviewRequest(request.url, request.method, request.resourceType)
    if (cancel && protectedTab) state.tabs.set(id, (state.tabs.get(id) || 0) + 1)
    callback({ cancel })
  })
}

export function bookingPreviewProtection(contents: WebContents): { enabled: boolean; blocked_requests: number } {
  const count = sessions.get(contents.session)?.tabs.get(contents.id)
  return { enabled: count !== undefined, blocked_requests: count || 0 }
}
