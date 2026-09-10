// IPC 频道常量（renderer ↔ main）。preload 一能力一方法。
export const IPC = {
  harnessRequest: 'harness:request',
  harnessEvent: 'harness:event',
  reminderRequest: 'reminders:request',
  // Renderer submits bounded user intents; main owns browser contents and execution.
  browserRequest: 'browser:view-request',
  browserLayout: 'browser:view-layout',
  browserState: 'browser:view-state',
  browserActivity: 'browser:activity',

  // 设置 / 数据源 / LLM
  getConfig: 'config:get',
  setConfig: 'config:set',
  pingLlm: 'llm:ping',

  // 技能
  listSkills: 'skills:list',
  toggleSkill: 'skills:toggle',

  // 主动关心
  proactivePush: 'proactive:push', // main→renderer

  // 记忆
  memoryGet: 'memory:get',
  memoryGreeting: 'memory:greeting',
  memoryDelete: 'memory:delete',
  memorySave: 'memory:save',
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
