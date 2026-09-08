// 自动定位：确定用户当前所在城市（跑在用户机器上时，出站走用户真实 IP）。
// 优先级：高德 IP 定位 → pconline 归属地 → ip-api → 配置默认城市。用户可在设置页手动覆盖。
import { getConfig, setConfig } from './config'

export interface LocationInfo {
  city: string
  province?: string
  district?: string
  source: 'amap-ip' | 'pconline' | 'ip-api' | 'config' | 'manual'
  coords?: string // "lng,lat" GCJ02
}

let cached: LocationInfo | null = null

function normalizeCity(raw: string): string {
  return (raw || '')
    .replace(/^中国/, '')
    .replace(/(省|特别行政区)$/, '')
    .replace(/(市|自治州|地区)$/, '')
    .trim()
}

async function fetchJson(url: string, timeoutMs = 6000): Promise<any | null> {
  const c = new AbortController()
  const t = setTimeout(() => c.abort(), timeoutMs)
  try {
    const r = await fetch(url, { signal: c.signal })
    if (!r.ok) return null
    return await r.json()
  } catch {
    return null
  } finally {
    clearTimeout(t)
  }
}

// 高德 IP 定位（真实 China IP 下可用）
async function viaAmap(): Promise<LocationInfo | null> {
  const key = getConfig().amap.key
  if (!key) return null
  const d = await fetchJson(`https://restapi.amap.com/v3/ip?key=${key}`)
  if (!d || d.status !== '1') return null
  const city = Array.isArray(d.city) ? '' : d.city
  const province = Array.isArray(d.province) ? '' : d.province
  if (!city && !province) return null
  // rectangle "lng1,lat1;lng2,lat2" 取中心作坐标
  let coords: string | undefined
  const rect = Array.isArray(d.rectangle) ? '' : String(d.rectangle || '')
  if (rect.includes(';')) {
    const [p1, p2] = rect.split(';').map((s) => s.split(',').map(Number))
    if (p1?.length === 2 && p2?.length === 2) coords = `${((p1[0] + p2[0]) / 2).toFixed(6)},${((p1[1] + p2[1]) / 2).toFixed(6)}`
  }
  // 逆地理补区县名（让徽标能显示"南山区"而不是只有城市）
  let district: string | undefined
  if (coords) district = await regeoDistrict(coords, key)
  return { city: normalizeCity(city || province), province, district, source: 'amap-ip', coords }
}

// 逆地理：坐标 → 区县名
async function regeoDistrict(coords: string, key: string): Promise<string | undefined> {
  const d = await fetchJson(`https://restapi.amap.com/v3/geocode/regeo?key=${key}&location=${coords}&extensions=base`)
  const comp = d?.regeocode?.addressComponent
  const district = comp?.district
  return Array.isArray(district) || !district ? undefined : String(district)
}

// pconline 归属地（国内友好，GBK；用 fetch 拿文本后手动解析）
async function viaPconline(): Promise<LocationInfo | null> {
  try {
    const c = new AbortController()
    const t = setTimeout(() => c.abort(), 6000)
    const r = await fetch('https://whois.pconline.com.cn/ipJson.jsp?json=true', { signal: c.signal })
    clearTimeout(t)
    if (!r.ok) return null
    const buf = Buffer.from(await r.arrayBuffer())
    // pconline 返回 GBK；Node 无内置 GBK 解码，改用其纯文本字段（城市名多为可辨识 UTF-8 子集失败时跳过）
    let text = buf.toString('utf-8')
    const cityMatch = /"city"\s*:\s*"([^"]*)"/.exec(text)
    const proMatch = /"pro"\s*:\s*"([^"]*)"/.exec(text)
    const city = cityMatch?.[1] || ''
    const pro = proMatch?.[1] || ''
    // 若出现乱码（GBK 未正确解码），放弃
    if (/\ufffd/.test(city + pro) || (!city && !pro)) return null
    return { city: normalizeCity(city || pro), province: pro, source: 'pconline' }
  } catch {
    return null
  }
}

// ip-api（英文/多语言，海外也可用；作为最后的网络兜底）
async function viaIpApi(): Promise<LocationInfo | null> {
  const d = await fetchJson('https://ip-api.com/json/?lang=zh-CN&fields=status,country,regionName,city')
  if (!d || d.status !== 'success' || !d.city) return null
  return { city: normalizeCity(d.city), province: d.regionName, source: 'ip-api' }
}

export async function detectLocation(force = false): Promise<LocationInfo> {
  if (cached && !force) return cached
  const chain = [viaAmap, viaPconline, viaIpApi]
  for (const fn of chain) {
    try {
      const r = await fn()
      if (r && r.city) {
        cached = r
        // 写回配置，让规划默认用这个城市（坐标作圆心）
        setConfig(r.coords ? { city: r.city, coords: r.coords } : { city: r.city })
        return r
      }
    } catch {
      /* next */
    }
  }
  cached = { city: getConfig().city, source: 'config' }
  return cached
}

export function getLocation(): LocationInfo {
  return cached || { city: getConfig().city, source: 'config' }
}

export function setManualCity(city: string): LocationInfo {
  cached = { city: normalizeCity(city), source: 'manual' }
  setConfig({ city: cached.city, coords: '' })
  return cached
}
