// 城市中心坐标（GCJ02），照抄 weplan：无 GPS / IP 未返回坐标时作为"出发点兜底"，保证路线永远能画。
// 诚实标注：用兜底时提示"从XX市中心估算"，定位后更准。
const CITY_CENTER: Record<string, string> = {
  深圳: '114.085947,22.547',
  广州: '113.264385,23.129163',
  上海: '121.473701,31.230416',
  北京: '116.407526,39.90403',
  杭州: '120.155070,30.274085',
  成都: '104.066541,30.572269',
  重庆: '106.551556,29.563009',
  武汉: '114.305392,30.593098',
  南京: '118.796877,32.060255',
  西安: '108.940174,34.341568',
  苏州: '120.585316,31.298886',
  天津: '117.190182,39.125596',
  长沙: '112.938814,28.228209',
  郑州: '113.625368,34.746599',
  东莞: '113.751765,23.020536',
  青岛: '120.382639,36.067082',
  沈阳: '123.431474,41.805698',
  宁波: '121.549792,29.868388',
  昆明: '102.832891,24.880095',
  福州: '119.296494,26.074508',
  厦门: '118.089425,24.479834',
  合肥: '117.227239,31.820587'
}

const DEFAULT_CENTER = CITY_CENTER['深圳']

const normalizeCity = (c?: string): string => (c || '').replace(/^中国/, '').replace(/(省|市|自治区|特别行政区)$/, '').trim()

// 城市 → 中心坐标（GCJ02 "lng,lat"）。找不到用默认（深圳）。
export function cityCenter(city?: string): string {
  const key = normalizeCity(city)
  return CITY_CENTER[key] || DEFAULT_CENTER
}

// 出发点兜底：优先真实定位坐标，否则回退当前城市中心。永远返回合法 "lng,lat"（照抄 weplan originFallback）。
export function originFallback(coords?: string, city?: string): string {
  if (coords && coords.includes(',')) return coords
  return cityCenter(city)
}
