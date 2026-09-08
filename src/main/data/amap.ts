// All Web API reads use the authenticated backend; this adapter preserves the existing rich POI cards.
import type { HarnessClient } from '../harnessClient'
import { fromAmap, type AmapPoi } from './converter'
import { toLocationContext, type GeoLocationResult, type LocationInfo } from '../../shared/location'

export interface GeoSearchResult {
  source: 'amap'
  scope: 'around' | 'city'
  pois: AmapPoi[]
  observed_at: string
  expires_at: string
  cache_hit: boolean
  source_ref: string
}
export function requireFresh(value: { observed_at: string; expires_at: string }): void {
  if (!Number.isFinite(Date.parse(value.observed_at)) || !Number.isFinite(Date.parse(value.expires_at)) || Date.parse(value.expires_at) <= Date.now()) throw new Error('地理结果已过期，请刷新后重试。')
}
export async function searchPoi(client: Pick<HarnessClient, 'request'>, keywords: string, location: LocationInfo, opts: { types?: string; page?: number; refresh?: boolean } = {}) {
  const result = await client.request<GeoSearchResult>('/api/v1/geo/search', 'POST', {
    query: keywords, location_context: toLocationContext(location), radius_m: 5000, page: opts.page ?? 1, limit: 20, refresh: opts.refresh ?? false,
    ...(opts.types ? { types: opts.types } : {})
  })
  requireFresh(result)
  return { ...result, items: result.pois.map(poi => ({ ...fromAmap(poi), observed_at: result.observed_at })) }
}
export async function geocode(client: Pick<HarnessClient, 'request'>, address: string, city?: string): Promise<GeoLocationResult> {
  const result = await client.request<GeoLocationResult>('/api/v1/geo/geocode', 'POST', { address, ...(city ? { city } : {}) })
  requireFresh(result)
  return result
}
export async function reverse(client: Pick<HarnessClient, 'request'>, longitude: number, latitude: number): Promise<GeoLocationResult> {
  const result = await client.request<GeoLocationResult>('/api/v1/geo/reverse', 'POST', { longitude, latitude })
  requireFresh(result)
  return result
}
