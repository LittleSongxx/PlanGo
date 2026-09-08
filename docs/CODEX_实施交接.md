# YOYU 后续实施交接

交接日期：2026-09-08。交接前基线：`82f2325`，分支 `feat/planora-browser-harness`。后续若 HEAD/工作区已变化，以新会话实际检查结果为准。本文件汇总前序对话，供新 Codex 会话直接实施，而不是重新开始一轮泛化选型讨论。

## 用户要求与授权范围

用户希望将 YOYU 丰富的本地生活功能、真实可见浏览器操作，与有持久化、审批、证据和恢复能力的 Harness 融合；项目必须完全独立。用户明确要求不要盲目复制 Planora，需边实施边验证和优化。用户已要求综合全部分析路线，另开 Codex 会话继续实施。

- 工作区：`/home/song/code/Agent/multi-agent/YOYU`。先读根目录 `AGENTS.md` 和 `docs/架构决策.md`。
- 允许修改本仓库中的 `vendor/planora` 固定副本；禁止修改或运行依赖兄弟 `../Planora` 仓库、配置、服务，避免干扰其测评。
- 后端 Python 使用 conda `planora`。不要恢复旧 `.venv`；不要向共享 conda 环境无差别 sync/卸载或无必要升级既有包。当前运行依赖已与本仓库锁核对过。
- 本项目 `.env` 已配置真实 OpenAI-compatible 模型、高德 Web Key、JS Key、安全密钥和默认重庆；读取本项目文件即可，不再向用户索要这些已配置值。不输出、复制到公共报告或提交任何密钥。
- 可对 YOYU 进行必要的代码、测试、依赖、构建、专属 Docker 服务和文档修改。先检查运行任务再重启自己的服务；不触碰其他项目容器/进程。
- 不因为常规可逆实施反复请求确认。真实商家预约、下单、支付、发送消息等外部业务写入，仍须具体授权；不能以“实施授权”代替业务操作授权。
- 保留用户已有未跟踪文件 `docs/YOYU_Planora_融合方案.md`，不要覆盖、删除或顺手提交。不要恢复已清理的旧 mock Agent、假订单/取号、演示资料。
- 可以本地提交可验证改动；用户未要求 push、发布 PR 或操作远端分支。

## 已确认决策与有条件建议

**固定方向：** 保留 Electron/React；浏览器承载逐步采用 WebContentsView；执行优先使用适配验证后的 Playwright，Electron 缺口用受控 CDP 补足；DOM-first、按需 Vision。UI、模型与执行器必须使用同一可见页面/session，保留人工接管与登录态。

DOM 和 Vision 共用命令身份、快照、授权、幂等、回执和后验。禁止通过视觉绕过拒绝授权、旧审批、验证码/登录接管或 UNKNOWN 提交；不能把任意 JS/CDP 权限交给模型或不可信 renderer 内容。Playwright 对 Electron/CDP 的适配须先验证，不能假设 firstWindow 等示例自动覆盖所有嵌入页面。

**实施路线中的建议：** 统一目标/需求/执行/结果契约、显式 LangGraph 流程、地理数据入口、OpenAPI 类型、SSE、可选记忆投影与 OTel。可以按必要性和兼容性自主选最小实现，不需要为了表面完整引入所有候选库。

**暂不预设引入：** 另一套 Agent 编排框架、Celery/Temporal、Mem0、LiteLLM、OR-Tools、Node SQLite。只有明确需求与对照证据支持时才采用。现有 LangGraph checkpoint、SQLAlchemy、Redis、业务账本和确定性编译/校验需要保留并完善。

## 先阅读的材料

1. [架构决策](架构决策.md)：用户已确认方向。
2. [模块替换与 Agent 工作流审计](模块替换与Agent工作流审计.md)：整体替换机会、代码证据和条件。
3. [闭环与设计成熟度评审](闭环与设计成熟度评审.md)：关键风险及浏览器路线细节。
4. [当前流程](../figures/yoyu-current-workflow.md)与[目标建议](../figures/yoyu-target-workflow.md)：图是语义摘要；后者不是已实现事实。
5. [架构探针结果](../eval/architecture_probes.json)：规则/节点级反例，未跑全图或真实业务。
6. README、现有 `backend/tests`、`scripts/check-*` 与部署验证脚本。

## 当前状态，不要误读

当前是“确定性协调器 + LLM 专业节点 + 浏览器工具 Agent”的工程 MVP。Supervisor 当前采用 fallback 阶段调度；Advocate 逻辑分支共享模型锁，模型请求串行；Compiler/Verifier/Memory/Queue 都不是独立 LLM Agent。

技术运行链和受控菜单任务已经跑通，不能据此宣称所有商户任务闭环。正常浏览器终态绕过 Reflection/记忆，行程拒绝等路径仍可到 Reflection。图片输入只做图像抽取，不是浏览器 Vision fallback。

本次交接前 Docker `yoyu` 的 API、PostgreSQL、Redis 健康，worker 运行；实际执行前再次确认。API 默认 `http://127.0.0.1:8011`，桌面独立运行。`start.sh`/`stop.sh` 已有安全归属与数据卷保留逻辑。不要在新会话里误停其他同机项目。

## 实施阶段与验收

### P0：先修正确性与安全边界

从已有反例写最小有效回归，随后修共享根因，避免只为样本补关键词。

1. **不可信地图 HTML 与高权限桥。** `PlanMap.tsx` 把地点标题拼入 InfoWindow HTML；先改安全文本渲染。当前只确认源码风险链，未运行实际攻击。原始 `browserEval(code)` 的权限收敛应结合受信主进程执行器，不能简单删除后把真实浏览器功能弄断。
2. **三态约束贯穿。** 已有 Evidence、`passed: bool | None` 和 unknown 基础。普通餐厅没有成分证据也可能通过“花生过敏”行程约束；另一些缺标签又被判硬违反。统一满足/明确违反/未知，未知补证据或澄清，不能默认为安全或全部无解。
3. **入口与执行接棒。** 行程审批后只改 input_text，kind 仍是 planning，帮助页可结束 SUCCEEDED；图片按钮“结合真实信息帮我规划”被分流为 browser/extract。明确规范目标、ExecutionGoal/ActionContract 和 Outcome 后置条件，让不同入口复用语义。
4. **连续动作与业务后验。** 当前 type 可继续，普通 click 会进入业务完成分支并提前结束；回执解析只返回 page_confirmation，没有身份核验的 business_receipt。区分页面交互、外部提交和影响不明；逐步执行后观测，未知提交不重放。商户身份/人数/时间/金额等不能靠成功文案判定。
5. **明显 UI/定位误标。** 等待和小写 connection_error 被投影为绿勾；手填地址被标 GPS/30m；宿主定位权限与商家权限混用。统一真实状态/来源，补最小错误反馈和恢复。

验收：反例在修复前可复现、修后不再误报；已有正向读取/算价仍交付有来源的结果；审批绑定、过期、取消、重复回执、UNKNOWN 都不退化。不能靠一律失败/拒绝刷通过率。

### P1：成熟浏览器驱动与可见会话

先做有界适配验证：定位正确的 guest target/session，人工和 Agent 看到同一页面；导航、新标签、iframe、开放 shadow、动态重渲染、可见性/遮挡、输入、缩放、取消、崩溃/重启都要验证。验证后迁移自研底层定位/点击/等待，保留 Harness 契约。

WebContentsView 迁移需处理布局、焦点、遮挡和生命周期。工具执行及固定脚本在受信主进程持有，不向普通 renderer 暴露任意执行能力。不能通过复制 Cookie 到另一个不可见浏览器冒充同一 session。

DOM 路径稳定后加入按需截图理解/核验与视觉定位，校验 viewport、DPR、zoom、clip、时间、页面身份。DOM 错误先处理 frame/等待/结构变化，不能无条件转成坐标重试。模型图像输入与坐标定位能力需要实际能力测试，不以“OpenAI-compatible”推定合格。

验收：相同授权与反例同时覆盖 DOM/Vision；真实 Chromium fixture 可连续完成多步表单，误点击/重复提交有保护；受控样本不算真实商户履约。

### P2：统一需求、数据与清晰工作流

- 复用已有 RequirementOutput 稀疏补丁，统一 field/operation/value/source，明确未提及、清除、未知；先合并规范需求再按依赖刷新候选、路线、供给及审批。
- 回归“改成重庆解放碑附近”“从观音桥出发”“改明天”等真实措辞。目前 refresh=False 的探针是规则层证据，不是完整线上故障回放。
- 统一高德后端数据入口、LocationContext（坐标系/来源/精度或粒度/时间）及缓存时效。JS SDK继续负责地图/设备观测。附近发现目前为同城关键词搜索，需要附近时用官方坐标/半径能力。
- 保留 LangGraph，减少父图删边拼接与多层重复包装，用清楚条件边连接澄清、检索、规划、校验、修复、审批、执行、后验、终态。
- 补全 Advocate evidence 传递。目标核心职责可收敛为 Requirement/Research/Planner/Browser；Critic/视角按需，不能用角色数或逻辑 fan-out 宣传质量/并行速度。

验收：改地区不借用旧地区候选；改时间刷新相关时效证据；软约束或金额编辑不无差别全重搜；已锁定节点不静默丢失；新旧版本审批不能串用。

### P3：协议、事件、模型能力、记忆与观测

- 补完整 Pydantic 响应契约，再由 OpenAPI 生成 TS 类型；保留运行时及授权语义校验。
- 主进程统一事件订阅，按 seq 补读与重连追平，去掉重复轮询。成熟 SSE 实现可复用，但服务端是否仍查库要单独处理；业务持久事件不能丢。
- 复用现有模型 SDK，明确结构化/tool/vision 能力，分类处理解析、网络、权限、额度错误，不把任意异常都转 JSON repair。
- 修 embedding opt-out 不对称：后台建向量关闭，retrieve 却可能用聊天 Key 建客户端。探针仅构造客户端，未发网络请求。禁止未经启用就访问默认 embedding 端点。
- 接通浏览器入口的明确偏好读取、可信终态/用户反馈的幂等记忆投影。保留来源、有效期、删除和审计。默认 Docker service/PostgreSQL 与可选 desktop/SQLite 的检索能力要分别说明和测试。
- 接通已有 OTel Provider/Exporter与 run/command 关联，保留数据库业务事件。优先处理结果提交与 BROWSER_OBSERVATION 事件之间的事务/outbox 边界、死信发布与 ack 边界。

验收：重连不漏事件、不重复副作用；同用户记忆可用且不串用户，opt-out 零外部 embedding 调用，删除/失效正确；模型失败可定位，用量不虚报。

## 统一验证与完成标准

常规命令：

```bash
npm run check
conda run --no-capture-output -n planora python -m ruff check backend/yoyu backend/tests
MYPYPATH=backend:vendor/planora/backend conda run --no-capture-output -n planora python -m mypy backend/yoyu vendor/planora/backend/planora
conda run --no-capture-output -n planora python backend/yoyu/migrations/check.py
npm run test:independent
python3 scripts/check_lifecycle.py
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:browser
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:desktop
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:full-stack
```

部署/真实调用命令须在源码与环境合适、没有被重启影响的进行中任务时使用：

```bash
npm run services:up
conda run --no-capture-output -n planora python scripts/check_deployment.py
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:deployed
```

`test:deployed` 会调用真实模型并重启 YOYU API/worker。密钥已授权用于有界验证，但不要无限重跑，也不执行真实订单/支付。先用受控页面回归，再用公共真实站点的只读任务验证泛化；具体业务写入保持用户授权。

交接前证据：47 Python 函数测试+35 subtests、39 Chromium 断言、23部署 API 检查；真实模型+真实 Electron菜单/重启链已验证；重庆JS配置、在线SDK、公共地标解析、诊断底图已验证。测试数不是任务成功率，历史模型两批各3/4、首批usage不完整，全部保留；不能覆盖失败或改变分母后宣称提升。

最终需有一个完整重庆纵向场景：需求→真实资料→计划→改商圈重搜→多步浏览器操作→提交前核对→可信结果或明确人工确认→反馈/记忆→恢复。分别记录实际完成、错误完成、接管、重复副作用、延迟、用量；并对同一批任务比较 DOM-only/hybrid 与必要的单角色/多角色收益。

条件性方案（求解器、回执SQLite、独立平台）没有足够需求时记录不采用及依据，不为凑技术栈强行加入。更新 README、状态/架构图与验收报告，使其与实现一致。

## 新会话开始时直接做什么

1. 检查 git status、当前分支、服务与进行中任务；只读理解现有改动，保护用户文件。
2. 读本交接和指定材料，建立 `docs/实施进度.md`，按 P0→P3 记录实现、测试、提交及剩余问题。
3. 直接从 P0 已复现反例进入实现，不再仅输出另一份分析或询问是否开始。可以并行委派互不冲突的小任务。
4. 每阶段做适当回归和独立 review，修完问题再推进；完成一阶段后继续后续核心实施，不把阶段性进展当全部完成。
5. 若有阻碍，保存可复现证据与具体缺失条件，继续不受影响的工作。普通实现选择自主判断；不能假造批准、成功或评测成绩。
