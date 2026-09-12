import type { RoutePathSegment } from '@shared/types'

const ROUTE_MODES = new Set(['walking', 'transit', 'driving'])

export const ROUTE_STROKE: Record<RoutePathSegment['mode'], { color: string; dashed?: boolean; weight: number }> = {
  walking: { color: '#64748b', dashed: true, weight: 4 },
  transit: { color: '#1d4ed8', weight: 6 },
  driving: { color: '#8a5a00', weight: 5 },
}

export function asRoutePaths(value: unknown): RoutePathSegment[] {
  if (!Array.isArray(value)) return []
  const segments: RoutePathSegment[] = []
  for (const item of value) {
    if (!item || typeof item !== 'object') continue
    const row = item as { mode?: unknown; name?: unknown; path?: unknown }
    const mode = typeof row.mode === 'string' ? row.mode : ''
    if (!ROUTE_MODES.has(mode) || !Array.isArray(row.path)) continue
    const path = row.path
      .filter((point): point is [number, number] => Array.isArray(point) && point.length >= 2 && Number.isFinite(Number(point[0])) && Number.isFinite(Number(point[1])))
      .map((point) => [Number(point[0]), Number(point[1])] as [number, number])
    if (path.length >= 2) segments.push({ mode: mode as RoutePathSegment['mode'], name: typeof row.name === 'string' && row.name ? row.name : undefined, path })
  }
  return segments
}

export function addRouteOverlays(AMap: any, map: any, segments: RoutePathSegment[]): any[] {
  return segments.map((segment) => {
    const style = ROUTE_STROKE[segment.mode]
    return new AMap.Polyline({
      path: segment.path,
      map,
      strokeColor: style.color,
      strokeStyle: style.dashed ? 'dashed' : 'solid',
      strokeDasharray: style.dashed ? [8, 6] : undefined,
      strokeWeight: style.weight,
      strokeOpacity: 0.92,
      lineJoin: 'round',
      lineCap: 'round',
      zIndex: segment.mode === 'transit' ? 80 : 70,
    })
  })
}

export function pathMidpoint(segments: RoutePathSegment[]): [number, number] | null {
  const points = segments.flatMap((segment) => segment.path)
  if (points.length < 2) return null
  return points[Math.floor(points.length / 2)]
}
