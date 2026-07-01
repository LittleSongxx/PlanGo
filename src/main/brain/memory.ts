// 记忆（三层：Event → PreferenceChunk → Profile）+ RewriteMemory（LLM 合并，越用越懂）。
// 本地明文落 userData（opt-in、不碰健康/情绪自动检测）。移植 yoyu memory + VitaBench-2.0 RewriteMemory 思路。
import { readFileSync, writeFileSync, existsSync } from 'fs'
import { join } from 'path'
import electron from 'electron'
import type { UserProfile, PreferenceChunk } from '@shared/types'

const app = (electron as unknown as { app?: { getPath: (n: string) => string } })?.app
import { chat } from '../llm'

const DEFAULT_PROFILE: UserProfile = {
  user_id: 'me',
  summary: '',
  preferences: [],
  favorite_shops: [],
  avoid_shops: [],
  home_city: '上海'
}

function storePath(): string {
  try {
    if (app) return join(app.getPath('userData'), 'xiaonian-memory.json')
  } catch {
    /* ignore */
  }
  return join(process.cwd(), '.xiaonian-memory.json')
}

let cache: UserProfile | null = null

export function getProfile(): UserProfile {
  if (cache) return cache
  const p = storePath()
  if (existsSync(p)) {
    try {
      cache = { ...DEFAULT_PROFILE, ...JSON.parse(readFileSync(p, 'utf-8')) }
      return cache!
    } catch {
      /* ignore */
    }
  }
  cache = { ...DEFAULT_PROFILE }
  return cache
}

function persist(): void {
  try {
    writeFileSync(storePath(), JSON.stringify(cache, null, 2), 'utf-8')
  } catch {
    /* ignore */
  }
}

// 第 1 层 Event → 第 2 层 PreferenceChunk：加入/加强偏好（相同文本累加证据）
export function addPreference(text: string, polarity: 'positive' | 'negative' = 'positive', source: PreferenceChunk['source'] = 'conversation'): void {
  const prof = getProfile()
  const existing = prof.preferences.find((c) => c.text === text)
  if (existing) {
    existing.evidence_count += 1
    existing.strength = Math.min(1, existing.strength + 0.15)
  } else {
    prof.preferences.push({ text, polarity, strength: 0.6, evidence_count: 1, source })
  }
  persist()
}

export function favoriteShop(name: string): void {
  const prof = getProfile()
  if (!prof.favorite_shops.includes(name)) prof.favorite_shops.push(name)
  persist()
}

// 记忆透明可控：删除单条偏好 / 收藏 / 清空全部（对齐 2026 最佳实践，用户可查看/编辑/删除）
export function deletePreference(text: string): void {
  const prof = getProfile()
  prof.preferences = prof.preferences.filter((c) => c.text !== text)
  persist()
}
export function deleteFavorite(name: string): void {
  const prof = getProfile()
  prof.favorite_shops = prof.favorite_shops.filter((n) => n !== name)
  persist()
}
export function clearMemory(): void {
  cache = { ...DEFAULT_PROFILE, preferences: [], favorite_shops: [], avoid_shops: [] }
  persist()
}

// 主动召回：见面先"想起你"——根据画像生成一句开场（无记忆则返回空，不打扰）。
export function recallGreeting(): string {
  const prof = getProfile()
  const bits: string[] = []
  // 最近一次足迹更有画面感，优先用它
  const lastTrip = prof.footprints?.[0]
  if (lastTrip) bits.push(`上次${lastTrip.scene}去了「${lastTrip.place}」${lastTrip.note ? '，' + lastTrip.note : ''}`)
  else {
    const fav = prof.favorite_shops.slice(-2)
    if (fav.length) bits.push(`上次去了${fav.join('、')}`)
  }
  const strong = prof.preferences.filter((c) => c.strength >= 0.5).slice(0, 1)
  for (const c of strong) bits.push(`${c.polarity === 'negative' ? '你不太喜欢' : '记得你'}${c.text}`)
  if (!bits.length && !prof.summary) return ''
  const days = prof.since ? Math.max(1, Math.round((Date.now() - prof.since) / 86400_000)) : 0
  const lead = bits.length ? bits.join('；') : prof.summary
  const tail = days ? `我们已经一起过了 ${days} 个日子啦～这周还想让我帮你安排点什么吗？` : '这周还想让我帮你安排点什么吗？'
  return `${lead}。${tail}`
}

// 首启播种：模拟"已用一两个月"的画像，让越懂你/主动关心/自动带入偏好一上来就有血有肉。
// 仅在完全空记忆时写入；用户真实使用后不再覆盖。
export function seedIfEmpty(): boolean {
  const prof = getProfile()
  if (prof.preferences.length > 0 || prof.favorite_shops.length > 0) return false
  const now = Date.now()
  cache = {
    user_id: 'me',
    home_city: '深圳',
    since: now - 52 * 86400_000, // ~7 周前开始用
    summary: '深圳南山上班族；老婆在减脂、常带 5 岁女儿；口味偏清淡杭帮/粤菜，爱亲子乐园与 citywalk，讨厌排长队和重油，周末预算人均 100-150。',
    preferences: [
      { text: '老婆减脂，正餐要清淡少油', polarity: 'positive', strength: 0.92, evidence_count: 6, source: 'conversation' },
      { text: '常带 5 岁女儿，要亲子友好', polarity: 'positive', strength: 0.86, evidence_count: 5, source: 'order' },
      { text: '偏爱杭帮菜 / 粤菜', polarity: 'positive', strength: 0.72, evidence_count: 4, source: 'order' },
      { text: '喜欢安静有格调的环境', polarity: 'positive', strength: 0.6, evidence_count: 3, source: 'review' },
      { text: '排队超过 30 分钟就想换', polarity: 'negative', strength: 0.78, evidence_count: 4, source: 'conversation' },
      { text: '不吃太辣、重油', polarity: 'negative', strength: 0.7, evidence_count: 3, source: 'order' }
    ],
    favorite_shops: ['奈尔宝家庭中心(深圳湾店)', '绿茶餐厅(海岸城店)', '木桑森林·轻食'],
    avoid_shops: ['某排队 2 小时网红火锅'],
    footprints: [
      { date: '6/21 周六', place: '欢乐海岸', scene: '约会', note: '娃交给外婆，二人世界' },
      { date: '6/14 周日', place: '木桑森林·轻食', scene: '减脂', note: '沙拉轻食，老婆满意' },
      { date: '6/7 周六', place: '奈尔宝家庭中心(深圳湾店)', scene: '带娃', note: '室内，下雨也不怕' },
      { date: '5/31 周六', place: '绿茶餐厅(海岸城店)', scene: '家庭聚餐', note: '清淡，人均 83' },
      { date: '5/24 周六', place: '莲花山公园', scene: '带娃', note: 'citywalk 放风筝，天气好' },
      { date: '5/17 周六', place: '反斗乐园(领展中心城)', scene: '带娃', note: '娃玩了 3 小时，很爱' }
    ]
  }
  persist()
  return true
}

export function avoidShop(name: string): void {
  const prof = getProfile()
  if (!prof.avoid_shops.includes(name)) prof.avoid_shops.push(name)
  persist()
}

// 注入 System Prompt 的记忆摘要（第 3 层 Profile）
export function memoryPrompt(): string {
  const prof = getProfile()
  const parts: string[] = []
  if (prof.summary) parts.push(`长期画像：${prof.summary}`)
  const strong = prof.preferences.filter((c) => c.strength >= 0.5).slice(0, 8)
  if (strong.length) parts.push('已知偏好：' + strong.map((c) => `${c.polarity === 'negative' ? '不喜欢' : '喜欢'}${c.text}(×${c.evidence_count})`).join('；'))
  if (prof.favorite_shops.length) parts.push('常去/收藏：' + prof.favorite_shops.slice(0, 6).join('、'))
  if (prof.avoid_shops.length) parts.push('踩过坑：' + prof.avoid_shops.slice(0, 6).join('、'))
  return parts.join('\n')
}

// 第 3 层：RewriteMemory —— 用 LLM 把碎片偏好压缩成稳定画像摘要（越用越懂）。失败静默降级。
export async function rewriteMemory(): Promise<void> {
  const prof = getProfile()
  if (prof.preferences.length < 2) return
  try {
    const raw = prof.preferences.map((c) => `- ${c.polarity === 'negative' ? '[负]' : '[正]'} ${c.text}（证据×${c.evidence_count}，强度${c.strength}）`).join('\n')
    const sys = '你是本地生活助手「小悠」的记忆整理器。把零散偏好合并去重，压缩为不超过 60 字的稳定用户画像摘要，只保留高置信、长期有效的偏好。'
    const summary = await chat(sys, `已有摘要：${prof.summary || '（无）'}\n新的偏好碎片：\n${raw}\n\n请输出更新后的画像摘要（纯文本，一句话）。`, { maxTokens: 160 })
    if (summary) {
      prof.summary = summary.trim()
      persist()
    }
  } catch {
    /* 静默降级 */
  }
}
