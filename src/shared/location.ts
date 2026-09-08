export const locationSources = ['config', 'manual', 'address', 'gps', 'amap-gps', 'ip', 'amap-ip', 'amap-city', 'pconline', 'ip-api'] as const
export type LocationSource = typeof locationSources[number]
export interface LocationInfo {
  city: string
  province?: string
  district?: string
  coords?: string // GCJ02; never raw WGS84 coordinates.
  source: LocationSource
  accuracy?: number // Meters only when actually reported by a positioning provider.
}

export function locationPriority(source: string): number {
  return ['manual', 'address'].includes(source) ? 4 : ['gps', 'amap-gps'].includes(source) ? 3 : ['ip', 'amap-ip'].includes(source) ? 2 : source === 'config' ? 0 : 1
}

export function locationLabel(source: string, accuracy = 0): string {
  if (source === 'gps' || source === 'amap-gps') return `设备定位${Number.isFinite(accuracy) && accuracy > 0 ? ` ±${Math.round(accuracy)}m` : '（精度未知）'}`
  return ({ manual: '手动城市', address: '手动地址（精度未知）', config: '配置位置', ip: 'IP区域', 'amap-ip': 'IP区域', 'amap-city': 'IP城市', pconline: 'IP城市', 'ip-api': 'IP城市' } as Record<string, string>)[source] || '来源未知'
}
