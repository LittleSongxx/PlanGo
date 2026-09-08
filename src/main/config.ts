// 配置层：读取 .env（轻量自解析，无需 dotenv 依赖）+ 运行时覆盖。
// key 只从环境/本地 .env 读取，绝不硬编码进仓库源码。
import { readFileSync, existsSync, writeFileSync, renameSync } from 'fs'
import { join } from 'path'
// 用默认导入以兼容 headless（tsx）：electron 非运行时下 module.exports 是字符串路径，app 取到 undefined。
import electron from 'electron'
import { migrateConfigFile } from './storageMigration'
const app = (electron as unknown as { app?: { getPath: (n: string) => string } })?.app

export interface AppConfig {
  llm: { apiKey: string; baseURL: string; model: string }
  amap: { key: string; jsKey: string; jsSecurity: string }
  city: string
  coords: string // 用户当前坐标 "lng,lat"（GCJ02），作为周边搜索圆心/行程起点
}

export function parseEnv(text: string): Record<string, string> {
  const out: Record<string, string> = {}
  for (const line of text.split(/\r?\n/)) {
    if (!line.trim() || line.trimStart().startsWith('#')) continue
    const assignment = /^[ \t]*(?:export[ \t]+)?([A-Za-z_][A-Za-z0-9_]*)[ \t]*=(.*)$/.exec(line)
    if (!assignment) throw new Error('Unsupported configuration line; existing configuration was preserved')
    const key = assignment[1]
    let value = assignment[2].trim()
    if (value.startsWith('"') || value.startsWith("'")) {
      const single = value.startsWith("'")
      const quoted = (single ? /^'((?:\\.|[^'])*)'[ \t]*(?:#.*)?$/ : /^"((?:\\.|[^"])*)"[ \t]*(?:#.*)?$/).exec(value)
      if (!quoted) throw new Error('Malformed quoted configuration value; existing configuration was preserved')
      const escapes: Record<string, string> = single ? { '\\': '\\', "'": "'" } : {
        '\\': '\\', '"': '"', "'": "'", a: '\x07', b: '\b', f: '\f', n: '\n', r: '\r', t: '\t', v: '\v'
      }
      value = quoted[1].replace(/\\(.)/g, (sequence, escaped: string) => escapes[escaped] ?? sequence)
    } else value = value.replace(/[ \t]+#.*$/, '').trimEnd()
    if (Object.hasOwn(out, key) && out[key] !== value) throw new Error(`Conflicting configuration values for ${key}; existing configuration was preserved`)
    out[key] = value
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
    if (existsSync(p)) return parseEnv(readFileSync(p, 'utf-8'))
  }
  return {}
}

// Main-process only. Never expose this object through IPC: it may contain credentials.
function currentEnvironment(): Record<string, string | undefined> {
  const values = { ...loadEnvFile(), ...process.env }
  const legacy = Object.keys(values).filter(key => /^(?:YOYU_|XIAONIAN_)/.test(key) || ['LLM_PROVIDER', 'DATA_SOURCE'].includes(key))
  if (legacy.length) throw new Error(`Legacy configuration keys require one-time migration: ${legacy.join(', ')}`)
  return values
}

export function getHarnessEnvironment(): Record<string, string> {
  const values = currentEnvironment()
  return Object.fromEntries(Object.entries(values).filter(([key, value]) => key.startsWith('PLANGO_') && typeof value === 'string')) as Record<string, string>
}

// 运行时覆盖（设置页写入），落 userData/plango-config.json
function overridePath(): string {
  if (app) return migrateConfigFile(app.getPath('userData'))
  return migrateConfigFile(projectRoot(), '.')
}

function loadOverride(): Partial<AppConfig> {
  const p = overridePath()
  if (existsSync(p)) {
    try {
      return JSON.parse(readFileSync(p, 'utf-8'))
    } catch { throw new Error('PlanGo settings could not be read; existing configuration was preserved') }
  }
  return {}
}

export function getConfig(): AppConfig {
  if (cache) return cache
  const env = currentEnvironment()
  // LLM provider 可切换：longcat（默认）/ minimax。MiniMax 为 OpenAI 兼容端点。
  const provider = (env.PLANGO_LLM_PROVIDER || 'longcat').toLowerCase()
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
    city: env.PLANGO_CITY || '重庆',
    coords: env.PLANGO_COORDS || ''
  }
  const ov = loadOverride()
  cache = deepMerge(base, ov)
  // 兜底净化：历史 override 里可能残留 city="本地" 等非法值，覆盖回 .env/默认，防止全国乱串
  if (!isValidCity(cache.city)) cache.city = isValidCity(base.city) ? base.city : '重庆'
  return cache
}

export function setConfig(patch: Partial<AppConfig>): AppConfig {
  // 丢弃非法城市写入（占位/兜底），避免污染持久化配置
  if (patch.city !== undefined && !isValidCity(patch.city)) delete patch.city
  const next = deepMerge(getConfig(), patch)
  const file = overridePath()
  writeFileSync(`${file}.tmp`, JSON.stringify(next, null, 2), { mode: 0o600 })
  renameSync(`${file}.tmp`, file)
  cache = next
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
    hasLlmKey: !!c.llm.apiKey,
    hasAmapKey: !!c.amap.key
  }
}

function deepMerge<T>(a: T, b: Partial<T>): T {
  const out = { ...a } as Record<string, unknown>
  for (const k of Object.keys(b || {})) {
    if (!Object.hasOwn(out, k)) continue // Drop obsolete persisted settings and unknown keys.
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
