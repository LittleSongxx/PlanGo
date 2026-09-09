# PlanGo 的 Agent 架构与选型

更新：2026-09-09。本文按当前工作树中的实现说明架构，不把设计目标或开发工具能力记为产品能力。阶段验收、部署状态与尚未通过的真实用户流程以 [实施进度](实施进度.md) 为准；本轮使用真实用户行为验证功能，不开展完整的角色收益或 DOM/Vision 指标评测。

## 1. 当前系统是什么

PlanGo 是 **集中式 Agent Workflow：行程层先规划、校验、审批，再准备或执行；浏览器层使用受控的 ReAct 工具循环**。Requirement、Planner 等专业节点可以调用模型；主流程协调器按状态、证据与审批条件选择下一阶段。模型负责语言理解、候选组织和页面操作选择，确定性代码负责契约、调度、事实校验、权限及结果验收。

这一区分采用 [LangGraph 官方的 workflow/agent 定义](https://docs.langchain.com/oss/python/langgraph/workflows-agents)：代码预设流程与模型动态选择工具可以组合存在。它解释了本项目的结构，不证明各专业角色提高了质量。

| 部分 | 实际职责 | 代码入口 |
| --- | --- | --- |
| Electron + React | 用户输入、计划/结果展示、审批、可见页面和人工接管 | `src/renderer/src/App.tsx`、`src/main/ipc.ts` |
| DesktopRuntime | 接收任务，保存用户命令，恢复运行，组合本项目浏览器工作流 | `backend/plango/runtime.py::DesktopRuntime` |
| LangGraph 工作流 | 持久状态、阶段回边、暂停及恢复；不是另一个用户数据源 | `plango_harness/agent/graph.py::build_graph`、`agent/state.py::PlanGoState` |
| 确定性协调器 | 根据已有产物与验证结果推进或暂停；不调用模型进行主阶段路由 | `agent/graph.py::_coordinator_decision`、`build_graph.coordinate` |
| LLM 专业节点 | 需求补丁、补充检索词、方案草稿、视角评价、批评和记忆建议 | `agent/subagents/` |
| 浏览器 Agent | 观察页面 → 选择有界操作 → 必要审批 → 执行 → 重新观察/验收 | `backend/plango/graph.py::build_desktop_graph` |
| 工具与领域服务 | Geo、搜索、日期天气、计划编译、三态事实验证、账本 | `backend/plango/geo.py`、`world.py`、`plango_harness/tools/registry.py`、`domain/planning.py` |
| 受信桌面执行器 | WCV 生命周期、同一会话上的 Playwright/CDP、固定 DOM 操作与原生截图 | `src/main/browserView.ts`、`browser-bridge.ts`、`browserDriver.ts` |

表中 `plango_harness/` 均指仓库内固定源码 `vendor/plango_harness/backend/plango_harness/`。这些服务或节点拥有不同职责，并不意味着各自拥有自治任务队列、独立长期记忆或独立模型进程。

## 2. 为什么采用这组模式

| 模式 | 本项目选择与理由 | 实际限制 |
| --- | --- | --- |
| Plan-and-Execute | 行程先形成带版本的结构化计划，校验约束与证据后请求审批；变更重新生成并验收 | 模型草稿不是可执行事实；“准备好供用户核对”不等于预约成功 |
| ReAct | 浏览器页面变化难以预先写成完整步骤，采用观察和操作交替的有界循环 | 工具集合、页面目标、预算、授权和后置条件都受代码限制；不是任意 JS/shell Agent |
| Supervisor-Workers | 采用集中协调、专业节点产出结构化结果的形态，Advocate 可有多个视角 | Supervisor 是确定性协调器；不是一个模型自由分派无限子任务 |
| 自由 Agent Team/去中心化协商 | 当前不采用。一个任务共享同一浏览器会话、同一审批链与副作用账本，统一所有者更易恢复与核验 | 没有 Agent 之间自由 handoff、私聊、竞价、投票后授权或自行增员 |
| 完全单一长提示 Agent | 不作为主实现。需求、事实验证、浏览器副作用和记忆的边界需要单独表达与检查 | 专业节点仍复用同一个 ModelAdapter；不是为了角色数量增加模型数量 |
| 全部硬编码页面流程 | 不作为通用网页执行方案。页面结构和任务表达变化需要模型选取下一操作 | 对业务成功等高风险结论仍用确定性后置条件，不交给模型一句 `finish` |

保留 LangGraph 的原因是已有 checkpoint、interrupt、重试和图状态需要延续；本次改动使用显式入口和回边，符合其 [custom workflow 组合方式](https://docs.langchain.com/oss/python/langchain/multi-agent/custom-workflow)。没有再叠加另一套 Agent 编排框架。

## 3. 一次任务如何闭环

桌面入口是 `task_context` → `image_entry`。上下文判断当前任务是行程规划、页面读取、写操作还是执行准备。纯图片读取也有独立来源验收，用户图片不能冒充当前网页。

行程规划路径为：`load_memory` → `requirements` → `supervisor` → `discovery` → 可选 `advocate_fanout/advocate_worker` → `synthesis` → `verify` → `browser_variants` → `supervisor` → `propose_actions/approval`。协调器根据实际产物选择阶段，缺证据、地点冲突等可以进入澄清；有界修复后仍失败则保留原因。

`browser_variants` 对“没有已知硬冲突、但事实尚不完整”的计划进入 `browser_draft_review`，不要求用户回答实时排队或库存。该 interrupt 复用持久命令和现有 checkpoint；`draft-decision` 同时绑定 interrupt、plan ID 与版本。用户可保存 `draft_ready` 草案，或在人数、日期、当前门店身份与地址来源足够时明确选择“仅准备表单”。后者的 ExecutionGoal 带 `plan_verification=draft` 与全部 `pending_checks`；原 Verifier 仍为不可执行。未知值不会升级为已核验，已知硬冲突不能走这个准备入口。

用户批准某个 `plan_id/version` 后，`BrowserTools.prepare_browser_execution` 生成绑定审批的 ExecutionGoal，再进入 `browser_first`。浏览器循环的核心节点是 `browser_operate`、`browser_decide`、`browser_approve` 和 `browser_check_receipt`。后端命令通过 Harness 进入主进程固定操作；页面返回观察后，后端依据任务目标与来源生成 ExecutionOutcome。

准备阶段的 DOM 表单控件和值会进入浏览器上下文。只有严格绑定当前商家、原生 form、页面快照和入口，并且人数/日期/时间仅有一个唯一差异时，代码直接生成对应 `type` 提案，优先于模型和 Vision；它仍等待同一套具体操作审批。输入后使用新快照验收，同 URL 的旧工作集不再被当成当前表单，旧观察仍留在持久命令和历史记录中。

未完成 Preparation 可通过“继续核对表单”入口恢复原 Goal：服务端检查原 plan ID/version/approval ID，拒绝取消、拒绝、UNKNOWN/RUNNING 或计划已变的请求。该入口复用持久 replan 游标和有界用户轮次预算，首步重新读取当前可见页，清除旧标签、快照与写提案；不会重新规划时间去凑表单值，也不会复用原输入审批。每项新的输入仍单独批准，结果仍仅为 `ready_to_review`。

不同结果必须分别表达：

- 页面读取有可追溯内容，才能称读取目标已满足；模型说 `finish` 本身不算证据。
- 草案保存只交付带待核验项的计划，状态虽为终态，`scope=draft_ready` 和 `execution_allowed=false` 明确其范围；不记为履约成功。
- Preparation 要核对同一原生表单中的门店/分店、人数、日期和时间；结果范围是 `ready_to_review`，明确未提交。
- 输入或普通链接导航完成，只证明浏览器交互完成，循环继续观察。
- 业务提交只有核对本次身份与业务回执才可称完成；无法核验保留 `UNKNOWN`，不得重新提交碰碰运气。

用户编辑已批准计划，会清除旧执行目标与结果，返回需求解析。`build_graph` 的 `entry`、`replan_entry`、`after_verify`、`after_execute` 是四个显式组合点，桌面层分别使用 `task_context`、`task_context`、`browser_variants`、`browser_first`。扩展层只注册本项目节点和边，已删除此前先构建再逐条删边的拼接方式。

澄清回答也回到同一个 `replan_entry`，不另写一套上下文合并入口。`ask_user` 只增加一次用户 turn，后续 `task_context` 更新该任务的意图/最新文本/编辑记录，再走需要的专业节点；因此“按当前网页继续规划”和“不要规划，先读取页面”可以分别保留规划来源与切换任务类型。

地理规划与页面执行由来源策略连接：`BrowserWorld.bind_run_state` 根据持久任务请求和后续编辑，在当前任务的 `run_context` 中选择来源。有高德配置的普通行程规划直接使用真实高德检索、路线和日期天气，不要求先打开一个浏览器标签。明确“按当前网页/菜单规划”，或未配置高德时，保留既有网页证据路径；不生成空白页面的成功回执来补接口。供给接口不能取得的排队、库存、可订信息仍为未知，允许显示待核验草案，不能绕过 Verifier 自动执行。

来源选择不保存在 provider 全局开关中；恢复时从本任务状态重建。改地区后，旧网页中不属于当前坐标范围的地点不会混入新候选，旧路线缺少当前起点绑定时不复用。普通高德路径也不会顺便把另一张已打开网页的数据叠加到地理检索结果。

明确要求步行时，BrowserWorld 请求[高德官方步行路线接口](https://lbs.amap.com/api/webservice/guide/api/direction#%E6%AD%A5%E8%A1%8C%E8%B7%AF%E5%BE%84%E8%A7%84%E5%88%92)，不能用驾车绕行距离冒充步行距离。未指定交通方式保留 driving 默认；明确公共交通而当前适配器未覆盖的查询返回未知，不自动改为驾车。“观音桥步行街”这样的地名既不是步行方式要求，也不是距离上限。

## 4. 需求、地点与事实如何保持一致

`RequirementOutput` 表达稀疏补丁：未提及保留，明确修改设置，明确取消清除，未知仍保留未知。`RequirementOutput.to_trip_spec` 合并旧规范，随后 `agent/requirements.py::requirement_delta` 比较规范字段，最后才决定刷新。`planning_reset` 统一用户编辑、重规划和澄清回答的失效边界。

Planner、Advocate 和 Critic 的模型输入不再包含累积的 `TripSpec.goal` 原文，只传当前结构化规范；例如预算已取消时，模型看到 `budget=null/per_person_budget=null`，人数、日期、当前硬约束和明确偏好照常保留。原消息及 goal 仍持久保存用于需求合并与审计。Advocate 报告/角色列表采用追加 reducer，重规划写入 `[]` 不会清掉历史；因此实际工作流按 run/turn 选择当前报告，worker 绑定真实任务、轮次和分派角色，模型不能自行改归属。旧 checkpoint 未携带 run ID 的报告按所在任务归属兼容，仍受轮次过滤。

| 修改 | 保留与刷新 |
| --- | --- |
| 预算、软偏好 | 保留尚有效的地点和来源，重新计价/排序/验证；不因含“改为”就误识别为新地点 |
| 起点或目标地区 | 刷新依赖地点的检索、路线与上下文；原用户起点与目标搜索中心分开保存 |
| 日期、时区、开始时间或时长 | 刷新相关天气、供给和路线事实；新的今天快照不能证明明天营业/有座 |
| 人数 | 刷新容量、预算与时序相关验证；不自动丢弃所有地点 |
| 明确步行/驾车/公共交通 | `travel_mode` 进入规范和路线缓存键，刷新路线及依赖到达时间的供给；旧 checkpoint 缺字段时沿用 driving |
| 新活动或更严格场所条件 | 必要时补检索；已有锁定站点与已选门店不能静默消失 |

`TripSpec.location` 是真实起点，`search_location` 是搜索中心，`must_visit_place_ids` 是用户已选定的目标。所选 POI 在后端按 ID 向本项目高德详情接口强制读取规范数据，初态原子保存。Discovery 复用其原 `evidence_id/observed_at/expires_at`，不为缓存事实盖新时间。所选门店与新地区冲突会要求确认；编译器/Verifier 也检查必须到访的 ID，锁定站点不能靠换一个同名候选顶替。

地点短语先分为已有起点、已选门店指代、通用活动类别、新的具名地点。`location_reference/search_location_reference` 与名称查询分开：当前位置有已知坐标就复用，没有就澄清；“当前网页这家店”复用 canonical 目标；“一家餐厅/一个公园”只约束活动，不能当新地名。明确“搜索中心改为某店”与 canonical 名称一致时直接用该 ID 的坐标；其他名称调用受当前城市范围约束的真实 geocode。城市范围在当前 run context 中恢复，明确城市名或完整市前缀地址可覆盖原范围，异地普通名词不能悄然成为新中心。

路线失败时，地点距搜索中心的距离不能代替用户到店路程。Provider 计算真实起点至目标的直线下界并标记 `distance_kind=straight_line_lower_bound`，不返回行程分钟，也不称查询成功。下界超过用户上限可判定冲突；未超过仍缺路线证据。PlanStop 和界面保留该标记，与成功查询的路线距离区分。

原任务在人工等待期间可能使选店身份来源过期。规划入口在证据过期、明确重新核验或规范日期变更时，按原 POI ID 强制获取一次真实详情，并计入本轮工具预算；同一 turn 的重复进入不重复刷新。只有成功且 ID 匹配的响应才产生新 Evidence，替换当前使用的旧身份来源；原起点、锁定计划与历史事件保留。失败时保留原来源时间并阻止准备，不靠新建任务、重命名证据或续签旧事实恢复功能。`SELECTED_POI_REFRESHED` 事件保存新来源与被替代的证据 ID，可沿同一 run 核对。

`visit_date` 可为空，`timezone` 默认 `Asia/Shanghai`，开始时分不明确时为空。“明天”按该用户命令接受时的持久时间锚点解析，浏览器恢复不会重新按当天计算。尚未明确的时分可以形成建议计划时间，但只有用户实际看见并批准的计划时间才能作为 Preparation 的 `approved_plan` 来源，不能声称用户原文指定过。

`domain/planning.py::visit_payload` 限定时效事实适用范围：日期匹配的来源、明确的周营业表，或确属所选日期的当日观测才可使用。普通高德接口无法证明排队、可订和剩余座位时保持未知。三态事实检查允许“满足/违反/未知”，不会把“未发现违反”视为满足。

高德搜索与选店详情使用同一 POI 解析器及 v5 商业字段，单一明确营业区间写入对应 Evidence；复杂分段/周营业描述保持待核验。依据是[高德 POI 2.0 的字段及 ID 查询说明](https://lbs.amap.com/api/webservice/guide/api/newpoisearch)。天气使用[官方 `reporttime` 与 `casts.date` 字段](https://lbs.amap.com/api/webservice/guide/api/weatherinfo)保留发布时间和适用日期；应用的保守失效上限为实况 2 小时、预报 24 小时，单次证据最多再有效 10 分钟。重复获取旧报告不延长来源有效期，缺内容、错日、过期和未支持时区都返回未知。

## 5. 单/多模式、Agent 和子图的真实边界

配置字段保留 `agent_mode=single|multi` 兼容既有配置，默认 `multi`；DesktopSettings 和 Compose 已接入 `PLANGO_AGENT_MODE`，界面不增加概念复杂的模式选择器。它实际上控制**视角策略**：

- `multi` 且人数大于 1 时，按需求选择体验、预算、家庭、健康等 Advocate，并用 LangGraph `Send` 分发。
- `single` 跳过 Advocate；Critic 使用确定性 Verifier 结果，不再额外调用 LLM Critic。Requirement、Planner、Browser 等阶段仍存在。
- 单人任务即使处于默认 `multi` 也不启动多人 Advocate。模式不改变任何审批或执行权限。
- `Send` 表示逻辑 fan-out；同一个 `ModelAdapter._call_lock` 串行预算与模型调用。当前不能宣称“多个大模型同时协商”或已经证明并行加速。

本轮删掉了 `ModelAdapter` 在 `single` 模式下替换所有 system 第一段的做法。它会破坏 Browser 专业角色或前置 Skills 说明；视角策略只应影响对应节点，不应改写其他角色的职责。

`_fallback_decision` 已更名为 `_coordinator_decision`；原来的“仅供故障 fallback”说明不符合真实调用链。持久节点 ID 仍为 `supervisor`，`SupervisorDecision` 类型名和历史事件名也保留兼容；新事件带 `component_kind=coordinator`、`routing=deterministic_coordinator` 与 `coordinator_reason`，累计模型计数不表示该协调器调用过模型。

Requirement、Discovery、Advocate、Critic、Reflection 的单节点子图目前保留，作用是输入输出边界与现存 checkpoint namespace 兼容。它们不是自治运行时。本轮补齐 Requirement 的时间/需求差分、Discovery 的缓存/目标字段，以及 Advocate 在 Send、Input/State 和实际调用处的 Evidence；否则外层字段存在也会在子图输入处被过滤。`backend/tests/test_requirement_transitions.py` 直接调用真实编译子图验证该链，防止只测 helper 而漏掉运行边界。

## 6. 状态、记忆、检查点和缓存不是一回事

| 存储 | 目的与当前实现 | 不能替代的职责 |
| --- | --- | --- |
| PlanGoState 工作状态 | 当前规范、候选、活跃 Evidence、计划、审批/执行目标、浏览器观察和结果 | 不等于长期用户偏好；旧证据保留不代表仍有效 |
| LangGraph checkpoint | SQLite/PG saver 保存节点位置、状态和 interrupt，运行恢复使用固定类型白名单 | 恢复步骤不授权新的页面动作，也不代表副作用只执行一次 |
| RunRepository 与事件 | 用户命令、运行快照、事件序号、租约与恢复事实；事件和当前投影分工 | 界面聊天消息不是任务真相；Redis 队列也不是最终任务数据库 |
| ActionLedger 与浏览器回执 | 动作身份、幂等状态和 UNKNOWN；桌面执行回执与服务端观察另有持久记录 | 页面“成功”文案或模型总结不能覆盖账本未决状态 |
| 长期记忆 | MemoryRepository 存事实、规则、episode/document 与 memory_event；显式来源、有效期、冲突优先级、遗忘接口 | 推断不能覆盖更强用户声明；历史商家事实不能作为当前营业/价格证据 |
| 检索索引 | PostgreSQL 词法 `pg_trgm`，可选向量检索；SQLite 有有限词法路径 | Embedding 是检索辅助，不是记忆本体；无 embedding 仍应读写结构化记忆 |
| Provider/计划读取缓存 | 避免同一次运行重复读取；规范差分和证据过期决定是否可复用 | 缓存命中不能续期或变成业务已核验 |

`MemoryResolver` 根据显式用户来源、置信度和遗忘操作解决冲突；`MemoryRepository.commit` 保存记忆事件与投影。Embedding 在 DesktopSettings 中是显式 opt-in，不借用聊天 Key 暗中发起向量请求；关闭时新文档标记 disabled，并使用已有结构化/词法路径。

执行预算以明确的新用户文字为边界。`RunRepository.update_input_with_event` 在同一事务保存命令和 `turn_budget` 的 grant ID、模型/工具累计基线，重复 pending command 复用原 ID；Runtime 的显式 replan 同样保存独立 grant。每轮仍使用配置中的固定上限，累计 token、模型调用和工具调用持续增长，不清零报表。ModelAdapter 与工具门限按“累计基线 + 本轮上限”执行，浏览器命令按同一 grant ID 计数，旧命令归初始范围。

每种 interrupt 都持久保存剩余执行秒数，包括澄清、审批和浏览器等待；恢复前先持久消费该暂停记录，再执行图节点。审批、浏览器回执和自动重放不补 token/工具额度；新用户文字才获得新一轮有界额度。`test_turn_budget.py` 用真实编译图与 SQLite checkpoint 验证三次用户编辑的累计用量，以及暂停恢复前和 checkpoint 完成后发生进程退出时不重复续时/执行。

浏览器入口通过同一个 MemoryRepository 按当前用户读取明确偏好和限定范围的历史观察。网页内容、用户反馈评分或模型推断不能自动写成用户偏好；记忆也不成为当前商家事实或工具权限。无 Embedding 时仍走既有结构化/词法检索。

浏览器终态仍直接 END，随后 DesktopRuntime 根据持久 ExecutionOutcome 投影确定性 episode：只接受 `read_only`、`image_text`、`ready_to_review` 或已经核算的比价结果，保留来源和观察时间，默认有效 30 天。`ready_to_review` 的草案标记与待核验项同时保存，`UNKNOWN/RUNNING`、未核验业务声称、草案保存和部分失败不写成自动成功记忆。恢复时扫描最近终态并以 `run_id:turn_id` 的稳定来源去重，不再额外调用模型反思已完成读取。

原 Reflection 节点用于其实际可达的工作流分支，其建议仍经过 MemoryResolver；来源同样绑定 run/turn，并先检查已有事件。评分反馈使用明确的 feedback ID、当前 turn 与内容摘要，重复内容返回原确认，冲突拒绝；只有显式填写的偏好才产生 fact。MemoryRepository 在同一用户事务锁下保存事件及投影，事件墓碑保留去重身份并清除原内容。删除 episode 可重复请求，删除全部记忆保存遗忘时间，旧终态恢复不能重新生成被忘记的内容；过期项从画像和检索排除。辅助回归覆盖这一整条 API/图/恢复路径，真实 UI 验收状态继续以实施进度为准。

最后的真实可达路径审查发现：原计划拒绝仍会到达 Reflection，且子图未传入 turn/phase/审批决定。现已补齐输入；拒绝、取消、无动作或动作尚未全部确认成功均跳过模型反思。模型 Reflection 不得写入 fact 或以 `preference:/favorite:` 为键的记忆；明确偏好由用户 API 保存。迁移前的推断数据不删除，画像条目新增原始 `source` 和 `explicit` 标记，界面可区分明确偏好与历史推断，不能把旧模型输出改称用户声明。

## 7. Skills 与 MCP

产品的 Skills 是本仓库 `skills/<id>/SKILL.md`，由 `backend/plango/skills.py` 管理。入口只给模型有限的 ID、名称、描述目录；模型通过 `read_skill` 请求启用的正文，再受长度限制地纳入当前浏览器上下文。它是渐进加载的任务说明，不是可执行插件安装器。目录穿越、符号链接与特殊文件读取受限，正文不能增加工具权限或绕过审批。

此选择参考 [Agent Skills 官方规范的渐进加载](https://agentskills.io/specification#progressive-disclosure)：先读元数据，任务需要时再读正文。PlanGo 当前仅实现本地目录、有限元数据与只读正文这一子集，不宣称支持规范中的任意脚本执行或全部扩展字段；`allowed-tools` 文字也不会改变 Harness 权限。

**当前产品没有 MCP client/server 实现。** 已检查 `backend/plango`、vendor runtime、`src` 的运行入口和包依赖：高德通过本地类型化服务/HTTP 接口调用，模型工具经过 `ToolRegistry` 和固定浏览器命令，没有 MCP 协议发现、能力处理、工具发现或 transport 生命周期。开发 Codex 可以使用的 MCP/connectors 不属于 PlanGo 产品能力。

MCP 本身是 host/client/server 的工具与上下文协议，见 [官方架构与协议职责](https://modelcontextprotocol.io/docs/learn/architecture)。仅提供 OpenAI 格式 JSON tool schema 不构成 MCP 接入。当前直接适配器已经覆盖所需高德与可见浏览器边界，因此先保持直接接口；以后确有外部工具复用需求，再增加限定服务器、权限映射、结果来源和连接生命周期。MCP 仍不能取代计划审批、幂等或业务回执校验。

## 8. 本轮已整改和验收边界

已针对实际混乱修改代码：四个显式工作流组合点替代删边拼接；统一 planning_reset；规范需求差分替代关键词清空；子图契约补齐；日期与 Evidence 适用范围绑定；选店与起点分离；协调器命名及事件修正；删除 single 模式对所有专业提示词的串改。这些改动保留持久节点 ID 和子图 namespace，不通过删除 checkpoint 进行升级。

本轮最小辅助回归包含固定时区日期与预算差分、真实编译 Discovery/Advocate 子图传参、single 模式 Browser/Skills 提示词保留、显式北京起点后的预算编辑、审批后 Preparation 再改人数，以及空桌面直接地理规划、来源隔离和变更地区后拒绝旧网页事实。它们均为明确标记的合成样本，不证明真实商家可用。空桌面检查通过同一 API/compiled graph 路径，断言没有任何 page 调用或浏览器命令，最终生成供给未知的待核验草案。最终仍按真实桌面用户路径检查地区/起点/日期/预算/锁定/选店、阅读结果、执行准备与人工接管；缺失业务事实继续展示未知。

P3 的记忆入口、可信范围投影、反馈幂等与遗忘代码已落盘；SSE、回执事务及真实桌面路径仍按各自故障恢复边界验收。代码和辅助样本通过不能替代最后的实际用户流程验证。

### 真实用户流程中保留的失败

2026-09-09 的隔离真实桌面验证暴露了以下问题；此处记录失败事实，定向合成回归通过不替代修复后的真实重走。

- `4b923f7b92a94fa589fe723c249f3cd0`：用户以重庆寿司郎(大融城店)为中心规划，选店 ID/坐标正确，却因模型把门店名放进起点字段而重新全局地理编码到香港。已将“以…为中心”解析为搜索中心，canonical 选店坐标直接复用；用户起点独立保留。
- 同一运行补充“2个人、今天14点、从重庆观音桥步行街出发、3小时、总预算300元”后，仍反复要求确认数值。根因是先前地址中的“步行街8号”被误作未定距离上限并残留；已排除该地名形式，并清理原文不存在的旧上限澄清。
- `d998fab816a7424c8f0784fbf654544b`：用户明确步行2公里内，却使用固定驾车路线且草案选择了更远地点。已补交通方式规范/步行接口及就近候选优先；硬距离违反和未知供给的草案交付表达由主控继续验收，不能用未知供给提示掩盖已知硬约束违反。
- 同一运行在用户查看分享内容后因时间预算耗尽而失败，当时累计模型用量约 6530、工具调用 6，未耗尽对应额度。根因是旧 Runtime 只排除浏览器等待、未排除澄清/审批等待；现统一记录剩余执行时间，并为新的明确用户编辑保存独立有界预算。
- `d51586748f934797a6c8b202ccd8df5b`：准确输入“3位成人、不设预算、只按当前网页这家餐厅”被算成用户1加成人3、预算仍400；后续指代还被当成新地名。已去掉非明确额外参与的用户占位、接通现有预算 clear 语义、保留 canonical 门店指代，并使澄清走统一上下文入口。对应原失败保存在 `eval/plango-live-preparation/first-requirements-clarification.json` 和 `second-requirements-clarification.json`。
- `381fe190c404442894f72b5ddb925524`：重庆起点、步行2公里的请求中，“一家餐厅”被地理编码到西安，候选距离还错误代表距西安搜索中心的距离。已统一活动/地点分类、当前城市范围与失败路线下界；同一原句重试时也会纠正先前错误搜索中心。该原句中的“安排一个2小时行程”同时补齐量词解析为120分钟，未通过改用户措辞绕过。

本轮修复后的真实待测条件是：同一选店模板保持真实起点、补充数值后结束无依据的追问、明确步行走真实步行路线、普通地理规划无浏览器标签也能交付带未知项的草案、预算/日期/交通修改使对应审批和事实失效。执行准备和业务提交仍分别验收，不以草案交付授予执行权限。


## 9. 当前协议、恢复与最终验收入口

主进程持有唯一的任务事件订阅，SSE按Last-Event-ID恢复；序号有缺口时不推进游标，已接受的批次前缀保留。相同版本的首个恢复快照也会推送以清除离线状态，后续才恢复去重。服务端SSE仍以数据库事件轮询实现，不能称已部署消息推送集群。

浏览器结果CAS与BROWSER_OBSERVATION在同一数据库事务写入；Redis死信发布与ACK用原子脚本，发布失败不确认原消息。截图回执可以迟到并按原内容确认，但PNG、命令、页面身份、未来时间及采集早于命令等校验仍保留；Vision实际使用前重新校验30秒新鲜度，过期不送模型、不重拍、不放开写权限。

模型连接、超时、鉴权、权限、额度、限流与结构化错误分别记录，只有真实结构化回复失败进入JSON修复，SDK负责有界传输重试；参见[官方Python SDK错误接口](https://github.com/openai/openai-python#error-handling)。业务事件、命令与模型用量是当前排错依据；可选OTel span接口尚未配置collector/exporter，不宣称已经具有全链路外部追踪。当前共享TS协议与Pydantic/Zod校验手工维护，没有部署全量OpenAPI代码生成。

地图与计划内路线使用规范计划中的origin和travel_mode，不能改用设备全局位置或固定驾车方式；附近发现仍使用用户当前指定位置。外部导航也保留当前方式，按[高德URI模式定义](https://lbs.amap.com/api/uri-api/guide/travel/route)使用car/bus/walk。

R0→P3 真实验收已覆盖同run预算、日期、身份刷新、地点纠正、草案保存；受控原生表单中原Goal恢复后时间修正和人数修改→新版本/审批→再次准备均通过，提交计数为0。接续 N2 已补独立需求卡和单站锁定/解锁按钮，并在同一真实模型/高德任务完成5版及WSL重启恢复；完整结果及未覆盖边界见[实施进度](实施进度.md)。

## 10. 接续结构化编辑与事实表达

`POST /api/v1/runs/{id}/requirements` 接受 `expected_version`、稀疏 `fields` 与可选 `stop_lock`。锁定绑定确切plan/version/place，过期版本、忙碌任务和未决浏览器动作/UNKNOWN均拒绝。字段补丁复用 `RequirementOutput.to_trip_spec`、`requirement_delta` 和持久 replan，跳过模型对明确字段的重解释；地点仍须真实geocode，失败后人工新地址沿原澄清入口处理。旧审批/执行目标失效，累计用量与历史不清零。

需求卡的范围变更（扩大、缩小或取消）实际刷新检索，并传入高德around半径和缓存键；直接编辑限制50km，默认无明确范围时沿用附近5km。起点与搜索中心独立；日期查询对应预报；锁定保留地点/时段而不续签失效证据。公共交通未覆盖部分仍返回未知。

真实扫码页的 `manual_gate` 由可信DOM driver生成；浏览器操作和网页规划读取均在模型抽取前持久暂停。`read_outcome` 不把登录页文本算为目标完成，人工继续只重建只读观察，不重新授权或重放写命令。

方案编译/刷新、历史界面投影和分享说明从结构化门店、人数、估算费用与未知项生成，模型自由 `rationale`/旧 `stop.reason` 保留审计，不能覆盖 `unknown`。天气导致的系统建议不通过自由理由冒充用户明确偏好。角色选择也只读当前规范；跨轮旧coordinator动作不再拼成当前循环。未部署新框架、MCP、OTel或全量OpenAPI生成。

共享消息恢复只向已有checkpoint追加本轮输入，消息ID绑定run/turn；相同用户文字在不同轮次仍是独立消息。checkpoint已经完成但投影尚未提交时恢复投影，不重复执行图；缺checkpoint才按原序列化消息重建。旧ID、AI角色和重复历史不删除，避免清历史掩盖原1→3→7→15→31倍增问题。

## 11. 已登录首站的只读适配与部分结果

实见大众点评短页不再使用文章提取器裁剪；已足够短的可见正文完整保留。门店预览通过精确域名/shop路径、标题及顶部店名一致性进入小型只读解析，提取字面地址、独立货币行售价及同块条件，推荐长串不擅自拆分，原价不从划线数字推断。它复用PageData及现有processed持久写入，没有隐藏接口、MCP或新的Agent。

ReadGoal兼容旧checkpoint，由明确请求推导门店身份/地址、菜单、推荐菜、优惠和条件字段；当前页要求只能使用同URL/snapshot。部分字段完成不代表整个目标满足，资料卡显示已读/仍需核对。实见App菜单入口且只有下载链接时有界停止，保留只读部分结果；验证码路由同时由可信driver与后端保守暂停，不能转Vision绕过。

恢复补投影按观测时间/当前snapshot选择新页面，不按追加数组末项假定新旧；历史仍保留。真实门店预览未出现原生预约表单，未新增预约、下单、支付或消息权限。

## 12. 优惠进入同一规范与路线费用

优惠比较是同一持久页面的确定性投影，保留字面报价、人数口径、来源时间和缺失规则。`OfferSourceRef` 绑定command/artifact，选中项增加索引/hash、canonical POI和人工名址对应证据，写入原TripSpec的`selected_offer`；不建立另一套商家事实。新明确读取可更新比较卡，但不会替换已选优惠，也不延长旧证据有效期。纯浏览器阶段的比较字段只更新原任务上下文；进入规划后复用规范补丁、版本、审批及持久replan。

单站明确锁定、无额外活动且无需重新发现时，规划输入只保留相关候选和证据；原历史不删除。过期身份、日期天气和路线仍按依赖刷新。实际单次改人数样本候选21→1、当前证据23→3；没有严格对照，不据此推算性能比例或解释WSL崩溃。

`search_radius_km`独立于路线约束`max_distance_km`；旧checkpoint/旧字段保留兼容，新UI/API以`route_distance_km`明确路程语义。多结果geocode拒绝默认取第一项。同城公交通过两端真实citycode查询，按bus/walking段合计距离和时长，单人标准票价乘人数一次；缺费用保留null，真实铁路/出租车和跨城路线仍未知。PlanStop中的交通费用及说明随原计划保存，方案/分享使用已知估算小计与未估项，不以总预算通过冒充完整消费保证。

用户可见回复以`ASSISTANT_MESSAGE`写入已有事件账本，与状态投影同一SQL事务；不添加到模型messages或另建对话事实表。前端以完整连续事件序列按接受顺序重建问答，旧任务只恢复已存GRAPH_INTERRUPTED问题，保留已有AI消息；缺日志时回退原消息，不编造回答。重放按同轮/内容/阶段去重，新轮相同内容仍独立；当前输入未执行时不将旧reason当新答复。

## 13. 无业务副作用的参数预览

实际TableCheck页面的landing和Find availability会调用私有cart接口，不能仅以“未点最终提交”定义只读。本轮对已核实悦廊入口采用导航前主进程网络保护，仅允许精确页面和已核对静态资源GET/HEAD；拒绝所有API/cart/checkout/查询与未知流量，关闭标签后仍拒绝无归属延迟请求。没有加入自动写入站点白名单。

官方URL参数预填后，可信隔离DOM读取实际按钮文字并携带主进程保护标记；后端与原请求参数/当前快照/时间核对，结果为booking_parameters，始终availability_checked=false/business_completed=false。年份仅来自URL，不伪装原生form或完整预约准备。网站须知暂停复用既有interrupt/继续/预算/回执，重启后下一次只读观测绑定当前可见标签并重新核对来源；历史失败不删，完整交易权限不变。
