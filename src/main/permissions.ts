import { allowedBrowserSite } from '../shared/browser'

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
