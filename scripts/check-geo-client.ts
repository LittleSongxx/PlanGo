import assert from 'node:assert/strict'
import { createServer } from 'node:http'
import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { HarnessClient } from '../src/main/harnessClient'
import { searchPoi, geocode, reverse } from '../src/main/data/amap'
import { computeLiveDiscover } from '../src/main/discover'
import { toLocationContext } from '../src/shared/location'

const work = mkdtempSync(join(tmpdir(), 'plango-geo-client-'))
const stamp = new Date(Date.now() - 20_000).toISOString()
const requests: { path: string; body: any }[] = []
let expired = false
const server = createServer(async (request, response) => {
  assert.equal(request.headers.authorization, 'Bearer fixture-geo-token')
  let raw = ''; for await (const part of request) raw += part
  const body = raw ? JSON.parse(raw) : {}
  const path = request.url || ''
  requests.push({ path, body })
  response.setHeader('content-type', 'application/json')
  const finish = (value: unknown) => response.end(JSON.stringify(value))
  const time = { observed_at: stamp, expires_at: new Date(Date.now() + (expired ? -1000 : 40_000)).toISOString() }
  if (path === '/api/v1/geo/search') return finish({ ...time, source: 'amap', cache_hit: true, source_ref: 'fixture-provider',
    scope: body.location_context.longitude === undefined || ['city', 'district', 'unknown'].includes(body.location_context.granularity) ? 'city' : 'around',
    pois: [{ id: 'fixture-poi', name: '受控地点', location: '106.57,29.56', cityname: body.location_context.city, address: '受控地址', type: '餐饮服务;餐厅', business: { rating: '4.5', cost: '80', tel: 'fixture-contact' }, photos: [{ url: 'https://fixture.invalid/poi.png' }] }] })
  if (path === '/api/v1/geo/geocode' || path === '/api/v1/geo/reverse') return finish({ ...time, source: 'amap', location: { longitude: body.longitude ?? 106.57, latitude: body.latitude ?? 29.56, address: '受控地址', city: '重庆市', district: '渝中区', granularity: path.endsWith('geocode') ? 'city' : 'address' } })
  if (path === '/api/v1/health/ready') return finish({ ready: true, input_delivery_version: 1 })
  if (path === '/api/v1/runs' && request.method === 'POST') return finish({ run_id: 'fixture-run', request_id: body.request_id, request_fingerprint: body.request_fingerprint, accepted: true })
  if (path.endsWith('/events?after=0')) return finish({ events: [] })
  if (path === '/api/v1/runs/fixture-run') return finish({ run_id: 'fixture-run', phase: 'SUCCEEDED', state: {}, event_seq: 0 })
  response.statusCode = 404; finish({ detail: 'unexpected fixture path' })
})
await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
const port = (server.address() as { port: number }).port
const origin = toLocationContext({ city: '成都', source: 'manual', granularity: 'city' })
const client = new HarnessClient({ baseURL: `http://127.0.0.1:${port}`, token: 'fixture-geo-token', browserSessionId: 'fixture-browser', dataDir: work, location: () => origin, emit() {}, execute: async () => { throw new Error('No browser execution is allowed in a Geo fixture') } })
try {
  const location = { city: '重庆', coords: '106.57,29.56', source: 'address' as const, granularity: 'address' as const, observed_at: stamp }
  const result = await computeLiveDiscover(client, location)
  assert.equal(result.scope, 'around')
  assert.equal(result.observed_at, stamp, 'Cache hits retain the original observation time')
  assert.equal(result.groups[0].items[0].image, 'https://fixture.invalid/poi.png')
  assert.equal(result.groups[0].items[0].source, 'amap')
  assert.equal(result.groups[0].items[0].price_per_person, 80)
  assert(requests.slice(0, 3).every(({ body }) => body.location_context.longitude === 106.57 && body.location_context.coordinate_system === 'GCJ02' && body.location_context.detail_source === 'address'))
  await searchPoi(client, '美食', { ...location, coords: '106.53,29.58' }, { refresh: true })
  assert.equal(requests.at(-1)!.body.location_context.longitude, 106.53)
  assert.equal(requests.at(-1)!.body.refresh, true)
  const city = await computeLiveDiscover(client, { city: '成都', source: 'manual', granularity: 'city' })
  assert.equal(city.scope, 'city')
  assert(city.groups[0].label.startsWith('同城'))
  assert.equal(requests.at(-1)!.body.location_context.longitude, undefined, 'A different city cannot borrow previous coordinates')
  expired = true
  await assert.rejects(() => searchPoi(client, '美食', location), /过期/)
  expired = false
  Object.defineProperty(globalThis, 'window', { value: { plango: { geo: { geocode: (request: { address: string; city?: string }) => geocode(client, request.address, request.city), reverse: (request: { longitude: number; latitude: number }) => reverse(client, request.longitude, request.latitude) } } } })
  const { geocodeAddress } = await import('../src/renderer/src/lib/amap')
  const address = await geocodeAddress('重庆市', '重庆')
  assert.equal(address?.granularity, 'city', 'Geocoding a city center must not turn it into an exact address')
  assert.equal(address?.accuracy, undefined)
  assert.equal(address?.observed_at, stamp)
  assert.equal(requests.at(-1)!.path, '/api/v1/geo/geocode')
  const selected = { poi_id: 'fixture-poi', name: '受控地点', address: '受控地址', longitude: 106.57, latitude: 29.56, source: 'amap' as const }
  await client.createRun('围绕所选地点规划', undefined, selected)
  const created = requests.find(request => request.path === '/api/v1/runs')!.body
  assert.deepEqual(created.selected_poi, selected)
  assert.deepEqual(created.location_context, origin, 'A selected destination must not replace the user origin')
  assert(!Object.hasOwn(created.selected_poi, 'observed_at'), 'Missing source time must not be fabricated')
  console.log('Geo client fixture passed: authenticated backend-only reads, rich POIs, precise/city scopes, coordinates/refresh, TTL, geocoding granularity and selected destination provenance')
} finally { await client.close(); server.close(); rmSync(work, { recursive: true, force: true }) }
