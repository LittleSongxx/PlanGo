export const locationSources = ['config', 'manual', 'address', 'gps', 'amap-gps', 'ip', 'amap-ip', 'amap-city', 'pconline', 'ip-api'] as const
export type LocationSource = typeof locationSources[number]
export const locationGranularities = ['point', 'address', 'district', 'city', 'unknown'] as const
export type LocationGranularity = typeof locationGranularities[number]
export interface LocationInfo {
  city: string
  province?: string
  district?: string
  coords?: string // GCJ02; never raw WGS84 coordinates.
  source: LocationSource
  detail_source?: LocationSource
  accuracy?: number // Meters only when actually reported by a positioning provider.
  coordinate_system?: 'GCJ02'
  granularity?: LocationGranularity
  observed_at?: string
}

export interface LocationContext {
  city: string
  longitude?: number
  latitude?: number
  source: 'config' | 'manual' | 'device'
  coordinate_system: 'GCJ02'
  detail_source: LocationSource
  accuracy?: number
  granularity: LocationGranularity
  observed_at?: string
}
export interface GeoLocationResult {
  source: 'amap'
  location: { longitude: number; latitude: number; address: string; city: string; district: string; granularity: Exclude<LocationGranularity, 'point'> }
  observed_at: string
  expires_at: string
}
export interface SelectedPoi {
  poi_id: string
  name: string
  address: string
  longitude: number
  latitude: number
  source: 'amap'
  observed_at?: string
}
export function toLocationContext(location: LocationInfo): LocationContext {
  if (location.coordinate_system && location.coordinate_system !== 'GCJ02') throw new Error('仅接受已转换的 GCJ02 坐标')
  if (location.coords && !/^-?\d+(?:\.\d+)?,-?\d+(?:\.\d+)?$/.test(location.coords)) throw new Error('位置坐标无效')
  const pair = location.coords ? location.coords.split(',').map(Number) : undefined
  if (pair && (pair.length !== 2 || !pair.every(Number.isFinite) || Math.abs(pair[0]) > 180 || Math.abs(pair[1]) > 90)) throw new Error('位置坐标无效')
  const device = location.source === 'gps' || location.source === 'amap-gps'
  const granularity = location.granularity || (pair ? device || location.source === 'config' ? 'point' : 'unknown' : 'city')
  if (!pair && ['point', 'address'].includes(granularity)) throw new Error('精细位置缺少坐标')
  return { city: location.city, ...(pair ? { longitude: pair[0], latitude: pair[1] } : {}),
    source: ['manual', 'address'].includes(location.source) ? 'manual' : location.source === 'config' ? 'config' : 'device',
    coordinate_system: 'GCJ02', detail_source: location.detail_source || location.source, granularity,
    ...(device && location.accuracy !== undefined ? { accuracy: location.accuracy } : {}), ...(location.observed_at ? { observed_at: location.observed_at } : {}) }
}

export function locationPriority(source: string): number {
  return ['manual', 'address'].includes(source) ? 4 : ['gps', 'amap-gps'].includes(source) ? 3 : ['ip', 'amap-ip'].includes(source) ? 2 : source === 'config' ? 0 : 1
}

export function locationLabel(source: string, accuracy = 0, granularity?: LocationGranularity): string {
  if (source === 'gps' || source === 'amap-gps') return `设备定位${Number.isFinite(accuracy) && accuracy > 0 ? ` ±${Math.round(accuracy)}m` : '（精度未知）'}`
  if (source === 'address' && granularity && granularity !== 'address') return granularity === 'city' ? '手动城市' : granularity === 'district' ? '手动地区' : '地址解析（粒度未知）'
  return ({ manual: '手动城市', address: '手动地址（精度未知）', config: '配置位置', ip: 'IP区域', 'amap-ip': 'IP区域', 'amap-city': 'IP城市', pconline: 'IP城市', 'ip-api': 'IP城市' } as Record<string, string>)[source] || '来源未知'
}
