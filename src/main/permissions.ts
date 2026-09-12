import { allowedBrowserSite } from '../shared/browser'

export function sameRendererDocument(actual: string, expected: string): boolean {
  try { const left = new URL(actual), right = new URL(expected); left.hash = right.hash = ''; return left.href === right.href } catch { return false }
}

export function allowsHostClipboard(context: { permission: string; isMainFrame: boolean; isHost: boolean }): boolean {
  return context.isHost && context.isMainFrame && context.permission === 'clipboard-sanitized-write'
}

export function allowsSessionPermission(context: { permission: string; isMainFrame: boolean; requestingUrl: string; pageUrl: string; hostUrl: string; isHost: boolean; isBrowser: boolean }): boolean {
  return allowsGeolocation(context) || allowsHostClipboard(context)
}

export function allowsGeolocation(context: { permission: string; isMainFrame: boolean; requestingUrl: string; pageUrl: string; hostUrl: string; isHost: boolean; isBrowser: boolean }): boolean {
  if (context.permission !== 'geolocation' || !context.isMainFrame) return false
  try {
    const requester = new URL(context.requestingUrl), page = new URL(context.pageUrl)
    if (context.isHost) {
      const host = new URL(context.hostUrl)
      requester.hash = page.hash = host.hash = ''
      return requester.href === host.href && page.href === host.href
    }
    return context.isBrowser && allowedBrowserSite(requester.href) && allowedBrowserSite(page.href) && requester.origin === page.origin
  } catch { return false }
}
