// This preview permits document/static reads only, never the site's cart or availability API.
function publicHttps(raw: string): URL | undefined {
  try {
    const url = new URL(raw)
    if (url.protocol === 'https:' && !url.username && !url.password && !url.port && !url.hash) return url
  } catch { /* Invalid URLs are blocked. */ }
}

export function isBookingPreviewUrl(raw: string): boolean {
  const url = publicHttps(raw)
  if (!url || url.hostname !== 'www.szuo.com' || !/^\/en\/(?:shops\/)?niccolo-chongqing-tealounge\/reserve(?:\/(?:message|landing))?$/.test(url.pathname)) return false
  if (!url.search) return true
  const params = url.searchParams
  if (params.size !== 3 || ['pax', 'start_date', 'start_time'].some(key => params.getAll(key).length !== 1)) return false
  const date = params.get('start_date')!
  if (!/^(?:[1-9]|1[0-2])$/.test(params.get('pax')!) || !/^(?:[01]\d|2[0-3]):[0-5]\d$/.test(params.get('start_time')!)) return false
  if (!/^[1-9]\d{3}-\d{2}-\d{2}$/.test(date)) return false
  const parsed = new Date(date + 'T00:00:00Z')
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === date
}

const imagePath = /\.(?:avif|gif|jpe?g|png|svg|webp)$/i
const fontPath = /^\/common\/fonts\/[\w./-]+\.(?:woff2?|ttf|otf)$/i
const themePath = /^\/booking_themes\/[a-f\d]{24}\/(?:canvas_images|banner_images)\/xl\/[\w.-]+\.(?:jpe?g|png|webp)$/i

export function allowedBookingPreviewRequest(raw: string, method: string, resourceType: string): boolean {
  if (method !== 'GET' && method !== 'HEAD') return false
  if (resourceType === 'mainFrame') return isBookingPreviewUrl(raw)
  if (!['script', 'stylesheet', 'font', 'image'].includes(resourceType)) return false
  const url = publicHttps(raw)
  if (!url) return false
  const path = url.pathname
  // Extensions alone are insufficient: an API can be disguised as a script or image request.
  if (url.hostname === 'booking-cdn.szuo.com') {
    return !url.search && ((resourceType === 'script' && /^\/china\/static\/booking\/js\/[\w.-]+\.js$/.test(path)) ||
      (resourceType === 'stylesheet' && /^\/china\/static\/booking\/css\/[\w.-]+\.css$/.test(path)))
  }
  if (/\/(?:api|booking|cart|payment)(?:\/|\.|$)/i.test(path)) return false
  if (/^cdn[0-3]\.szuo\.com$/.test(url.hostname)) {
    if (resourceType === 'stylesheet') return !url.search && /^\/common\/css\/[\w.-]+\.css$/.test(path)
    if (resourceType === 'font') return !url.search && fontPath.test(path)
    return resourceType === 'image' && (!url.search || /^\?\d{10}$/.test(url.search)) &&
      (themePath.test(path) || /^\/common\/images\/[\w./-]+$/.test(path) && imagePath.test(path))
  }
  if (url.hostname === 'cdn0.tablecheck.com') return resourceType === 'font' && !url.search && fontPath.test(path)
  if (resourceType !== 'image' || !/^[1-3]\.image\.cdn\.szuo\.com$/.test(url.hostname) || (url.search && !/^\?\d{10}$/.test(url.search))) return false
  const source = path.match(/^\/unsafe\/fit-in\/\d{1,4}x\d{1,4}\/filters:format\((?:webp|jpeg|png)\)\/https:\/\/cdn[0-3]\.szuo\.com(\/booking_themes\/.+)$/)
  return !!source && themePath.test(source[1])
}
