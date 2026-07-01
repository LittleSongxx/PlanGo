# 小念 · AI 本地生活浏览器

> 美团黑客松 · 赛题06「本地探索：周末闲时活动规划」参赛作品

**小念**是一个 LongCat 驱动、对话操控真实美团/大众点评页面的 **Electron AI 本地生活浏览器工作台**。
你对右侧「小念」说一句话，多 Agent 就自己去读页面、搜攻略、抽数据，把「周末下午几小时」的
**规划 → 单点vs套餐比价 → AI点菜 → 排号 → 群体确认 → 发同行人 → 跑腿送花** 这条闭环替你做完；
写操作一律两步确认，拿不到的真实数据用美团官方 VitaBench 数据集兜底并诚实标注来源。

## 差异化（别人都用默认 Mock 数据，我们的护城河）
1. **真·AI 浏览器操控**：登录你真实美团/点评账号，操控页面拿真实门店/团购/菜单/排队；能像 Horsepower 一样自动搜攻略汇总。
2. **精美「行程画布」**：成果是可视化时间线 + 五维评分 + 比价 + 点菜卡，不是一坨聊天文字。
3. **主动关心 + 越用越懂**：会在合适时机主动发消息（排队错峰/收藏展快结束/复购），每次用完更懂你。
4. **平台化**：用户可自己安装 Skill、企业可自定义替换 API（对齐美团 To-A）。

## 运行

```bash
# 需要 Node 20+
cd xiaonian
cp .env.example .env      # 填 LongCat / 高德 Key（本机已内置 .env）
npm install
npm run dev               # 启动桌面 App
```

无界面快速验证（headless）：

```bash
npm run cli -- "周六下午带娃玩4小时 减脂 预算120 上海"   # 一句话出完整行程 + 比价
npm run eval                                              # VitaBench 自评（约束通过率/写确认率）
```

## 目录结构

```
xiaonian/
├── data/shops.json              # VitaBench 到店 611 门店（内存 Mock DB）
├── skills/<id>/SKILL.md         # 可安装技能（Agent Skills 标准）
├── scripts/{cli,eval}.ts        # headless CLI / 自评
├── src/
│   ├── shared/                  # 领域模型 types.ts + IPC 频道
│   ├── preload/                 # contextBridge 暴露 window.xiaonian
│   ├── main/
│   │   ├── index.ts ipc.ts      # Electron 壳 + IPC
│   │   ├── config.ts llm.ts     # 配置 + LongCat 客户端
│   │   ├── data/                # mockdb / amap / converter(防腐层) / adapters(企业可改API)
│   │   ├── brain/               # persona / verifier / antiPollution / dealFinder / memory
│   │   ├── planner.ts           # 规划引擎（5 阶段 + 定向重生）
│   │   ├── tools/               # 工具注册表 + 本地生活工具 + 浏览器工具 + Harness
│   │   ├── agent/               # Harness Loop + 动态Prompt + 子Agent + 事件总线 + 会话态
│   │   ├── skills/loader.ts     # Skill 渐进披露加载
│   │   ├── im/bridge.ts         # 微信 Bridge 抽象层
│   │   └── proactive.ts         # 主动关心引擎
│   └── renderer/                # React UI：浏览器 + 对话 + 成果区画布
└── docs/                        # 设计文档 + Demo 脚本
```

## 关键技术
- **模型**：LongCat-2.0（OpenAI 兼容，原生 tool_calls，已实测）；Provider 可切。
- **多 Agent**：WOWService 式「Agents-as-Tools + Handoff」+ LLM-as-Controller + DPT 快慢思维。
- **数据策略**：真实优先（高德 API + 登录态页面抽取）→ VitaBench 数据集兜底；每条数据挂来源徽章。
- **异常处理**：错误即信息 + 重试 + 3 级降级绝不返 0 方案 + 满位/售罄/超时三类 fallback + 断网切数据集。

详见 `docs/设计文档_小念.md`。
