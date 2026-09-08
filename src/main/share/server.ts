// 分享协作服务（Electron 主进程内嵌 HTTP）：把方案快照发到局域网，朋友手机扫码打开、投票、留评语，
// 桌面端轮询反馈并可一键并入方案。移植 weplan consensus（share_link/collab/voting）的数据模型，
// 但用本地 HTTP + 局域网二维码（桌面端无常驻云服务器）。
import express from 'express'
import type { Server } from 'http'
import { networkInterfaces } from 'os'
import { randomBytes } from 'crypto'
import { existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'fs'
import { join } from 'path'
import type { Plan } from '../../shared/types'
import { renderSharePage } from './page'

export interface ShareVote {
  voter: string
  vote: 'up' | 'meh' | 'down'
  ts: number
}
export interface SharePref {
  member: string
  idea: string
  budget?: number
  ts: number
}
interface ShareRecord {
  id: string
  plan: Plan
  city: string
  createdAt: number
  views: number
  votes: ShareVote[]
  prefs: SharePref[]
}

let shares = new Map<string, ShareRecord>()
let storagePath: string | undefined
let server: Server | null = null
let port = 0

function lanIp(): string {
  const nets = networkInterfaces()
  for (const name of Object.keys(nets)) {
    for (const ni of nets[name] || []) {
      // 只要 IPv4、非回环、非内部
      if (ni.family === 'IPv4' && !ni.internal) return ni.address
    }
  }
  return '127.0.0.1'
}

export function startShareServer(preferredPort = 8799, storageDir?: string): Promise<number> {
  if (server) return Promise.resolve(port)
  if (storageDir) {
    mkdirSync(storageDir, { recursive: true, mode: 0o700 })
    storagePath = join(storageDir, 'shares.json')
    if (existsSync(storagePath)) {
      const saved = JSON.parse(readFileSync(storagePath, 'utf8'))
      if (!Array.isArray(saved)) throw new Error('分享存储损坏，请保留文件后恢复备份。')
      shares = new Map(saved)
    }
  }
  const app = express()
  app.use(express.json({ limit: '2mb' }))

  // 手机端只读方案页（自包含 HTML）
  app.get('/s/:id', (req, res) => {
    const rec = shares.get(req.params.id)
    if (!rec) return res.status(404).send('<h1>分享不存在或已失效</h1>')
    rec.views++
    res.type('html').send(renderSharePage(rec.id))
  })

  // 方案数据
  app.get('/api/s/:id', (req, res) => {
    const rec = shares.get(req.params.id)
    if (!rec) return res.status(404).json({ error: 'not_found' })
    res.json({ plan: rec.plan, city: rec.city })
  })

  // 朋友投票
  app.post('/api/s/:id/vote', (req, res) => {
    const stored = shares.get(req.params.id)
    if (!stored) return res.status(404).json({ error: 'not_found' })
    const rec = { ...stored, votes: [...stored.votes] }
    const voter = String(req.body?.voter || '朋友').slice(0, 12)
    if (!['up', 'meh', 'down'].includes(req.body?.vote)) return res.status(400).json({ error: 'invalid_vote' })
    const vote = req.body.vote
    rec.votes = rec.votes.filter((v) => v.voter !== voter) // 一人一票，可改
    rec.votes.push({ voter, vote, ts: Date.now() })
    saveRecord(rec)
    res.json({ ok: true, tally: tally(rec) })
  })

  // 朋友留评语/想法（可带预算）
  app.post('/api/s/:id/pref', (req, res) => {
    const stored = shares.get(req.params.id)
    if (!stored) return res.status(404).json({ error: 'not_found' })
    const rec = { ...stored, prefs: [...stored.prefs] }
    const member = String(req.body?.member || '朋友').slice(0, 12)
    const idea = String(req.body?.idea || '').slice(0, 200).trim()
    const rawBudget = req.body?.budget
    const budget = rawBudget === undefined || rawBudget === '' ? undefined : Number(rawBudget)
    if (budget !== undefined && (!Number.isFinite(budget) || budget <= 0)) return res.status(400).json({ error: 'invalid_budget' })
    if (!idea && budget === undefined) return res.status(400).json({ error: 'empty_feedback' })
    if (idea || budget) {
      rec.prefs.push({ member, idea, budget, ts: Date.now() })
      saveRecord(rec)
    }
    res.json({ ok: true })
  })

  return new Promise((resolve) => {
    const tryListen = (p: number, attemptsLeft: number): void => {
      // Express 5 also invokes a listen callback on errors; use the native listening event.
      const s = app.listen(p)
      s.once('listening', () => {
        server = s
        port = (s.address() as { port: number }).port
        resolve(port)
      })
      s.on('error', () => {
        if (attemptsLeft > 0) tryListen(p + 1, attemptsLeft - 1)
        else resolve(0)
      })
    }
    tryListen(preferredPort, 10)
  })
}

function tally(rec: ShareRecord): { up: number; meh: number; down: number } {
  return {
    up: rec.votes.filter((v) => v.vote === 'up').length,
    meh: rec.votes.filter((v) => v.vote === 'meh').length,
    down: rec.votes.filter((v) => v.vote === 'down').length
  }
}

// 桌面端：创建分享（快照方案），返回局域网 URL
export function createShare(plan: Plan, city: string): { id: string; url: string } {
  if (!server || !port) throw new Error('分享服务尚未就绪。')
  const id = randomBytes(16).toString('hex')
  saveRecord({ id, plan: JSON.parse(JSON.stringify(plan)), city, createdAt: Date.now(), views: 0, votes: [], prefs: [] })
  const url = `http://${lanIp()}:${port}/s/${id}`
  return { id, url }
}

function saveRecord(record: ShareRecord): void {
  const next = new Map(shares)
  next.set(record.id, record)
  if (storagePath) {
    const temporary = storagePath + '.tmp'
    writeFileSync(temporary, JSON.stringify([...next]), { mode: 0o600 })
    renameSync(temporary, storagePath)
  }
  shares = next
}

// 桌面端：拉取某分享的反馈（投票+评语）
export function getShareFeedback(id: string): {
  found: boolean
  views: number
  tally: { up: number; meh: number; down: number }
  prefs: SharePref[]
  mergeInstruction: string
} {
  const rec = shares.get(id)
  if (!rec) return { found: false, views: 0, tally: { up: 0, meh: 0, down: 0 }, prefs: [], mergeInstruction: '' }
  return { found: true, views: rec.views, tally: tally(rec), prefs: rec.prefs, mergeInstruction: buildMerge(rec) }
}

// 汇总朋友意见 → 一句可直接喂 refine 的指令（移植 weplan build_merge_instruction）
function buildMerge(rec: ShareRecord): string {
  const parts: string[] = []
  const budgets = rec.prefs.map((p) => p.budget).filter((b): b is number => !!b)
  if (budgets.length) parts.push(`预算控制在人均¥${Math.min(...budgets)}左右`)
  const ideas = rec.prefs.map((p) => p.idea).filter(Boolean)
  if (ideas.length) parts.push('朋友的想法：' + ideas.slice(0, 5).join('；'))
  const t = tally(rec)
  if (t.down > t.up) parts.push('多数朋友对当前方案有保留，建议换更稳妥的选择')
  if (!parts.length) return ''
  return '综合同行朋友的意见，' + parts.join('，') + '。请据此调整方案。'
}

export function stopShareServer(): void {
  try {
    server?.close()
  } catch {
    /* ignore */
  }
  server = null
}
