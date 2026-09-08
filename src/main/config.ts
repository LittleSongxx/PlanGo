// 配置层：读取 .env（轻量自解析，无需 dotenv 依赖）+ 运行时覆盖。
// key 只从环境/本地 .env 读取，绝不硬编码进仓库源码。
import { readFileSync, existsSync, writeFileSync } from 'fs'
import { join } from 'path'
// 用默认导入以兼容 headless（tsx）：electron 非运行时下 module.exports 是字符串路径，app 取到 undefined。
import electron from 'electron'
const app = (electron as unknown as { app?: { getPath: (n: string) => string } })?.app

export interface AppConfig {
  llm: { apiKey: string; baseURL: string; model: string }
  amap: { key: string; jsKey: string; jsSecurity: string }
  dataSource: 'mock' | 'amap' | 'enterprise'
  city: string
  coords: string // 用户当前坐标 "lng,lat"（GCJ02），作为周边搜索圆心/行程起点
  enterprise: { base: string; key: string }
}

function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split(/\r?\n/)) {
    const t = line.trim()
    if (!t || t.startsWith('#')) continue
    const i = t.indexOf('=')
    if (i < 0) continue
    out[t.slice(0, i).trim()] = t.slice(i + 1).trim()
  }
  return out
}

let cache: AppConfig | null = null

// 无效城市名（占位/兜底/未定位），绝不能写进配置或用于搜索，否则高德 region 非法 → 全国乱串
const INVALID_CITY = new Set(['', '本地', '中国', '定位中…', '定位中', '未知'])
export function isValidCity(c?: string): boolean {
  return !!c && !INVALID_CITY.has(c.trim())
}

function projectRoot(): string {
  // dev: cwd 为项目根；打包后落到 userData
  return process.cwd()
}

function loadEnvFile(): Record<string, string> {
  const candidates = [join(projectRoot(), '.env'), join(projectRoot(), '.env.example')]
  for (const p of candidates) {
    if (existsSync(p)) {
      try {
        return parseEnv(readFileSync(p, 'utf-8'))
      } catch {
        /* ignore */
      }
    }
  }
  return {}
}

// Main-process only. Never expose this object through IPC: it may contain credentials.
export function getHarnessEnvironment(): Record<string, string> {
  const values = { ...loadEnvFile(), ...process.env }
  return Object.fromEntries(Object.entries(values).filter(([key, value]) => key.startsWith('YOYU_') && typeof value === 'string')) as Record<string, string>
}

// 运行时覆盖（设置页写入），落 userData/xiaonian-config.json
function overridePath(): string {
  try {
    if (app) return join(app.getPath('userData'), 'xiaonian-config.json')
  } catch {
    /* ignore */
  }
  return join(projectRoot(), '.xiaonian-config.json')
}

function loadOverride(): Partial<AppConfig> {
  const p = overridePath()
  if (existsSync(p)) {
    try {
      return JSON.parse(readFileSync(p, 'utf-8'))
    } catch {
      /* ignore */
    }
  }
  return {}
}

export function getConfig(): AppConfig {
  if (cache) return cache
  const env = { ...loadEnvFile(), ...process.env } as Record<string, string>
  // LLM provider 可切换：longcat（默认）/ minimax。MiniMax 为 OpenAI 兼容端点。
  const provider = (env.LLM_PROVIDER || 'longcat').toLowerCase()
  const longcat = {
    apiKey: env.LONGCAT_API_KEY || '',
    baseURL: env.LONGCAT_BASE_URL || 'https://api.longcat.chat/openai/v1',
    model: env.LONGCAT_MODEL || 'LongCat-2.0'
  }
  const minimax = {
    apiKey: env.MINIMAX_API_KEY || '',
    baseURL: env.MINIMAX_BASE_URL || 'https://api.minimaxi.com/v1',
    model: env.MINIMAX_MODEL || 'MiniMax-M2.7'
  }
  // 选定 provider；若选定的没配 key 而另一个有，则自动回退到有 key 的那个
  let llm = provider === 'minimax' ? minimax : longcat
  if (!llm.apiKey) llm = provider === 'minimax' ? longcat : minimax.apiKey ? minimax : llm
  if (env.OPENAI_API_KEY || provider === 'openai') {
    llm = { apiKey: env.OPENAI_API_KEY || '', baseURL: env.OPENAI_BASE_URL || 'https://api.openai.com/v1', model: env.OPENAI_MODEL || '' }
  }
  const base: AppConfig = {
    llm,
    amap: {
      key: env.AMAP_WEBSERVICE_KEY || '',
      jsKey: env.AMAP_JS_KEY || '',
      jsSecurity: env.AMAP_JS_SECURITY || ''
    },
    dataSource: (env.DATA_SOURCE as AppConfig['dataSource']) || 'amap',
    city: env.XIAONIAN_CITY || '上海',
    coords: env.XIAONIAN_COORDS || '',
    enterprise: { base: env.ENTERPRISE_API_BASE || '', key: env.ENTERPRISE_API_KEY || '' }
  }
  const ov = loadOverride()
  cache = deepMerge(base, ov)
  // 兜底净化：历史 override 里可能残留 city="本地" 等非法值，覆盖回 .env/默认，防止全国乱串
  if (!isValidCity(cache.city)) cache.city = isValidCity(base.city) ? base.city : '上海'
  return cache
}

export function setConfig(patch: Partial<AppConfig>): AppConfig {
  // 丢弃非法城市写入（占位/兜底），避免污染持久化配置
  if (patch.city !== undefined && !isValidCity(patch.city)) delete patch.city
  const next = deepMerge(getConfig(), patch)
  cache = next
  try {
    writeFileSync(overridePath(), JSON.stringify(next, null, 2), 'utf-8')
  } catch {
    /* ignore */
  }
  return next
}

// 设置页脱敏
export function getConfigMasked(): AppConfig & { hasLlmKey: boolean; hasAmapKey: boolean } {
  const c = getConfig()
  const mask = (s: string) => (s ? s.slice(0, 5) + '••••' + s.slice(-3) : '')
  return {
    ...c,
    llm: { ...c.llm, apiKey: mask(c.llm.apiKey) },
    amap: { ...c.amap, key: mask(c.amap.key), jsKey: mask(c.amap.jsKey), jsSecurity: mask(c.amap.jsSecurity) },
    enterprise: { ...c.enterprise, key: mask(c.enterprise.key) },
    hasLlmKey: !!c.llm.apiKey,
    hasAmapKey: !!c.amap.key
  }
}

function deepMerge<T>(a: T, b: Partial<T>): T {
  const out = { ...a } as Record<string, unknown>
  for (const k of Object.keys(b || {})) {
    const bv = (b as Record<string, unknown>)[k]
    const av = out[k]
    if (bv && typeof bv === 'object' && !Array.isArray(bv) && av && typeof av === 'object') {
      out[k] = deepMerge(av, bv as Record<string, unknown>)
    } else if (bv !== undefined) {
      out[k] = bv
    }
  }
  return out as T
}
