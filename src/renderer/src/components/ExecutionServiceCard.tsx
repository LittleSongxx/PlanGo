import { useEffect, useState } from 'react'
import type { HarnessStatus } from '@shared/types'

const checkErrors: Record<string, string> = {
  missing_key_or_model: '缺少密钥或模型名称', authentication: '密钥鉴权失败', permission: '模型权限不足',
  model_or_endpoint: '模型名称或接口地址错误', rate_limit: '请求限流或额度不足', provider: '模型服务异常',
  request: '模型不接受此请求，请核对接口能力', timeout: '模型请求超时', connection: '无法连接模型服务', invalid_response: '模型返回格式不符合预期'
}

export function ExecutionServiceCard({ revision = 0 }: { revision?: number }): JSX.Element {
  const [status, setStatus] = useState<HarnessStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const refresh = async (checkModel = false): Promise<void> => {
    setBusy(true)
    try { setStatus(await window.plango.harness.status(checkModel)) }
    catch { setStatus({ ready: false }) }
    finally { setBusy(false) }
  }
  useEffect(() => { void refresh() }, [revision])
  const execution = status?.execution
  const model = execution?.model
  const check = model?.check
  const recent = execution?.recent_task_model
  const independent = status?.service?.ownership === 'external'

  return <section className="plango-section space-y-3 text-xs" aria-label="执行服务与能力">
    <div className="flex items-center justify-between gap-3">
      <h3 className="font-semibold text-sm">执行服务与能力</h3>
      <span className={status?.ready ? 'text-green-700' : 'text-amber-700'}>{busy ? '检查中…' : status?.ready ? '服务已连接' : '服务未连接'}</span>
    </div>
    {status?.service && <div className="text-[var(--muted)] break-all">{independent ? '独立服务（含 Docker）' : '桌面启动的本地服务'} · {status.service.origin}</div>}
    {!status?.ready && <p role="status" className="text-amber-800">无法读取当前执行配置。请检查运行服务与网络，再刷新；本地模型测试不能代替服务连接。</p>}
    {status?.ready && !execution && <p className="text-amber-800">当前服务版本未提供配置摘要，请更新该运行服务；已连接不代表模型可用。</p>}
    {execution && <>
      <dl className="grid grid-cols-[4.5rem_1fr] gap-x-2 gap-y-2">
        <dt className="text-[var(--muted)]">服务模式</dt><dd>{execution.runtime_profile === 'service' ? 'API + 独立 worker' : '桌面内嵌 worker'}</dd>
        <dt className="text-[var(--muted)]">配置模型</dt><dd className="break-words font-medium">{model?.name || '未配置模型名称'}</dd>
        <dt className="text-[var(--muted)]">模型接口</dt><dd className="break-all">{model?.provider_origin}</dd>
        <dt className="text-[var(--muted)]">密钥状态</dt><dd>{model?.key_configured ? '已配置（不显示密钥）' : '未配置'}</dd>
      </dl>
      <div role="status" className={`rounded-xl p-3 ${check?.status === 'passed' ? 'bg-green-50 text-green-800' : 'bg-[#f4f6f2] text-[var(--muted)]'}`}>
        {check?.status === 'passed' ? '服务文本接口实测通过' : check?.status === 'failed' ? `服务文本接口实测失败：${checkErrors[check.category || ''] || '请核对服务配置'}` : check?.status === 'not_configured' ? '服务模型尚未配置完整' : '服务模型已配置，尚未实测'}
        {check?.checked_at && <div className="mt-1 text-[11px]">{new Date(check.checked_at).toLocaleString('zh-CN')}{check.total_tokens !== undefined ? ` · 本次诊断 ${check.total_tokens} tokens` : ''}</div>}
        <p className="mt-1 text-[11px]">文本测试只验证服务 API 的一次回复；任务工具调用和图像能力仍需按任务核验。{execution.runtime_profile === 'service' ? '摘要来自 API，独立 worker 当前配置尚未单独核对。' : ''}</p>
      </div>
      {recent && <p className="text-[11px] text-[var(--muted)] break-words">最近任务记录的模型：{recent.name || '未记录'} · {recent.status === 'ok' || recent.status === 'success' ? '调用成功' : `调用状态 ${recent.status}`}。记录更新于 {new Date(recent.recorded_at).toLocaleString('zh-CN')}；这是历史证据，不证明当前连接可用。</p>}
      <ul className="space-y-1.5 text-[var(--muted)]">
        <li>高德：{execution.capabilities.amap_configured ? '服务已配置，支持地点、驾车/步行与有日期范围的天气查询' : '服务未配置，规划需补充带来源的地点/路线资料'}；公交覆盖有限，缺失路线和车费保留未知。</li>
        <li>浏览器优先读 DOM；只读 Vision {execution.capabilities.browser_vision_enabled ? '已启用，需模型支持图片' : '未启用'}。上传图片另需模型支持，文本测试不证明支持图片。</li>
        <li>可读取公开资料、制作行程草案；App 专属规则需补充核对。表单输入逐项审批，提交/下单/支付另需具体授权。</li>
      </ul>
    </>}
    <div className="flex flex-wrap gap-2">
      <button disabled={busy} onClick={() => void refresh()} className="px-3 py-2 rounded-lg border border-[var(--line)] bg-[#f7faf7] text-xs disabled:opacity-50">刷新服务状态</button>
      <button disabled={busy || !status?.ready || !execution || !model?.key_configured || !model.name} onClick={() => void refresh(true)} className="px-3 py-2 rounded-lg border border-[var(--line)] bg-[#f7faf7] text-xs disabled:opacity-50">测试服务模型</button>
    </div>
    <p className="text-[11px] text-[var(--muted)]">模型测试会发起一次最多 10 秒的文本请求，可能产生少量用量。{independent ? '修改下方桌面配置不会更新独立服务；请在该服务的部署配置中修改并安全更新 API 与 worker。' : '下方桌面配置在保存后用于桌面启动的本地服务。'}</p>
  </section>
}
