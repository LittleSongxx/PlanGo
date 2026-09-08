// 桌面 Skill 列表与开关；技能正文由独立 Harness 按需读取。
import { readFileSync, existsSync, readdirSync, statSync } from 'fs'
import { join } from 'path'
import { getConfig, setConfig } from '../config'

export interface SkillMeta {
  id: string
  name: string
  description: string
  enabled: boolean
  path: string
}

let cache: SkillMeta[] | null = null

function skillsDirs(): string[] {
  return [join(process.cwd(), 'skills'), join(process.cwd(), 'plango', 'skills'), join(__dirname, '..', '..', '..', 'skills')]
}

function parseFrontmatter(text: string): { name?: string; description?: string } {
  const m = /^---\s*([\s\S]*?)\s*---/.exec(text)
  if (!m) return {}
  const out: Record<string, string> = {}
  for (const line of m[1].split(/\r?\n/)) {
    const i = line.indexOf(':')
    if (i < 0) continue
    const k = line.slice(0, i).trim()
    let v = line.slice(i + 1).trim()
    v = v.replace(/^["']|["']$/g, '')
    out[k] = v
  }
  return { name: out.name, description: out.description }
}

function loadSkills(): SkillMeta[] {
  if (cache) return cache
  const found: SkillMeta[] = []
  for (const dir of skillsDirs()) {
    if (!existsSync(dir)) continue
    for (const id of readdirSync(dir)) {
      const skillPath = join(dir, id)
      try {
        if (!statSync(skillPath).isDirectory()) continue
        const md = join(skillPath, 'SKILL.md')
        if (!existsSync(md)) continue
        const fm = parseFrontmatter(readFileSync(md, 'utf-8'))
        if (found.find((s) => s.id === id)) continue
        found.push({ id, name: fm.name || id, description: fm.description || '', enabled: true, path: md })
      } catch {
        /* ignore */
      }
    }
    if (found.length) break // 用第一个存在的 skills 目录
  }
  cache = found
  return found
}

export function toggleSkill(id: string, enabled: boolean): void {
  const s = loadSkills().find((x) => x.id === id)
  if (!s) throw new Error('Skill is no longer installed')
  setConfig({ skillEnabled: { [id]: enabled } })
}

export function listSkills(): SkillMeta[] {
  const enabled = getConfig().skillEnabled
  return loadSkills().map(skill => ({ ...skill, enabled: enabled[skill.id] ?? true }))
}
