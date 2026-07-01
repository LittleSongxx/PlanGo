// ToolHarness：给每个数据/工具调用统一包裹 超时 / 重试(指数退避) / 降级 / 来源标注。
// 「错误即信息」：失败不抛崩，返回带 error 的结构，交给上层转文字喂回模型自纠。
import type { SourceTag } from '@shared/types'

export interface HarnessOpts {
  timeoutMs?: number
  retries?: number
}

export async function withTimeout<T>(p: Promise<T>, ms: number): Promise<T> {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`超时 ${ms}ms`)), ms)
    p.then(
      (v) => {
        clearTimeout(t)
        resolve(v)
      },
      (e) => {
        clearTimeout(t)
        reject(e)
      }
    )
  })
}

// 带降级默认值的调用：失败重试后仍失败 → 返回 fallback（并把来源标为 fallback）
export async function withHarness<T extends { source: SourceTag }>(
  name: string,
  fn: () => Promise<T>,
  fallback: T,
  opts: HarnessOpts = {}
): Promise<T> {
  const timeoutMs = opts.timeoutMs ?? 12_000
  const retries = opts.retries ?? 1
  let lastErr: unknown
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      return await withTimeout(fn(), timeoutMs)
    } catch (e) {
      lastErr = e
      if (attempt < retries) await sleep(300 * Math.pow(2, attempt))
    }
  }
  console.warn(`[harness:${name}] 全部失败，降级：`, (lastErr as Error)?.message)
  return fallback
}

// 模拟真实世界失败率（演异常处理链路）：满座/售罄/网络抖动
export function rollFailure(rate: number): boolean {
  return Math.random() < rate
}

function sleep(ms: number): Promise<void> {
  return new Promise((r) => setTimeout(r, ms))
}
