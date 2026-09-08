// Persist the user's chosen location and observations supplied by the actual desktop.
import { getConfig, setConfig } from './config'
import { locationPriority, toLocationContext, type LocationInfo } from '../shared/location'
export type { LocationInfo } from '../shared/location'

let cached: LocationInfo | null = null

function normalizeCity(raw: string): string {
  return (raw || '')
    .replace(/^中国/, '')
    .replace(/(省|特别行政区)$/, '')
    .replace(/(市|自治州|地区)$/, '')
    .trim()
}

// Device/IP observations come from the actual desktop JS SDK. A remote server must not infer the user's location from its own exit IP.
export async function detectLocation(_force = false): Promise<LocationInfo> { return getLocation() }

export function getLocation(): LocationInfo {
  const config = getConfig()
  // Saved device/IP coordinates are a configured fallback until observed again during this launch.
  return cached || { city: config.city, coords: config.coords, source: ['manual', 'address'].includes(config.location.source) ? config.location.source : 'config', detail_source: config.location.source, district: config.location.district, coordinate_system: 'GCJ02', granularity: config.coords ? config.location.granularity : 'city', ...(config.location.observed_at ? { observed_at: config.location.observed_at } : {}) }
}

export function setReportedLocation(location: LocationInfo, userInitiated = false): LocationInfo {
  if (!userInitiated && locationPriority(getLocation().source) > locationPriority(location.source)) return getLocation()
  const next: LocationInfo = { ...location, city: normalizeCity(location.city || getConfig().city), coordinate_system: 'GCJ02', detail_source: location.source, granularity: location.granularity || (location.coords ? ['gps', 'amap-gps'].includes(location.source) ? 'point' : 'unknown' : 'city') }
  if (!next.coords || !['gps', 'amap-gps'].includes(next.source)) delete next.accuracy
  toLocationContext(next)
  setConfig({ city: next.city, coords: next.coords || '', location: { source: next.source, accuracy: next.accuracy || 0, district: next.district || '', granularity: next.granularity!, observed_at: next.observed_at || '' } })
  cached = next
  return cached
}

export function setManualCity(city: string): LocationInfo { return setReportedLocation({ city, source: 'manual' }, true) }
