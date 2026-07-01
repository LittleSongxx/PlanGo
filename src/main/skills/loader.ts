// 可安装 Skill 系统（对齐 Anthropic Agent Skills 标准）。
// Drop-in Bundle：skills/<id>/SKILL.md（YAML frontmatter: name+description）。
// 渐进披露：启动只把 name+description 进 System Prompt；命中意图才读全文。
import { readFileSync, existsSync, readdirSync, statSync } from 'fs'
import { join } from 'path'

export interface SkillMeta {
  id: string
  name: string
  description: string
  enabled: boolean
  path: string
}

let cache: SkillMeta[] | null = null

function skillsDirs(): string[] {
  return [join(process.cwd(), 'skills'), join(process.cwd(), 'xiaonian', 'skills'), join(__dirname, '..', '..', '..', 'skills')]
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

export function loadSkills(): SkillMeta[] {
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

// 渐进披露第 1 层：只给 name + description（advert）进 System Prompt
export function listSkillAdverts(): string {
  const skills = loadSkills().filter((s) => s.enabled)
  if (!skills.length) return ''
  return skills.map((s) => `  · [${s.name}] ${s.description}`).join('\n')
}

// 第 2 层：命中意图后读全文
export function readSkillBody(id: string): string {
  const s = loadSkills().find((x) => x.id === id)
  if (!s) return ''
  try {
    return readFileSync(s.path, 'utf-8')
  } catch {
    return ''
  }
}

export function toggleSkill(id: string, enabled: boolean): void {
  const s = loadSkills().find((x) => x.id === id)
  if (s) s.enabled = enabled
}

export function listSkills(): SkillMeta[] {
  return loadSkills()
}
