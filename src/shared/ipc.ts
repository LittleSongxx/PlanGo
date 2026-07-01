// IPC 频道常量（renderer ↔ main）。preload 一能力一方法。
export const IPC = {
  // Agent 对话（主线）
  agentChat: 'agent:chat',
  agentConfirm: 'agent:confirm', // 两步确认：token 回传执行
  agentStep: 'agent:step', // main→renderer 推送步骤流
  agentStreamCard: 'agent:card', // main→renderer 推送成果卡片

  // 浏览器桥（main→renderer 下发动作）
  browserExec: 'browser:exec',
  browserExecResult: 'browser:exec-result',

  // 标签页管理（renderer 内部，但小悠可请求开标签）
  openTab: 'app:open-tab',

  // 设置 / 数据源 / LLM
  getConfig: 'config:get',
  setConfig: 'config:set',
  pingLlm: 'llm:ping',

  // 技能
  listSkills: 'skills:list',
  toggleSkill: 'skills:toggle',

  // IM Bridge
  imStatus: 'im:status',
  imLoginQr: 'im:login-qr',
  imIncoming: 'im:incoming', // main→renderer

  // 主动关心
  proactivePush: 'proactive:push', // main→renderer
  proactiveList: 'proactive:list',

  // 记忆
  memoryGet: 'memory:get',
  memoryGreeting: 'memory:greeting',
  memoryDelete: 'memory:delete',
  memoryClear: 'memory:clear',

  // 分享协作（局域网 + 二维码）
  shareCreate: 'share:create',
  shareFeedback: 'share:feedback',

  // 攻略导入（截图暂存）
  guideSetImage: 'guide:set-image',

  // 附近发现 / 优惠发现（独立窗口直连）
  discoverFetch: 'discover:fetch',
  dealsFetch: 'deals:fetch',

  // 定位
  locationGet: 'location:get',
  locationSet: 'location:set',
  locationUpdate: 'location:update', // main→renderer

  // 评测
  evalRun: 'eval:run'
} as const

export type IpcChannel = (typeof IPC)[keyof typeof IPC]
