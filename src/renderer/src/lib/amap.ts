// 动态加载高德 JS SDK（客户端定位 + 地图 + 路网折线）。仿 weplan，但用官方 AMapLoader，
// 它会在"地图渲染 provider 完全就绪"后才 resolve —— 解决 Electron 里裸 <script> onload 后
// 立刻 new AMap.Map 抛「No maps provider」的时序问题。
import AMapLoader from '@amap/amap-jsapi-loader'
import type { LocationInfo } from '@shared/location'

let loading: Promise<any> | null = null

const PLUGINS = ['AMap.Scale', 'AMap.ToolBar', 'AMap.Driving', 'AMap.Walking', 'AMap.Transfer', 'AMap.Geolocation', 'AMap.CitySearch']

export function loadAMap(): Promise<any> {
  if (window.AMap) return Promise.resolve(window.AMap)
  if (loading) return loading
  loading = (async () => {
    const cfg = await window.plango.getAmapJsConfig()
    if (!cfg?.jsKey) throw new Error('未配置高德 JS Key')
    if (cfg.jsSecurity) window._AMapSecurityConfig = { securityJsCode: cfg.jsSecurity }
    const AMap = await AMapLoader.load({ key: cfg.jsKey, version: '2.0', plugins: PLUGINS })
    return AMap
  })()
  loading.catch(() => (loading = null)) // 失败允许重试
  return loading
}

export type DetectedLoc = LocationInfo

const normalize = (c: string): string => (c || '').replace(/^中国/, '').replace(/(省|市|自治区|特别行政区)$/, '').trim()

// 浏览器原生 GPS（触发 macOS 定位授权），WGS84 → 高德 GCJ02，再逆地理拿城市/区。最精确。
function viaBrowserGPS(AMap: any): Promise<DetectedLoc | null> {
  return new Promise((res) => {
    if (!navigator.geolocation) return res(null)
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { longitude, latitude, accuracy } = pos.coords
        try {
          AMap.convertFrom([longitude, latitude], 'gps', (status: string, result: any) => {
            const lnglat = status === 'complete' && result?.locations?.[0] ? result.locations[0] : null
            if (!lnglat) return res(null)
            const lng = lnglat.lng
            const lat = lnglat.lat
            const coords = `${lng},${lat}`
            res({ city: '', coords, source: 'gps', accuracy, coordinate_system: 'GCJ02', granularity: 'point', observed_at: new Date(pos.timestamp).toISOString() })
          })
        } catch {
          res(null)
        }
      },
      () => res(null),
      { enableHighAccuracy: true, timeout: 10000, maximumAge: 60000 }
    )
  })
}

// 高德 Geolocation（H5 GPS + 内部转换，返回 GCJ02 + 精度 + 地址）
function viaAmapGeo(AMap: any): Promise<DetectedLoc | null> {
  return new Promise((res) => {
    try {
      // noIpLocate:1 —— 只认真实 H5 GPS，禁止 SDK 内部降级到 IP（否则会返回城市级坐标却标成 GPS，污染圆心）
      const geo = new AMap.Geolocation({ timeout: 10000, enableHighAccuracy: true, GeoLocationFirst: true, noIpLocate: 1 })
      geo.getCurrentPosition((status: string, result: any) => {
        if (status === 'complete' && result?.position) {
          const comp = result.addressComponent || {}
          res({
            city: normalize(comp.city || comp.province || ''),
            district: comp.district || '',
            coords: `${result.position.lng},${result.position.lat}`,
            source: 'amap-gps',
            accuracy: typeof result.accuracy === 'number' && Number.isFinite(result.accuracy) ? result.accuracy : undefined,
            coordinate_system: 'GCJ02', granularity: 'point', observed_at: new Date().toISOString()
          })
        } else res(null)
      })
    } catch {
      res(null)
    }
  })
}

// CitySearch（IP 定城市，城市级坐标兜底）
function viaCitySearch(AMap: any): Promise<DetectedLoc | null> {
  return new Promise((res) => {
    try {
      const cs = new AMap.CitySearch()
      cs.getLocalCity((status: string, result: any) => {
        if (status === 'complete' && result?.city) {
          res({ city: normalize(result.city), source: 'amap-city', coordinate_system: 'GCJ02', granularity: 'city', observed_at: new Date().toISOString() })
        } else res(null)
      })
    } catch {
      res(null)
    }
  })
}

// 打开即定位：优先原生/高德 GPS（精确到街道），拿不到再用 CitySearch（城市级）。
export async function detectViaAMap(): Promise<DetectedLoc | null> {
  let AMap: any
  try {
    AMap = await loadAMap()
  } catch {
    return null
  }
  const [gps, amapGeo, city] = await Promise.all([viaBrowserGPS(AMap), viaAmapGeo(AMap), viaCitySearch(AMap)])
  // 在有坐标的 GPS 结果里取精度最高（accuracy 最小）的；都没有则用城市级
  const gpsCands = [gps, amapGeo].filter((x): x is DetectedLoc => !!x?.coords)
  if (gpsCands.length) {
    gpsCands.sort((a, b) => (a.accuracy ?? 9999) - (b.accuracy ?? 9999))
    const best = gpsCands[0]
    // Reverse geocoding belongs to the backend; retain the device's own measured accuracy/time.
    try {
      const [longitude, latitude] = best.coords!.split(',').map(Number)
      const result = await window.plango.geo.reverse({ longitude, latitude })
      return { ...best, city: normalize(result.location.city), district: result.location.district }
    } catch { return best.city ? best : city }
  }
  return city
}

// User address parsing uses the same backend Geo service as planning and discovery.
export async function geocodeAddress(address: string, city?: string): Promise<(DetectedLoc & { formattedAddress: string }) | null> {
  const result = await window.plango.geo.geocode({ address, ...(city ? { city } : {}) })
  return { city: normalize(result.location.city), district: result.location.district, formattedAddress: result.location.address,
    coords: `${result.location.longitude},${result.location.latitude}`, source: 'address', coordinate_system: 'GCJ02',
    granularity: result.location.granularity, observed_at: result.observed_at }
}
