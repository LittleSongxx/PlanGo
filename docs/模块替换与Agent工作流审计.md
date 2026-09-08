# YOYU 模块替换机会与实际 Agent 工作流

日期：2026-09-08。审查代码基线：`6b76a12`。本轮覆盖桌面入口、浏览器、跨语言协议、规划、模型、地理数据、记忆、队列/持久化、观测与评测；采用源码检查、独立审查与无网络内存探针，未替换产品模块或执行真实商户动作。

用户已经确认的浏览器路线保存在 [架构决策](架构决策.md)，并由根目录 [AGENTS.md](../AGENTS.md) 引用。以下新增替换机会仍是建议，不自动视作已确认选型或已实现能力。

## 总体判断

值得整块替换或简化的内容存在，收益最集中的部分是：统一任务/结果契约、明确状态图、结构化需求变化与依赖失效、三态证据校验、单一地理数据入口、事件订阅和生成式协议维护。

“整块替换”包含三种不同操作：用成熟库替换通用基础设施；用成熟工程模式重组现有代码；保留已采用的成熟基础设施，修复本项目的业务语义。后两类不等于增加依赖。不能把接入新 Agent 框架、记忆平台或任务队列当作闭环本身。

## 哪些模块值得替换

| 模块 | 当前问题与证据 | 建议替换策略 | 仍应由 YOYU 保留 | 优先级/成本 |
|---|---|---|---|---|
| 浏览器执行 | renderer 中自制 DOM 索引、等待、合成点击/输入；复杂页面覆盖不足 | 已确认：Electron 保留，目标 WebContentsView，成熟 Playwright/CDP，DOM-first + 按需 Vision | 同一可见 session、授权、命令身份、回执、UNKNOWN 与业务后验 | 已确认方向；适配成本中高 |
| 任务分流、接棒和完成判断 | `outcomes.py` 规则分流；执行节点只改 input_text；不同分支按旧 kind 判完成 | 共用规范化目标和 Outcome 契约；规划交接显式产生 ExecutionGoal/ActionContract；完成由目标后置条件决定 | 计划版本、人数/时间/金额、来源证据、用户确认语义 | P0；中 |
| 超长阶段调度和图拼接 | 约 1700 行父图、约 670 行 YOYU 图；Supervisor 直接用 fallback，却保留大量覆盖规则；扩展通过 remove/add edge 改父图 | 保留 LangGraph，以原生 StateGraph 明确条件边与阶段职责；共享业务函数，减少单节点子图重复 Input/State/Output | durable interrupt、恢复、预算上限与领域校验 | P1；中高 |
| 多轮需求与失效 | 已有 RequirementOutput 稀疏补丁，但先按中文关键词刷新，再解析规范需求；浏览器另存一套文本编辑 | 复用 Pydantic/结构化输出，将修改归一为字段、操作、值、来源片段；先合并，再按字段依赖重搜/重算/作废审批 | 显式清除与未提及的区别、锁定节点、金额人数校验 | P0/P1；中 |
| 标签式约束判定 | 现有 Evidence/unknown_evidence 未贯穿；缺标签有时当违反，缺过敏证据有时当满足，名称推断又可作为标签 | 共用有限类型谓词与满足/违反/未知三态，要求事实来源、实体归属和时效 | 地方生活业务规则、阈值、人工补证据与确认政策 | P0；中 |
| 地理、搜索和缓存 | Node v5 与 Python v3 两套 HTTP/转换/缓存/限流；JS 定位/路线再有独立状态；来源与精度丢失 | 后端统一高德 Web 服务；JS SDK负责展示/设备观测；共用 LocationContext 与数据类型 TTL；用官方周边/区域搜索能力 | 分店匹配、用户指定地点优先、GCJ02/WGS84边界、事实证据 | P1；中 |
| 事件传输和前端状态 | 主进程每750ms拉 events+snapshot，renderer 每2秒再拉；服务器 SSE 仍每0.5秒查库 | 主进程统一 SSE 订阅，使用成熟 SSE 服务端/客户端实现，重连按 seq 补读，必要时拉权威快照 | 持久事件、版本过滤、认证、命令回执确认及取消 | P1；中 |
| Python/TypeScript 协议 | Pydantic、TypedDict、TS、Zod重复维护，部分响应缺 response model，客户端只 as T | 先补完整 Pydantic 请求/响应契约，再由 OpenAPI 生成 TS 类型；保留薄 fetch，按需生成运行时校验 | 信任边界校验和批准/会话/版本等语义规则 | P1；中 |
| 模型兼容与修复 | 已用 ChatOpenAI/structured output/tool calling；但端点能力未显式区分，各类异常常进入 JSON repair | 沿用已安装 LangChain，显式选择结构化协议；分开解析、网络、额度和权限错误；测试实际后端所需能力 | 运行预算、deadline、真实 usage、禁止伪成功 | P1；低中 |
| 记忆与检索 | 部分读写链缺连接；embedding 开关不对称；两种数据库的检索能力不同 | 先接通已有事实表、事件和 worker 投影；统一 opt-in；service 复用 pg_trgm/pgvector，按需比较 RRF；desktop 大量自由文本后再评估 FTS5 | 用户明确事实、来源/有效期、冲突处理、撤回删除、审计 | P1补链路；换平台暂缓 |
| 可观测性 | 已有 OTel 依赖/span，但未找到 Provider/Exporter/跨进程上下文初始化 | 在 YOYU 启动入口接通现有 OpenTelemetry，选择合适 exporter，再接必要 instrumentation | 业务事件与回执账本、脱敏策略、成本/失败分类 | P2；低中 |
| 通用 UI 交互 | 自制抽屉/标签的焦点、键盘和错误状态不完整 | 简单场景优先原生 button/dialog；复杂通用交互可用 Radix Dialog/Tabs；状态投影用明确 enum | Electron、React、Zustand、现有产品布局与任务状态权威 | P2；低中 |

## 成熟能力的边界与采用条件

### 需求、约束、完成：优先重组语义，不先换框架

现有 [RequirementOutput](../vendor/planora/backend/planora/agent/decisions.py) 已有稀疏补丁，`to_trip_spec` 也有合并规则；不是从零引入结构化解析。应把各分支收敛到同一套含来源的编辑语义。字段操作可以借鉴 [JSON Patch](https://datatracker.ietf.org/doc/html/rfc6902)，但具体人数、预算、时间和活动关系仍要自己验证。

依赖失效示例：改总预算主要重算费用/可行性；改城市要作废区域候选与路线；改出发时间要刷新相关营业、交通和供给；改计划或业务参数要作废相应审批。已有观测不能因为模型改了目标就自动成为新目标的证据。

目前三态基础类型已经存在。所需改造是让所有语义约束一致使用它，而非引入通用规则引擎或知识图谱。室内、饮食、亲子、可预约等条件的“没有看到否定信息”不等于满足；缺少正面事实时应补查或澄清，不能把所有未知都判成无解。

### 地理服务：统一事实权威，直接使用正确的官方能力

关键位置：`src/main/data/amap.ts:28` 的进程缓存、`src/main/location.ts:46` 的 IP 位置、`vendor/planora/backend/planora/providers/world.py:321` 的另一套请求缓存、renderer 的 `lib/amap.ts` 与 `PlanMap.tsx`。现在附近发现是同城关键词搜索；需要真实“附近”时应使用高德[区域/周边搜索](https://lbs.amap.com/api/webservice/guide/api/search/)的坐标与半径能力。

LocationContext 应统一记录坐标系、来源、精度或粒度及时间。高德 IP 返回的 [rectangle 是城市范围](https://developer.amap.com/api/webservice/guide/api/ipconfig)，不能转述成设备 GPS。地图展示应尽量使用已验证的路线结果，避免各层各自选择模式重新计算。单用户场景先统一入口与有界缓存，未见需求前不必增加 Redis 缓存层。

### SSE/OpenAPI/OTel：有现成机制，但仍需保留业务契约

FastAPI [支持由 OpenAPI 生成客户端](https://fastapi.tiangolo.com/advanced/generate-clients/)；对当前薄客户端，可以优先用 [openapi-typescript](https://openapi-ts.dev/introduction) 生成类型。类型生成不提供运行时验证，也不会自动补齐当前缺失的响应 schema。

SSE 可采用 [sse-starlette](https://github.com/sysid/sse-starlette) 和兼容运行时版本的 [eventsource](https://github.com/EventSource/eventsource)。先合并客户端重复订阅；SSE 库不会自动取消服务端轮询。需要降低服务端查询频率时，再用提交后唤醒或已有 Redis 通知，SQL 持久事件仍负责断线补读。LangGraph [streaming](https://docs.langchain.com/oss/python/langgraph/streaming) 提供阶段/自定义事件，但不是跨服务持久审计的替代。

OTel 的 `get_tracer` 调用不等于已经建立采集链。需要按[官方初始化方式](https://opentelemetry.io/docs/languages/python/instrumentation/)设置 Provider、处理器、Exporter，并携带 run/command 对应关系；不要记录密钥、Cookie、完整网页或隐藏推理。trace 用于诊断，不能代替持久业务事件。

通用 UI 行为可复用 [Radix Dialog](https://www.radix-ui.com/primitives/docs/components/dialog) 等成熟原语。此举能减少焦点/键盘交互代码，但不会修复后端任务状态被错误显示成成功的问题。

### 模型与记忆：已有基础，不急于迁平台

模型层已经使用 LangChain 的结构化输出和工具调用。应依据[模型接口能力](https://docs.langchain.com/oss/python/langchain/models)选择协议，把网络失败与 JSON 校验失败分开处理。普通聊天 ping 不能证明工具、图像、结构化输出都可用。只有多供应商路由、负载均衡和跨应用配额成为需求时，才值得额外引入 LiteLLM 类网关。

README 默认 Docker service/PostgreSQL；可选 desktop 才使用 SQLite。service 已有 pg_trgm/pgvector 检索和队列 reconcile，不能说向量路径完全不存在。不过浏览器主链绕过记忆读取且不经过正常终态反思；默认 Compose 未传 embedding 配置，后台建向量被 opt-in 关闭，而 retrieve 分支未同样检查开关，EmbeddingService 还会回用聊天 Key。无网络构造探针确认 `embedding_enabled=false` 时仍能构造该客户端。这不是已发生远端调用的证据，但确认了开关边界不一致。

desktop 的 embedding_json 不被 SQLite 查询用于向量检索；中文自由文本的简单空格分词也有限。先修开关、主链连接、投影积压与删除一致性，再按实际数据量比较 [pgvector 的混合检索/RRF建议](https://github.com/pgvector/pgvector)或 [SQLite FTS5](https://www.sqlite.org/fts5.html)。FTS5 中文 trigram 的短查询有边界，不能当成现成中文语义记忆。迁 Mem0 并不能替代用户授权、事实来源和反馈接入。

## 暂不应直接整块替换的部分

1. **不换掉 LangGraph。** 当前用法可简化，但 checkpoint、interrupt 和恢复已经是成熟基础。改用其他 Agent 框架会先带来迁移成本，不能自动解决目标接棒和业务完成语义。
2. **不直接换 Redis 队列为 Celery/Temporal。** 当前已用 XREADGROUP/XAUTOCLAIM、数据库租约和 command fencing；新队列不会替浏览器保证幂等。Celery [同样要求任务自身处理幂等](https://docs.celeryq.dev/en/stable/userguide/tasks.html)。只有出现多服务长事务、调度/SLA等新需求时再评估。
3. **不删 checkpoint 或业务投影来“去重”。** 前者服务执行恢复，后者服务 API 查询与审计。可以明确事务和重建边界，但不能把两者视为相同数据用途。
4. **回执 JSON→SQLite 是条件性替换。** `harnessClient.ts:45/212/230` 全量读取与同步重写 JSON，已做原子文件提交。若回执大、积压或耗时明显，再改桌面本地 SQLite；Electron 使用 better-sqlite3 存在原生 ABI/打包成本。执行前落盘、中断写入 UNKNOWN、只重发回执的契约必须保留。
5. **组合求解器可以评估，但不是当前第一步。** `backend/yoyu/planning.py:71` 每种风格最多尝试8次单点替换；若真实案例需要多地点联合替换、时间窗、预约槽或交通方式优化，可用 [OR-Tools CP-SAT](https://developers.google.com/optimization/cp) 替换这部分启发式搜索。少量候选先用有界枚举可能更简单。求解器只解决给定事实下的组合约束，不能证明食材、价格或预约事实真实；LLM 意图解析与编译后的独立校验仍要保留。

还有两个该先修的事务边界：浏览器结果在 `backend/yoyu/browser.py:317` 提交后，另行写事件/请求恢复；中途崩溃后重试可能缺少 BROWSER_OBSERVATION 审计事件。队列死信发布失败后的 ack 边界也应复核。适合用现有 SQLAlchemy 事务和持久 outbox 处理，而非借机增加新队列体系。

## 当前有哪些 Agent，谁实际上不是 Agent

| 部件 | 实际职责 | 类型 |
|---|---|---|
| Supervisor | fallback 阶段调度、预算和状态门禁 | 确定性协调器，不是自主 LLM 派工 |
| Requirement | 结构化需求与稀疏编辑，配合澄清 | LLM 专业节点 + 规则合并 |
| Discovery | 先按规范活动检索，缺类别时按需调用模型补查询 | 工具化研究节点；并非每次必调 LLM |
| Advocate | 多人场景下提供预算/家庭等视角 | 可选 LLM 专业节点，逻辑分支，模型请求串行 |
| Planner | 根据真实候选 ID 提草案 | LLM 专业节点；不创建商家事实 |
| Compiler / Verifier / Variants | 编译、三态/数值校验、有限候选替换 | 纯代码领域机制，不应包装成独立 LLM Agent |
| Critic | 输出批评；实际修复主要是有界确定性操作 | 可选 LLM 专业节点 + 修复服务 |
| Browser | 观测后逐步决定工具动作 | 受约束的工具 Agent，当前另一条子流程 |
| ImageReading | 对用户提供图片做文字/事实抽取 | 图像模型调用；不是浏览器 Vision fallback |
| Reflection | 从终态资料提记忆建议，按策略提交 | LLM 专业节点；正常 browser 终态未连回 |
| Memory / Queue / Geo / Ledger | 数据与运行基础设施 | 服务，不是 Agent |

这些名字可以组成清楚的架构，但不能用类名数量证明自主性、并行速度或质量收益。单节点子图重复三套输入/状态/输出类型，已经出现 Advocate evidence 传递缺失的问题；应保留确有隔离价值的子图，其他直接使用原生节点与明确数据契约。

## 当前工作流与闭环程度

[当前工作流图](../figures/yoyu-current-workflow.md) / [PNG](../figures/yoyu-current-workflow.png) 是语义摘要：

`用户 → 上下文/可选OCR → 分流 → 记忆 → 需求 → 检索 → 可选视角 → 草案 → 编译校验 → 修复/澄清/候选审批 → 浏览器子流程 → 结束或人工回填`

这里存在两条不同主链：规划侧有需求澄清、批评修复和重新选择的局部闭环；浏览器侧有观测、决策、审批、输入回读的局部闭环。二者的业务交接、连续点击和终态反馈尚未完整接通。具体如下：

- **已有：** 澄清后重新解析、草案校验后有限修复、输入后再观测、状态/命令恢复、手动偏好 CRUD。
- **缺口：** 批准计划后只改写 input_text，旧 browser_task_context 未转换为执行目标；BrowserDecision 仍按旧 kind 判定完成。
- **缺口：** click 被当作业务写后置核验分支，当前没有 business_receipt 身份核验产物，普通点击就可能结束为部分失败。
- **缺口：** 浏览器直接输入绕过 load_memory；正常终态直接 END。Reflection 仍在拒绝等路径可达，不能说完全不可达，但不是正常完成的统一收尾路径。
- **缺口：** Advocate 的输入没有传完整 evidence，合法引用存在被过滤的路径；多人视角聚合不是完整的证据协商机制。

## 本轮新增控制流探针

结果见 [architecture_probes.json](../eval/architecture_probes.json)。这些是无 Key、无网络的内存规则/节点探针，模型只返回 fallback，页面为明确的测试样本；不是实际商户任务成功率。

1. **规划到执行接棒：** 初始 kind=planning，审批后输入含预约要求且 execution_started=true，kind 仍是 planning。向浏览器完成节点提供帮助页观测，结果为 SUCCEEDED，仅给“已读取页面”理由。说明只改自然语言提示不能建立执行契约。
2. **图片入口：** 界面默认“我上传了一张攻略截图……结合真实信息帮我规划”被规则归为 browser/extract，对应 image_finish 返回 SUCCEEDED。该探针未调用 OCR；它确认预设用户请求与路由/完成判定不一致，不能据此说图片规划闭环已完成。
3. **需求刷新：** 改到解放碑、从观音桥出发、改成明天均返回 refresh=False。
4. **Embedding opt-out：** 显式关闭 embedding、仅给虚拟聊天 Key，仍能构造 embedding 客户端；未发出任何请求。

## 建议的目标工作流

[目标工作流图](../figures/yoyu-target-workflow.md) / [PNG](../figures/yoyu-target-workflow.png) 是待实施建议。

核心 LLM 职责可以收敛为 Requirement、Research、Planner、Browser 四类；Critic/视角模块按需调用，Reflection 成为可信终态与用户反馈触发的可选提炼能力。确定性调度、Verifier、ActionPolicy、记忆投影都应明确是系统机制。

目标链路：规范目标/编辑 → 按依赖刷新数据 → 检索证据 → 草案与三态校验 → 交付方案或产生明确执行目标 → 受信浏览器执行 → 后验/接管 → 统一结果 → 可信事件与用户反馈 → 幂等记忆投影 → 下次任务按需读取。

所有回环必须有预算、取消与停止条件；未知证据无法补齐时进入澄清或部分完成，动作结果 UNKNOWN 不重放提交。先把交接对象、状态转移和后置条件讲清楚，再决定是否增加角色和并发。

## 实施顺序建议

1. 先处理前次评审的 HTML 渲染风险、硬约束证据语义，再统一入口、接棒与完成契约。
2. 同步推进已确认的浏览器驱动接管验证；把规划到执行、普通点击继续、业务核验放到同一个纵向验收场景。
3. 统一 RequirementPatch/位置事实/依赖失效，再接 OpenAPI 单源协议与主进程事件订阅。
4. 接通可选记忆反馈和实际 OTel 链路，用失败分类与代表性任务评测决定是否需要求解器、更多 Agent、SQLite 回执或独立记忆平台。

本轮完成的是决策持久化、替换机会审计、控制流探针和图示；没有将上述建议写成已经实施的能力。
