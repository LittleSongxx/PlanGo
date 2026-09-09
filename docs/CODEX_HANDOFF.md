# PlanGo 当前实施交接

更新：**2026-09-09（N1→N4 接续收尾，Asia/Shanghai）**。当前仓库：`/home/song/code/Agent/multi-agent/PlanGo`。

这是新 Codex 对话的当前交接入口。**R0→P3 本轮核心实施已完成并本地提交；不要重新更名、迁移目录或重建环境。N2结构化需求编辑和Linux试用交付已在接续中补齐；整个产品仍有真实商家登录后读取/准备等未完成范围。** 本文件区分已实现、已验证和建议后续范围，不能把受控测试当作真实商家履约。

## 0. 最新接续结果（优先于下方带旧时间的快照）

用户已授权直接推进第7节。接续从 `ad686e7` 开始，保留全部历史、用户融合原文与项目配置；未新建/重命名 conda 环境、未动兄弟 Planora、未清库、未push/PR/发布。证据集中在 [N1→N4验收](../eval/plango-next/README.md)。

| 项目 | 已实现/已验证 | 仍未覆盖 |
| --- | --- | --- |
| N1 | 有界检查大众点评/美团；实见大众点评扫码页在模型前暂停，原run经过WSL重启、重绑tab与只读意图纠正后恢复；累计2583 tokens保持 | 大众点评仅为候选首站；尚需用户在可见窗口登录，再核实真实门店/菜单/套餐及可用表单。登录页不算业务读取 |
| N2 | 需求卡直接编辑起点、搜索中心/范围、日期/时间、人数、预算、交通；单站锁定/解锁；真实同run5版及WSL恢复、对应日期高德预报、draft_ready保存 | 商家供给未知保留；公共交通实际覆盖仍有限；不是交易验收 |
| N3 | Linux x86_64/WSLg独立安装、包内Electron非开发启动、重复start、配置诊断、已有安装升级、冷备恢复；17表/72个profile文件/受控Cookie/审批/checkpoint/UNKNOWN/Redis PEL保持 | 受控Cookie不证明真实账号登录或跨机器密钥环解密；未发布，非全平台安装器 |
| N4 | 8场景小回归、47条归档断言；修复历史goal误选预算角色、跨轮误判循环、重规划历史消息1→3→7→15→31倍增；结构化摘要替代未核验模型理由的UI/分享事实 | 没有完整质量测评或严格同输入性能对照，不报告提速百分比；全候选刷新仍可继续优化 |

最终 `npm run check`：**220 pytest +43 subtests**、TS/存储/传输/分享/构建通过；真实主桥 **78** 项、Ruff、mypy **67文件**、生命周期与试用维护回归通过。日志：`output/next-check-final.log`、`next-browser-check.log`、`next-lifecycle-check.log`；旧失败均保留。

主 plango API/worker 已切换本轮源码，PG/Redis健康；切换前后17表1414条原始行全部保留，49 Cookie及桌面身份/回执原值保持，有效租约/待命令/未回执浏览器命令均0。见[主数据连续性](../eval/plango-next/main-data-continuity.json)。**未启动主用户profile桌面**；当前可见窗口是下面独立扫码验证窗口，不能误认为主桌面。

- N1：`output/merchant-next/session-C0ovDv/`；run `f8f8ad2719074b0a9c62cbb08367892f`，`REQUIREMENTS_READY/authentication_required`，独立SQLite后端当前 `http://127.0.0.1:55631`，可见扫码窗口保留给用户。最新恢复消息3→4，累计2583 tokens不变。端口/PID随重启改变，务必重新查归属；不能复制该profile的Cookie到其他浏览器或自动重放写入。
- N2：`output/requirements-next/session-kfOEZ5/`；run `de59413d26ff4b1a958d5d607fc4c6a2`，v5 `SUCCEEDED/draft_ready`，累计14514 tokens/164 tools；末轮增量2566/42，cap仍12000/48/300。测试窗口和后端已停止，原31条重复历史保留，后续消息只增量。
- N3：`plango-trial-check`、`plango-trial-restore-check`均已stop/down，卷/profile/冷备保留在 `output/trial-check/`。实际安装与恢复证据见 [trial-install.json](../eval/plango-next/trial-install.json)。干净源码最终包在 `release/plango-0.1.0-linux-x64.tar.gz`（本地生成，不发布），用法见[试用安装](试用安装.md)。旧dirty试验包不冒称最终源码版本。
- 用户因WSL应用窗口卡住重启过WSL；恢复后重新核实进程与状态，未按历史PID操作。历史消息增长是已复现并修复的缺陷，但**尚无证据证明它是窗口卡住的唯一或直接原因**。图形验证窗口逐个启动。

后续先让用户完成当前可见大众点评登录，然后在原run读取门店/菜单/套餐来源；确有表单时按既有逐项审批准备。没有真实商家表单就明确停在只读范围，不用受控样本补齐。真实下单/预约提交/支付/外部消息仍需具体授权。实现/源码提交和最终包标识见本文件末尾交付记录。

## 1. 新对话先做什么

1. 阅读根目录 [AGENTS.md](../AGENTS.md)、本文件、[架构决策](架构决策.md)、[实施进度](实施进度.md)末尾“本轮收尾结果”及[Agent 架构与选型](Agent架构与选型.md)。按实际改动再读相关源码和验收 README。
2. 只读核对 Git、已有修改、Compose 归属、主桌面进程、租约、待命令及保留的测试数据。下面的服务状态只是带时间快照，重新查询才是当前事实。
3. 以第 7 节为下一阶段建议，先推进真实商家只读/准备适配与结构化需求交互，再做可安装试用交付。直接调查和实现可逆改动，不停在泛化分析，也不再询问“是否开始”。目标站点尚未由用户指定，应依据已有入口和真实可达性选取有界首站；需要用户特定账号或业务操作授权时才提出具体缺口。
4. 分阶段更新 [实施进度](实施进度.md)，写清改动、验证、未覆盖项。可本地提交，不自动 push、创建 PR 或发布。后续若用户改变范围，以用户新指令为准。

本文件优先于历史进度措辞。[旧实施交接](CODEX_实施交接.md)保留迁移前基线；进度和验收报告中的“正在修”“待复验”有些是当时的过程记录，应结合各自最终结论阅读，不能据此撤销已完成实现或重复施工。

## 2. 不得丢失的约束与授权

- 产品名 `PlanGo`，技术标识 `plango` / `PLANGO_`。主包 `backend/plango`，内部 Harness 为 `vendor/plango_harness/backend/plango_harness`。真实作者署名、许可证、固定上游来源、Alembic 历史 revision 和历史证据保持真实；搜索到旧名不等于运行时遗漏。
- 仅修改本仓库及其专属资源。**不得修改、借用或重启兄弟 `../Planora` 的仓库、配置、服务、测评，也不得修改、重命名或删除原 conda `planora`。** 本项目独立 `plango` 环境已经存在。
- 保留 Electron + React、真实可见浏览器、WebContentsView + 已验证的 Playwright/CDP、DOM-first 与按需 Vision。用户、模型、执行器共用同一会话、页面身份、审批、幂等和回执边界。禁止给模型或不可信 renderer 任意 JS/CDP/shell 能力。
- 保留数据库、任务、checkpoint、审批、幂等记录、UNKNOWN 回执、浏览器登录态与桌面身份。不能清库、重置 profile、重新授权或重放不确定写入来制造迁移/恢复成功。未经检查不得重启 worker；不并行启动旧、新消费者。
- 保留默认重庆和本项目已有模型/高德配置。`.env` 的值不输出、不提交；不要打印完整 `docker inspect` 环境或展开后的 Compose 配置。保留 `OPENAI_*` / `AMAP_*` 等供应商标准键。桌面模型配置检查不等于已更新独立 Docker 后端环境。
- 用户明确要求保留未跟踪原文 `docs/YOYU_Planora_融合方案.md`，**不修改、不删除、不顺手提交**。原 SHA256：`4e63df2f9bf469b142ce6fc03b321f04da8337bf6d0a9ad817433197dca3a70d`。
- 正常运行不使用 mock 补齐商家事实、价格、库存或业务结果。测试页面可受控，但必须标明，不可冒充真实网站。
- 适量真实模型和高德只读调用已获授权；常规可逆实现、必要测试和本项目服务操作可继续。**真实预约提交、下单、支付、发送消息等外部业务写入仍需具体授权**，准备表单授权不包含最终提交。
- 本轮完整质量指标测评已暂停。继续必要功能回归、失败定位和有界真实场景验证，不自行恢复角色收益、DOM/hybrid 等整批 sweep，不靠增加预算掩盖错误。
- 保留丰富产品入口、人工接管、审批、地图、发现、分享、历史、记忆和设置。集中式工作流继续演进，不为形式完整引入另一套 Agent/记忆框架、MCP 或调度系统。

本轮接续工作已按上述范围收尾；未完成的真实商家链继续按第0节处理。新对话不继承旧工具会话、PTY或子Agent状态，不依赖旧agent ID/PID恢复，也不要未经用户要求自动创建goal。

## 3. Git、环境和服务快照

本节保留上一轮08:10的历史快照；最新运行状态以第0节和实际查询为准。

| 项目 | 2026-09-09 08:10 状态 |
| --- | --- |
| 仓库路径 | `/home/song/code/Agent/multi-agent/PlanGo`；原 `YOYU` 目录已迁走 |
| 分支 | `feat/planora-browser-harness`，保留历史分支名 |
| 写本交接前 HEAD | `e1c6172 docs: record PlanGo delivery and verification`；本交接可能形成后续文档提交，以 `git log` 为准 |
| 最新实现提交 | `d6086c1`：P2/P3、现代桌面 UI 与验收证据；大量 diff 来自原始 JSON 证据，不全是代码 |
| 之前提交 | R0 `4a5bf11`；CI `4451aa4`；P0 `f845e9c`；P1 `15bb44b` |
| 工作区初查 | 仅用户融合原文未跟踪；无其他遗留代码修改。之后本交接相关文档会产生修改 |
| 主 Compose | `plango`：API、PostgreSQL、Redis healthy，worker 运行，migrate Exited 0 |
| 主 API | `http://127.0.0.1:8011`；数据库/role 为 `plango` |
| 主任务 | 共 8 条：6 `SUCCEEDED`、1 `REQUIREMENTS_READY`、1 `PARTIAL_FAILED`；有效租约或 pending_command 合计 0，未回执浏览器命令 0；`agent_action` 当前无行 |
| 主桌面 | **当前未发现存活的本仓库桌面进程**。上一轮启动、连接及重复 start 已验收；`desktop.json` 和 `DevToolsActivePort` 文件仍在，不能当作当前存活证据。需操作 UI 时在图形会话运行 `./start.sh` |
| 测试 Compose | `plango-e2e` 全部停止；API/worker 的 Exited 143 为之前主动停止，PG/Redis Exited 0。卷、profile、SQL 备份保留 |
| Python | `/home/song/miniconda3/envs/plango/bin/python`，3.12.14；原 `planora` 未改 |
| 浏览器已验证组合 | Electron 33.4.11 / Chromium 130 + `playwright-core` 1.63.0；版本变更需重新适配验证 |

初查命令（不输出配置秘密）：

```bash
cd /home/song/code/Agent/multi-agent/PlanGo
git status --short
git log -7 --oneline
docker ps -a --filter label=com.docker.compose.project=plango --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
docker ps -a --filter label=com.docker.compose.project=plango-e2e --format '{{.Names}}\t{{.Status}}'
docker exec plango-postgres-1 psql -U plango -d plango -Atc "SELECT phase,count(*) FROM agent_run GROUP BY phase ORDER BY phase; SELECT count(*) FROM agent_run WHERE lease_until>now() OR pending_command IS NOT NULL; SELECT count(*) FROM plango_browser_command WHERE result IS NULL;"
/home/song/miniconda3/envs/plango/bin/python --version
```

用 `scripts/lifecycle.py` 的只读 `desktop_processes()` 核实 cwd、父子关系、boot ID 和进程启动时间；不要仅凭 PID 文件、进程名或旧端口杀进程。主桌面 CDP 端口动态发现于 `~/.config/plango/DevToolsActivePort`，先核实端口归属；不要固定旧端口。`./start.sh` / `./stop.sh` 已有本仓库归属检查和保留卷逻辑，当前启动实际仍是 `npm run dev`。

## 4. 持久数据与恢复位置

| 数据 | 位置与注意事项 |
| --- | --- |
| 主数据卷 | `plango_postgres-data`、`plango_redis-data`、`plango_runtime-data`；不使用 `down -v` 或清卷 |
| 主桌面 | `~/.config/plango`，浏览器分区 `persist:plango`；Cookie DB 为 `Partitions/plango/Cookies` |
| 桌面身份与回执 | `~/.config/plango/harness/desktop-identity.json`、`browser-receipts.json`；保留原会话 ID、幂等及不确定回执 |
| 原迁移备份 | 私有 `output/r0-backup/`（目录 700），含原 `.env`、Compose、`postgres.sql`、旧卷冷备/清单、完整桌面 profile；不提交、不公开打印 |
| 真实 UI 隔离数据 | `plango-e2e` 卷 + `output/live-ui/profile/`；私有 SQL `output/live-ui/final-backup/plango-e2e.sql`（600） |
| 外部网页/图片隔离数据 | `output/live-browser-e2e/session-7sbvyC/`；原后端 55877 已停止 |
| 准备链隔离数据 | `output/preparation-e2e/session-plD5Ds/`；原后端 56427、受控页面 55887 已停止 |
| 主启动日志 | `output/lifecycle/setup.log`、`desktop.log`、`desktop.json`；日志可能含私有上下文，先筛选再引用 |

R0 迁移保留原 4 个用户任务、18 条浏览器命令、49 条 Cookie，16 张业务/checkpoint 表原始行；682 条真实 checkpoint 可读取，32 条旧模块路径经严格兼容转换。Redis 原 22 条消息及消费位置保留，非空 PEL 另有受控迁移测试。主库另有按测试用户归档的验收记录，不能用“必须仍只有 4 条”作为数据连续性标准。

最后主服务更新后，原始行及 49 Cookie、身份/回执再次核验完整，见 [连续性证据](../eval/plango-live-ui/original-data-continuity.json)与同目录 `desktop-data-continuity.json`。这是上轮验收事实，不代表每次读取本文时重新计算。`output/live-ui/check_original_rows.py` 依赖隔离测试 PostgreSQL，不能在测试服务停止时盲跑；恢复原 SQL 只能进入独立审计数据库，主库只读比对。

旧 YOYU 容器/卷及备份保留用于回滚，不重启旧消费者。历史 Alembic revision ID、上游标注和旧模块兼容白名单有意保留；不要机械追求旧名零匹配或扩大反序列化白名单。细节见 [PlanGo 迁移](PlanGo迁移.md)。

## 5. 已实现的架构与关键契约

架构是 **LangGraph 集中式 Agent Workflow：外层 Plan-and-Execute，浏览器内层有界 ReAct**。确定性协调器负责路由、预算、审批、证据和完成门槛；Requirement/Discovery/Planner/Advocate/Critic 等专业节点各有结构化输入输出。`single/multi` 是视角策略，不是两套拓扑；Advocate 可逻辑分支，共享模型锁仍串行调用。Compiler、Verifier、Memory、Queue 是服务，不把它们包装成自治 LLM Agent。

| 领域 | 已实现及继续修改时必须保持的边界 |
| --- | --- |
| 需求/规划 | `TripSpec` 规范字段与稀疏补丁；先规范化再计算依赖变化。起点、搜索中心、selected POI、活动类别和新地名分开。专业模型只读当前规范字段；累积 goal/messages 和旧报告保留审计，不把旧预算/旧人数重新带回当前生成 |
| 地理/事实 | 前后端共用高德规范 POI 入口；附近 5km 与城市搜索有明确区别。选店 canonical ID 服务端核验；过期身份按原 ID 实际刷新，日期变更实际查对应天气。失败保持旧时间并报告 unknown；高德身份不证明排队、库存、可订 |
| 路线 | 计划起点/交通模式传到地图与路线面板；walking 已有真实链路证据。路线失败时只能显示真实两点的直线下界及来源，不能伪装步行/驾车时长；后端公共交通覆盖仍有限，不能从前端 JS 公交能力推断已完整支持 |
| 草案/准备 | 持久 `draft_review` 绑定 exact plan/version/interrupt。保存产生 `draft_ready`；明确“仅准备表单”产生 `itinerary_preparation` Goal，并保留未核验事项。`ready_to_review` 只证明同一原生表单门店、人数、日期/时间与入口核对，`business_completed=false` |
| 修改/恢复 | 人数等变更产生新方案版本/审批；旧 Goal 不能继续用。原准备 Goal 的 PARTIAL 可显式 resume，重新读取；严格同 form 唯一字段差异可提出待审批 type，实际输入后需新快照验证。不得重放旧写命令或 UNKNOWN |
| 浏览器 | 主进程 WCV 管理与 Playwright/CDP 固定操作，同一持久 session；实际 popup 使用同 session 的可见原生 BrowserWindow，之前全 WCV popup 方案实测有挂起，不能未验证就改回。renderer 原始 eval/伪造回执 IPC 已移除 |
| Vision | 按需只读，每 turn 最多 1 次截图；当前本机显式启用，示例默认关闭。没有坐标点击/输入。快照、页面、时效、来源与授权共用边界；登录/验证码/拒绝/UNKNOWN 不得切换视觉绕过 |
| 回执/一致性 | Browser result CAS 与 `BROWSER_OBSERVATION` 同 SQL 事务；已保存回执不可变，重复传输不重复执行。迟到截图 ACK 可保留真实证据，但消费仍有时效/身份校验。Redis 死信发布和 ACK 原子化，发布失败不能 ACK |
| 预算/等待 | 每次明确新用户输入取得有界本轮 baseline；累计 tokens/tools 不清零。人工澄清/审批/浏览器等待保存剩余执行时间，自动恢复/重复命令不加预算；checkpoint 与投影间崩溃不重复补充额度 |
| SSE | 桌面主进程唯一订阅者，Last-Event-ID、严格序号、gap 重连、终态追平；恢复时即便快照未变也通知 UI 脱离离线。服务端 SSE 仍使用数据库轮询 |
| 记忆/反馈 | 明确偏好、幂等反馈和有来源/有效期的限定 episode 分开；可信读取/准备可投影经历。评分不推断偏好，UNKNOWN 不记履约；删除/清空的最小墓碑防止恢复复生。Embedding opt-in，禁用时不借用聊天密钥 |
| Skills/工程 | 本地 10 个 Skills 目录，按需只读正文，开关持久化并冻结新任务快照，不增加权限。当前产品无 MCP；沿用 Pydantic/Zod/共享 TS 协议，没有全量 OpenAPI 类型生成、OTel collector/exporter 部署或第二套框架 |

模型错误已区分鉴权、权限、配额、限流、连接、超时、服务/请求、结构化和内部错误；只有结构化错误做 JSON repair，不能把网络失败变成模拟成功。默认每轮 12000 tokens / 48 tools / 300 执行秒，模型 timeout 45 秒、retry 1，高德 timeout 8 秒；实际以本项目配置为准，不能为过关盲目扩大。

主要源码入口：

| 修改对象 | 入口 |
| --- | --- |
| API、运行与结果 | `backend/plango/app.py`、`runtime.py`、`outcomes.py` |
| 工作流、规范/地理编排 | `backend/plango/graph.py`、`planning.py`、`geo.py`、`world.py`、`location.py` |
| 核心状态与专业节点 | `vendor/plango_harness/backend/plango_harness/agent/{contracts,state,requirements,graph,model_adapter,subgraphs}.py`、`agent/subagents/` |
| 编译、事实、持久恢复 | 内部包 `domain/planning.py`、`providers/world.py`、`persistence/runs.py`、`queue.py`、`memory/` |
| 可见执行器 | `src/main/browserView.ts`、`browserDriver.ts`、`browser-bridge.ts` |
| 桌面传输/配置 | `src/main/harnessClient.ts`、`harness.ts`、`ipc.ts`、`config.ts`、`location.ts`、`skills/loader.ts` |
| UI 投影与交互 | `src/renderer/src/store.ts`、`lib/harnessProjection.ts`、`components/` 中 ChatPanel、OutcomeCanvas、DraftReviewCard、PlanMap、RouteSheet、SidePanel、SettingsDrawer、ResultFeedback |

现有 API 可优先复用：`POST /api/v1/geo/{search,geocode,reverse}`；创建 run 可带 selected_poi hint，服务端再做真实 ID 核验；`POST /api/v1/runs/{id}/draft-decision` 传 decision=`save|prepare` 和 interrupt/plan/version；`POST /api/v1/runs/{id}/preparation/resume` 传 plan_id/plan_version/approval_id；`POST /api/v1/runs/{id}/feedback` 是独立幂等反馈入口。修改前读实际 Pydantic 模型，不凭本文概述直接拼生产请求。

## 6. 已验证什么，尚未证明什么

| 证据入口 | 实际覆盖 | 不可扩大的结论 |
| --- | --- | --- |
| [真实桌面流程](../eval/plango-live-ui/README.md) | Electron + 真模型/高德 + 独立 PG/Redis；发现、规划与同 run 多轮修改、草案保存、真实步行路线、分享/投票/留言、明确偏好/反馈/遗忘、提醒、Skills 重启、API 断线恢复 | 没有证明任意要求/任意商家都成功；保留真实失败和修后记录 |
| [外部网页与图片](../eval/plango-live-browser/README.md) | 真重庆政府三峡博物馆网页读取、该网页截图经真实 filechooser 上传、导航/标签/缩放/常驻停止/历史恢复 | 网页为 `read_only`，用户图片为 `image_text`，不证明实时库存或业务写入；短暂“停止任务并接管”浮条未单独点击验收 |
| [原任务准备恢复](../eval/plango-live-preparation/README.md) | 真 Electron/模型/高德身份 + **本地受控原生表单**；原任务恢复、逐项审批 type，时间 18:30→18:31、人数 3→4，新版本/审批与 ready_to_review | 17 命令=12 extract+2 snapshot+3 type，其中 localhost type 被拒、两次实际执行；click=0、submit=0。`fixture.meituan.com` 仅测试 host 映射，不是真实美团预约 |
| `eval/plango-ui/` | 离线受控 UI 夹具与布局 | 不是真模型或真实商家流程 |
| `eval/plango-r0/`、`plango-p0/`、`plango-p1/` | 分阶段迁移、正确性和浏览器适配记录 | 历史阶段记录不代表所有最终能力；失败文件不覆盖 |

关键保留任务，继续复验时不要删除失败后另造“成功基线”：

- 规划原 run `381fe190c404442894f72b5ddb925524` 位于 `plango-e2e`：保留错误西安历史，修后完成重庆步行、预算 250、2026-09-10 14:00、真实天气/身份刷新、新起点解放碑，v6 保存；路线实测约 119m/2min。历史日期不应在未来冒充新的实时证据。
- 准备原 run `d51586748f934797a6c8b202ccd8df5b` 位于独立准备 SQLite：最终 v4，4 人/18:31，2 条待核验事项保留，`business_completed=false`。该诊断场景每轮 cap **60000**，累计 117705、最终轮 baseline 91945；未抬到 120000，不能把它当默认 12000 cap 的性能验收。
- 外部网页成功 run `6903a5625c244b82983989f5f268635e`、图片成功 run `7aca5ec4a79a44648a54a8681d3e081e` 位于独立网页 SQLite。

最近完整 `npm run check` 通过 **209 pytest + 43 subtests**、TS、存储/传输/分享与构建；随后计划起点/交通模式 UI 改动又通过类型/投影/构建、真实 Chromium 地图夹具与真实高德 UI。主桥真实浏览器 **76**、原生视图 **40** 项通过；Ruff、mypy（66 文件）通过。这是此前实施验收，本次文档交接未重跑业务测试。私有日志见 `output/live-ui/release-check.log`、`final-ui-build.log`、`browser-final.log`、`browser-view-final-retry.log`；失败日志仍保留。未 push，远端 Actions 没有本轮通过证据。

**尚未覆盖：** 真实预约提交/下单/支付/外部消息；逐商家 OAuth、验证码、闭合 Shadow 和交易回执；微信/飞书产品渠道；完整公共交通规划；完整质量与多角色收益对比；其他操作系统安装器、跨机器真实登录态恢复。普通 host Playwright 截图不包含原生 WCV，查看真实网页要用 native capturePage 或系统截图。

## 7. 建议下一阶段路线与验收

用户已授权按本节接续，最新完成范围见第0节。**大众点评是依据实见入口保留的候选首站，尚未取得登录后的商家证据，也未授权真实交易。** 原固定架构和数据约束继续有效。

### N1：一条真实商家只读到准备链

先检查已有入口、站点策略和当前可见会话，选择一个真实可达且有业务价值的首站。先完成真实门店身份/地址、菜单或套餐条件、来源和有效时间读取；确需登录时保留当前会话并提供人工接管，不复制 Cookie 到隐藏浏览器、不绕过验证码。基于真实页面缺口修改受信适配器和后验规则，再验证正常、失效和恢复路径。

验收至少覆盖：真实来源可追溯，门店一致，未知条件不编造；真实页面的准备参数与当前方案一致；修改人数/时间使旧审批失效；重启能恢复原任务，已发写命令/UNKNOWN 不重放。未取得业务写入授权前，停在经批准的表单准备/待核对范围，报告尚未完成交易。站点不提供可操作表单时明确交付只读范围，不用本地样本冒充补齐。

### N2：结构化需求确认与编辑

此项已实现并做真实同run验证。需求卡通过 `POST /api/v1/runs/{id}/requirements` 提交 `expected_version`、稀疏fields及可选精确stop_lock，复用TripSpec/版本/依赖刷新，不另建事实状态。扩大/缩小/取消范围均实际刷新搜索，地理失败后的人工澄清保留其余字段。

验收用同一持久任务：编辑指定字段后，界面与后台规范一致；只刷新受影响事实/路线，仍有效的门店身份和无关字段保留；取消预算不会恢复旧金额；修改日期查询对应日期的真实天气；锁定保留正确站点但不能冻结失效证据；旧方案/审批明确失效，历史保留。

### N3：可独立安装的试用版本

Linux/WSLg试用路径已实现并验收。源码 `start.sh` 保留开发入口，已安装试用版依据持久安装身份直接启动包内Electron及编译页面，后端从包内固定源码构建；使用 `scripts/trial.py` 管理安装/诊断/冷备/恢复/升级，不依赖兄弟仓库或主机conda。见试用安装文档。

验收包括：不依赖 Vite dev server 或兄弟仓库的启动路径；配置校验与无密钥诊断；持久数据备份/恢复；已有 profile/数据库升级和独立干净安装两条路径；版本说明、停止/重复启动与资源归属。不要把主用户现有 profile 当干净安装样本。

### N4：基于实际日志缩短用户等待

在首站和需求卡稳定后，用实际 trace 定位重复地理刷新、冗余模型调用与无效循环，做小范围对照和固定真实用户回归集。全量质量测评仍待用户恢复范围。MCP、新 Agent、OpenAPI 生成或 OTel 只有出现明确收益和需求时再引入。

当前需求卡与Linux试用交付已具备上述证据；**一个站点登录后的真实只读/准备链仍未交付**。不能把局部工程完成或受控恢复验收标为全项目完成。

## 8. 验证命令与操作注意

按改动选择检查；文档改动不必重跑真实模型。完整常规检查和专门浏览器检查是两组，`npm run check` 不包含下面全部 Electron 回归：

```bash
npm run check
/home/song/miniconda3/envs/plango/bin/python -m ruff check backend vendor/plango_harness/backend/plango_harness
/home/song/miniconda3/envs/plango/bin/python -m mypy backend/plango vendor/plango_harness/backend/plango_harness
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:browser
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:browser-view
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:desktop
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:map-title
```

需要完整部署链时，先确认 18011 和保留的 `plango-e2e` 资源归属、待任务，才启动它；当前测试数据不是空库。测试脚本会重启测试 API/worker，只允许隔离 URL/Compose：

```bash
PLANGO_SERVICE_PORT=18011 docker compose -p plango-e2e up --build -d --wait
PLANGO_TEST_BACKEND_URL=http://127.0.0.1:18011 PLANGO_TEST_COMPOSE_PROJECT=plango-e2e env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:deployed
PLANGO_TEST_BACKEND_URL=http://127.0.0.1:18011 PLANGO_TEST_COMPOSE_PROJECT=plango-e2e env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:deployed -- --vision
```

这两条部署测试分别使用受控菜单/Canvas，调用真实模型，报告默认保存到带时间的 `output/full-stack-smoke/`；不能称真实商家交易。不要移除脚本的主服务拒绝/Compose 归属预检，也不要覆盖旧 `eval` 报告来制造全绿。

新一轮交付时更新本文件和接手提示词的当前状态，保持 [实施进度](实施进度.md) 可追溯；明确代码完成、实际验证、未覆盖和运行状态分别是什么。
