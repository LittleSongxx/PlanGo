# PlanGo 当前实施交接

更新：**2026-09-09，D3–D6接续交付及D7有界调查**。工作区：`/home/song/code/Agent/multi-agent/PlanGo`。

## 0. 当前目标与下一步

用户希望新 Codex **按最近讨论的“项目本身推进计划”直接实施**，不要重新输出泛化分析或询问“是否开始”。主线是：用户选择门店与优惠 → 修改人数/日期/预算 → 系统解释适用性与缺失规则 → 保存行程 → 中断后继续原任务。

**D1/D2已提交，D3/D4已在原真实门店任务完成选择→修改→保存→重启；D5已做单站输入收窄与180秒循环，D6已实现独立距离语义、有限同城公交及费用范围。** 用户随后要求修复聊天只剩最后回复并改明黄色主题；本轮继续实现持久回复事件、按轮次恢复和黄色/暖白配色。下一步是D7真实表单/独立使用验收，以及有实际长会话样本时继续D5稳定性；不能把180秒循环算长期稳定或已解释WSL崩溃。复用已有持久命令、规范补丁、证据和审批机制，按小阶段验证与本地提交；不要重复首批实现或为了形式统一重建架构。

用户主投方向已确认 **AI应用／Agent开发**；[秋招与作品集建议](秋招与作品集推进建议.md)是辅助背景，不能据此把本次产品实施改成写简历或发布作品集。20个评估任务、3–5个独立试用者等仍是建议数量，尚未执行；**完整质量指标测评继续暂停**。

本文件取代此前交接的当前状态与后续优先级。原文完整保存在[门店/UI交付归档](CODEX_HANDOFF_2026-09-09_门店UI归档.md)，迁移前基线在[更早交接](CODEX_实施交接.md)。旧文“尚未登录”“继续N2/N3”及进度中的过程性待办不代表当前任务。

## 1. 不可丢失的约束

- 只修改本仓库及归属明确的专属资源。禁止修改、依赖、重启兄弟 `../Planora` 的仓库、服务、配置、测评和原 conda `planora` 环境；固定上游只通过本仓库 `vendor/plango_harness`、来源清单及显式维护参考。
- 产品名 `PlanGo`；自有技术标识 `plango` / `PLANGO_`；默认重庆，保留本项目模型与高德配置。R0更名/目录迁移/独立conda已完成，**不要重新创建环境、迁目录或重建空库**。
- `.env` 密钥不输出、不提交。不打印完整容器环境或展开后的Compose配置；保留供应商标准 `OPENAI_*` / `AMAP_*` 键。原作者、许可证、上游来源和历史证据保留。
- 用户原文 `docs/YOYU_Planora_融合方案.md` 必须保持未跟踪，不修改、删除或提交。SHA256：`4e63df2f9bf469b142ce6fc03b321f04da8337bf6d0a9ad817433197dca3a70d`。本次新增的秋招分析文档不属于该保护原文。
- 保留 Electron + React、真实可见浏览器、WebContentsView、经验证的Playwright/CDP、DOM-first及按需只读Vision。UI、模型、执行器共用会话和Harness授权/回执边界，不能给模型或renderer原始任意JS/CDP/shell权限。
- 集中式LangGraph工作流，外层Plan-and-Execute，浏览器内层有界ReAct。single/multi是视角策略，不扩为无需求的自治多Agent团队；当前没有产品MCP，不为关键词引入新框架、OpenAPI全量生成或OTel部署。
- 数据库、任务、checkpoint、审批、幂等、UNKNOWN回执、浏览器登录态完整保留。不清库、不重置profile，不以新建任务、重新授权或重放不确定写入制造成功。重启worker前检查租约/待命令，不能同时启动两个消费者。
- 常规可逆实现、必要检查及适量真实模型/高德只读调用已有授权；**真实预约提交、下单、支付和外部消息仍需具体授权**。准备授权不包含提交，每项页面输入沿既有审批；不得自动解决验证码。
- 测试使用归属明确的独立profile/数据库；标明受控样本与真实网页。保留旧失败和累计用量，不提高预算掩盖错误，不把正确暂停当业务完成。
- 用户要求测试中顺手改善不友好的UI、美观与可读性。**临时窗口不用时及时关闭**；需要用户扫码/验证码/审批时明确说明。关闭窗口保留profile/任务，不实现产品空闲自动关闭。
- 本地阶段提交可继续；不自动push、创建PR、发布网站/安装包、联系试用者或投递简历。不把代码、录屏或测试数量当个人独立原创、任务成功率或生产SLA。

## 2. Git、资源与持久数据（重新查询后操作）

| 项目 | 本次只读核实 |
| --- | --- |
| 分支 | `feat/planora-browser-harness`，历史分支名保留 |
| 本轮接手HEAD | `f8eb546 docs: hand off the next PlanGo product implementation stages`；D1/D2阶段提交以本文件对应Git记录为准 |
| 最新实现 | D1/D2=`34eb7d0`；D3–D6见本文件对应Git提交。优惠适用性/同任务选用、单站输入收窄、距离/公交/费用已实现；此前`a05f037`、`87f18e9`、`d6086c1`均保留 |
| 初查工作区 | 无代码修改，仅用户融合原文未跟踪；没有重做更名、环境、安装或商家登录 |
| 主Compose | `plango`，目录标签归属本仓库；API/PG/Redis healthy，worker运行，migrate Exited 0 |
| 主API/数据库 | `http://127.0.0.1:8011`；database/role=`plango` |
| 主任务 | 8条：6 SUCCEEDED、1 REQUIREMENTS_READY、1 PARTIAL_FAILED；有效租约或pending_command=0，未回执浏览器命令=0，RUNNING/UNKNOWN动作=0。终态包含只读/草案，不表示6次交易 |
| 桌面/隔离进程 | lifecycle未发现主桌面；商家、需求验证、trial目录未发现活跃进程。不要依据旧PID/CDP文件操作 |
| 测试Compose | `plango-e2e`停止，API/worker Exited 143是先前主动停止；`plango-trial-check`、`plango-trial-restore-check`无容器，卷/profile/备份保留 |
| Python | 已有 `/home/song/miniconda3/envs/plango/bin/python`（3.12系列）；所有后端入口使用独立plango环境 |
| 浏览器版本 | Electron33.4.11 / Chromium130 + playwright-core1.63.0；升级须重新适配验证 |

数据位置：

| 数据 | 路径/边界 |
| --- | --- |
| 主卷 | `plango_postgres-data`、`plango_redis-data`、`plango_runtime-data`；禁止 `down -v` |
| 主profile | `~/.config/plango`，`persist:plango`；Cookie在 `Partitions/plango/Cookies` |
| 主桌面身份/回执 | profile下 `harness/desktop-identity.json`、`browser-receipts.json`，不能替换成新身份 |
| 原迁移私有备份 | `output/r0-backup/`；含配置、SQL/卷/profile，不提交、不公开打印 |
| 最近主库更新备份 | `output/conversation-final/before-main-update.sql`、`before-main-rows.json`（回复持久化更新）；D3–D6的`output/product-final/`和D1/D2的`output/delivery-next/`和此前备份照常保留 |
| 原真实PG/Redis测试 | `plango-e2e`卷、`output/live-ui/profile/`、`output/live-ui/final-backup/plango-e2e.sql`；当前停止，不是空库 |
| 商家原任务 | `output/merchant-next/session-C0ovDv/`，数据库 `data/runs.sqlite`、profile `electron/`；原后台端口已停，不固定端口恢复 |
| 结构化需求原任务 | `output/requirements-next/session-kfOEZ5/`，`data/runs.sqlite`与独立profile，已停止 |
| 受控表单原任务 | `output/preparation-e2e/session-plD5Ds/`；保留受控表单、失败/取消运行及profile，已停止 |
| 安装/恢复样本 | `output/trial-check/`及专属卷；受控UNKNOWN/审批/checkpoint/Redis PEL与Cookie均保留 |

当前可继续复核的原run：

- `f8f8ad2719074b0a9c62cbb08367892f`（商家SQLite）：**SUCCEEDED/draft_ready，v4，36243tokens/28tools**，无pending/lease/UNKNOWN。原12152/5的完整菜单读取失败保留，后续原第6轮通过“已处理，继续”完成窄范围门店/地址/3优惠读取；明确选47元代金券（面值50），canonical POI `B00178UE0C`。v4为2人、2026-09-11 18:30、120分钟、预算250、搜索半径0.5km/单段路程2km/步行。真实路线0.255km/4分钟，交通0、餐饮估算120；优惠未抵扣且旧来源已过期，完整规则/供给仍未知。起点是验证者手动设定的真实高德观音桥站坐标，非设备GPS。实际重启后规范、优惠引用和累计用量不变；原profile保留且窗口/后端已关闭，不再扫码。开发验证不等于独立用户采用或商家履约。
- `de59413d26ff4b1a958d5d607fc4c6a2`（需求SQLite）：SUCCEEDED/draft_ready，v5，14514tokens/164tools，无pending/lease；预算取消、人数修改、单站锁定/解锁、起点/搜索中心、3km、2026-09-11 15:00与驾车均有真实UI/高德记录，经历WSL重启。
- `381fe190c404442894f72b5ddb925524`（停止的plango-e2e）：保留错误西安历史，后续恢复重庆步行/预算/日期/起点，v6保存。历史天气/日期不能当新的实时事实。
- `d51586748f934797a6c8b202ccd8df5b`（受控准备SQLite）：SUCCEEDED/ready_to_review，v4，117705tokens/53tools，无pending/lease；是本地受控表单，submit=0，不能当真实预约。其历史60000/轮cap只用于当时诊断，不沿用到新测试；同库其他取消/失败记录保留。

## 3. 已完成的能力与必须保持的契约

| 领域 | 已实现/验收边界 |
| --- | --- |
| 规范/地理 | TripSpec稀疏补丁、先规范化再依赖刷新；未提及/清除/未知区分；真实起点、搜索中心、selected POI分开；详情按原ID核验，过期失败不续时间；日期用持久时间锚点 |
| 需求UI | 起点/中心/范围、日期/时间、人数、预算、交通直接编辑；单站锁定/解锁，expected_version与plan/version/place校验；旧审批失效，历史保留 |
| 草案/准备 | draft_review绑定exact plan/version/interrupt；save为draft_ready，prepare仅生成保留未知项的准备Goal；原生同form参数核对才ready_to_review，仍未提交；原Goal可显式恢复，UNKNOWN不可重放 |
| 浏览器 | 主进程WCV及受信固定操作；popup用同session可见BrowserWindow（全WCV popup曾挂起）；renderer原始eval/伪造回执入口已移除；只读Vision每轮最多1次，无坐标写操作 |
| 读取范围 | 复合请求逐字段验收，当前页需同URL/snapshot；推荐菜、优惠预览不等于完整菜单/规则。短页保留正文；大众点评实见版式只读适配，同实体原文与有限数值校验，面值/原价不当售价；processed共用持久写入，旧结果不改 |
| 可靠性 | 回执CAS与BROWSER_OBSERVATION同SQL事务；Redis死信发布/ACK原子；SSE单主进程订阅、序号/缺口/重连；已保存回执不可变；新旧观测按时间/snapshot而非数组追加顺序显示 |
| 预算/恢复 | 每次明确新输入有有界baseline，累计不清零；澄清/审批/浏览器等待保存剩余执行时间，重复恢复不补预算；消息ID绑定run/turn，只追加本轮，旧重复历史保留 |
| 记忆/反馈 | 显式偏好、反馈、有来源/有效期的episode分开；不从点赞推偏好，UNKNOWN不记履约；删除墓碑防恢复复生；embedding opt-in，不借聊天密钥 |
| UI/安装 | 默认1800×1120并适配屏幕；资料/优惠/未知项分层，原文折叠、键盘可展开、反馈后置；Linux/WSLg独立安装、非开发启动、诊断、升级/冷备恢复已有证据 |

默认每轮12000tokens/48tools/300执行秒，模型timeout45秒/retry1，高德timeout8秒；实际以本项目配置为准。历史完整菜单读取的一轮3343tokens后出现model_token_budget fallback，**baseline正确**：保守输入/schema/system/skills估算及收尾预留触发，不能误当累计上限错误或通过调大额度掩盖。已有跨累计cap新轮回归。

用户多次报告WSL崩溃，并说明三个项目同时运行；2026-09-09 15:25后核实swap已扩为64GB（当时未用），内存约16GB可用。仍串行检查、一个临时图形窗口，不操作其他项目。历史消息倍增已修复，但**未证明是崩溃直接原因**；本轮180秒循环也不证明长期无泄漏。重启后必须重新核对归属，不使用旧PID/CDP。

## 4. 现有验证与交付

上一轮门店UI交付完整npm check为263pytest+43subtests、TS/存储/传输/分享/构建通过；追加预算回归后全量pytest为**264+43subtests**，Ruff/mypy67文件、真实浏览器**80项**及隔离完整桌面/键盘交互通过。9场景54条归档断言核对冻结证据中的记录与字段值，不执行新业务，也不是54次新业务成功。

以上为上一轮门店交付基线。本轮D1/D2完整npm check通过279pytest+43subtests、TS及构建；追加内容指纹后全量pytest最终**280+43subtests**，最终类型、传输、Geo客户端、Ruff/mypy69文件与构建通过。实际Electron集成包含接受后断响应→原请求只读取回（POST=1）；8MB图片两次独立进程恢复、服务配置/诊断/旧服务/缺key/离线界面通过。双runtime PostgreSQL并发创建/消息/暂停各12请求仅1次首次接受，旧行/UNKNOWN保留。详见[首批证据](../eval/plango-delivery-next/README.md)；均未执行真实模型/商家交易，未恢复完整质量测评。

主API/worker已更新并迁移至0013。更新前无租约/待命令/UNKNOWN且无主桌面，私有SQL备份后升级；原16张业务表1413行全部保留（另1张迁移版本表按预期更新），主Cookie文件/桌面身份/浏览器回执哈希保持。更新后API/PG/Redis健康、worker运行、未决计数仍0；没有打开主profile。独立e2e只临时启动PG做并发验收，现已停止，v1–v4测试数据库和失败日志保留。

- [真实门店/UI](../eval/plango-merchant-live/README.md)、[读取数据](../eval/plango-merchant-live/read-result.json)、[主数据连续性](../eval/plango-merchant-live/main-data-continuity.json)。主服务更新时17表1414条原始行、49Cookie及身份/回执保持是有时间的历史验收，本次未重新逐行计算。
- [需求/安装/接续](../eval/plango-next/README.md)、[安装验收](../eval/plango-next/trial-install.json)：17表/72profile文件、受控Cookie/UNKNOWN/审批/checkpoint/Redis PEL恢复；不是跨机器真实登录验收。
- [原UI](../eval/plango-live-ui/README.md)、[政府网页/图片](../eval/plango-live-browser/README.md)、[受控准备](../eval/plango-live-preparation/README.md)。实际字段、拒绝和旧失败保留。
- 最新日志：`output/merchant-live/pytest-delivery-final.log`、`check-final-recovery.log`、`browser-security-gate.log`；更细失败/复验在同目录，内容可能私有，先筛选再公开。
- 新包 `release/plango-0.1.0-linux-x64-merchant-ui.tar.gz`，112828176字节，revision=a05f037/source_dirty=false；SHA256 `c30b58a3a362912c023ae318173f84a6986a7efb6689439acb9659fa52835640`。旧包 `release/plango-0.1.0-linux-x64.tar.gz`保留。包与sidecar存在；[包记录](../eval/plango-merchant-live/package.json)、[使用文档](试用安装.md)。不覆盖旧包，也不声称每次重打包都重跑安装全链。

本轮D3–D6完整 `npm run check` 最终**375pytest+43subtests**，TS/存储/正确性/UI/传输/分享/构建通过，Ruff及mypy70文件通过。真实原任务v4及重启、独立高德公交读取、180秒实际Electron循环分别留证；循环156次、52个临时标签全部关闭、无新增业务POST，不能当生产稳定性或质量成功率。详见[产品接续证据](../eval/plango-product-next/README.md)。

主API/worker已更新至本轮源码，迁移版本仍0013；静止并停本项目消费者后私有SQL备份，18表1414行（其中17业务表1413行）及主Cookie/身份/回执文件保持。更新后API/PG/Redis健康、worker运行，未决计数0；主profile未打开。旧试用包不包含D1–D6新增功能；打新包时使用独立文件名，不覆盖旧包，也不重做安装全链。

聊天/黄色主题追加验证：类型、UI/传输/分享/存储、Ruff/mypy70文件、构建和实际Electron桌面回归通过。后端全量378通过+43subtests，1项旧末事件断言失败；更新为核对回执与助手回复后定向5项通过。原v4恢复13条用户输入/8条已有回复，刷新保留阅读位置，用量不变；[新界面](../eval/plango-product-next/21-yellow-conversation.png)。未编造未记录的历史答复。

## 5. 已授权接续的产品实施顺序

这是用户确认的产品实施路线；**D1–D4和D6已有实现及验证，D5仅有界完成，D7真实表单与独立使用仍待落地**。故障注入与真实业务验收分别报告，不能当作已经发生的用户事故。求职展示作为顺带证据，不取代本路线。

### D1 / P0：发送失败与断线恢复（第一项）

已实现：草稿和单个未决发送保留在原profile，图片用原生IndexedDB附件引用；固定request_id与内容指纹贯穿renderer/main/API。main先持久固定正文、图片、选店、位置、Skills及服务地址，再POST；接受后只GET取回。服务端`input_acceptance`与创建/命令/绑定/预算同事务持久，重复接受跨完成/重启仍返回原回执。同文新轮独立，内容冲突、当前租约/取消/pending/UNKNOWN均受事务围栏保护。旧服务无协议声明时不POST，保留草稿；0012→0013迁移不清旧数据。

上述风险已通过独立故障注入复现并修复，不是主用户数据事故。另复现并修复迟到replan覆盖已接受pending命令，以及409响应丢失后仅按ID误认旧内容；后者由持久内容指纹校验阻断。未送达可返回草稿，未知送达不可静默丢弃或改用新请求身份；重启先只读核对。

先读：`src/renderer/src/components/ChatPanel.tsx` → `src/renderer/src/store.ts` → `src/preload/index.ts` / `src/shared/types.ts` / `src/main/ipc.ts` → `src/main/harnessClient.ts` → `backend/plango/app.py` / `runtime.py` → `vendor/plango_harness/backend/plango_harness/runtime.py` / `persistence/runs.py`。

先复现再改；复用现有持久命令/本地存储/恢复入口，必要时补稳定请求身份和服务端幂等接受，**不要再造一个队列或只靠按钮防抖**。清楚表示未送达、已接收待取回、送达待核实；保留可恢复草稿。

验收覆盖：请求未到服务端、接受后响应丢失、POST成功GET失败、重复点击/重连/桌面重启、相同文本不同轮次、图片与选店入口。一次请求只能形成一次接受/一个run或一个用户轮次/一份预算；相同文字的独立新输入不能被文本去重吞掉；未知业务动作不能通过“重试消息”获得重放权限。保留原记录，新增字段需兼容旧DB/checkpoint并在隔离库验证升级。

### D2 / P0：实际模型配置与能力入口（与D1形成首批交付）

已实现：设置和连接入口共用执行服务卡，显示安全服务来源/归属、API配置模型、最近任务实际模型记录、key配置布尔值、DOM/Vision/图片/高德/公交能力边界。明确区分桌面文本测试、主动服务文本诊断与任务模型可用性；独立worker当前配置未单独核对时明确注明。健康检查不调用模型，诊断有界且只由按钮触发。快捷示例改为规划、只读优惠及草案分享；不覆盖独立Docker环境，不展示密钥片段或带凭据URL。

入口：`SettingsDrawer.tsx`、`SidePanel.tsx`、`ChatPanel.tsx`、`src/main/{config,harness,ipc}.ts`、`docker-compose.yml`、`backend/plango/settings.py`、`app.py`。扩现有设置/健康入口，不开第二套配置真相。

验收：普通用户能看懂当前任务由哪个服务、哪个模型/能力执行；本地和Docker两路径、缺key/错误模型/断网/未启用Vision分清。只显示必要安全摘要，不泄露key或带凭据URL；不能将ready或本地ping当作任务模型可用，更不能静默覆盖独立Docker配置。示例优先可完成的规划、只读与草案，保留丰富入口和自由输入；App-only时给已取得/缺失/下一步，不重复要求扫码。

### D3 / P1：从读到优惠到辅助选择优惠

已实现：`offers.py`从单个持久页面按人数/日期/预算确定性比较，来源/条目hash及过期约束保留；`OfferComparisonCard`分开面值、售价、原价和完整费用未知。真实2→3人使双人餐不适用；纯比较不调用模型，进入行程的需求编辑会replan。以下为持续验收契约。

先做同店2–3个优惠，按已确认人数、日期、总预算显示适用/不适用/规则缺失及来源。50元券售价47元分清；双人餐98元不能在3人时直接按98÷3认定够用；券叠加、超出人数收费、有效期和节假日限制未知时保留未知。只比较已知同口径费用，不因平均价格或推荐菜名凑出完整单点清单。

验收：人数2→3、日期/预算变更实际重算；缺规则/过期/异店/面值与原价混淆均有反例；提供带来源的选择依据，不能升格为可预约或已购买。不新增交易权限。

### D4 / P1：已读门店/优惠到行程的显式衔接

已实现：原run的`merchant-candidates`核对真实高德，`offer-selection`绑定expected_version、command/artifact、条目索引/hash与canonical POI；用户明确核对两个名址后写同一TripSpec.selected_offer。异品牌、冲突门牌/楼层不因人工确认绕过。新读取可更新比较卡，不会静默替换已选优惠。真实原任务已完成选择→编辑→v4保存→重启；旧来源过期仍显示未知。

复用 `selected_poi`、TripSpec、计划版本、证据和draft机制；不得把所有打开网页静默混入高德结果，不建立第二套事实状态。验收同一任务选择→改人数/日期→重新核验→保存→重启恢复，旧审批失效，缺失费用/规则仍明示。

### D5 / P1：减少等待与长时稳定性

本轮已有窄场景实现：单站无额外活动且无需重新发现时，只向规划传相关候选/证据；真实改人数样本21→1候选、23→3当前证据，新增4715tokens/4tools。身份/天气/路线刷新仍保留；无严格对照，不报告提速比例。180秒循环156次、52临时标签关闭、无新增POST/采集到的崩溃/无响应/监听器增长。长时稳定性仍待实际样本。

持续依据[真实回归日志](真实用户回归.md)选窄场景：单站已锁定且只改人数时，不应无条件刷新全部候选；每次只传当前相关证据，压掉重复正文/quote/skills上下文，避免相同页面无收益提取。保留必须的过期身份、日期天气、路线及未知项核验。

先固定输入/版本/模型/预算/冷热缓存口径，再小范围对照真实调用数、tokens、端到端等待；保留人工等待和失败，不扩大成暂停中的质量sweep。WSL卡住、长会话、反复切换任务/标签、监听器/资源回收单独复现，不猜原因、不以调高预算过关。

### D6 / P1：路线可行性与费用口径

已实现：TripSpec.search_radius_km与max_distance_km独立；新API route_distance_km明确路程口径，旧字段/旧checkpoint兼容。多结果geocode拒绝取首项；同城公交核对两端citycode、按真实bus/walking段和时刻查询，缺票价保留null；跨城/真实铁路/出租车段仍未知。真实独立读取观音桥站→解放碑返回31分钟/6.206km/3元每人，单人标准票价在规划只乘人数一次；初次空铁路容器误判失败保留。

预算明确餐饮、活动、交通和未估费用，不能把仅餐饮估算展示成全部支出保证。验收不同起点/搜索中心、交通变更、不可达/路线缺失、有限天气日期，保留真实直线下界与unknown，不能补模拟时长或车费。

### D7 / P2：真实表单与独立使用验收

本轮已对原店大众点评、高德详情和携程信息页做有界调查，**未找到已验证可操作原生网页预约表单**，不是证明网上不存在；详见[调查记录](../eval/plango-product-next/d7-form-investigation.md)。没有输入/提交交易或发送邀约，独立用户验收尚未发生。

后续先找到一个真实可操作网页表单再适配；当前大众点评页面仅公开预览，不无限撞登录/风控，不编造预订URL。复用同form参数、plan/version审批与恢复；用户具体批准输入后核对，submit=0；真实提交/支付仍另行授权。找不到就按只读范围交付，不用fixture假装商家验收。

在用户配合下让未参与开发者完成安装和2–3个实际任务，记录并顺手修UI/说明卡点；不代发邀约，不把开发者自己测试算用户采用。其他平台安装器按真实受众需要决定，现有Linux交付不重做。

## 6. 新会话首轮应落地什么

1. 读根AGENTS、本文件、架构决策、Agent架构与选型、进度末尾；只读核实本文件第2节的归属与待任务。
2. 不重做D1–D4和D6：先查看产品接续证据与原商家v4；保持当前优惠来源过期提示及原任务/预算，恢复显示不新增模型调用。
3. D7需用户提供实际可用表单或在新的具体入口上有界调查；具体输入沿逐项审批，提交另行授权。原店规则需App，不重复扫码/无限风控探测。独立用户验收等用户安排真实使用者，不擅自联系。
4. D5长时稳定只在实际长会话或复现线索下继续；64GB swap不证明WSL根因解决。多个项目运行时测试串行、单窗口，不自动开启sweep或长期压力任务。
5. 保持故障恢复/版本/UNKNOWN契约；通过必要检查后更新进度/交接/使用文档并本地提交。旧安装包没有新功能，需要试用时按已有pack/upgrade流程生成新文件，不重做环境或清库。

## 7. 启停和验证入口

归属检查不要打印环境：

```bash
git status --short
git log -7 --oneline
docker ps -a --filter label=com.docker.compose.project=plango --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
docker exec plango-postgres-1 psql -U plango -d plango -Atc "SELECT count(*) FROM agent_run WHERE lease_until>now() OR pending_command IS NOT NULL; SELECT count(*) FROM plango_browser_command WHERE result IS NULL;"
```

用 `scripts/lifecycle.py::desktop_processes()` 的cwd、父子关系、boot ID/开始时间核实。`./start.sh` / `./stop.sh` 有归属保护且保留卷；源码start仍开发入口，试用安装依据trial-install.json走包内Electron。浏览器CDP端口动态生成于对应profile/DevToolsActivePort，文件存在不证明进程存活。旧output中的交互driver脚本可能写固定eval文件、重用私有配置，**先读后用，不能盲跑或新开同库第二消费者**。

按变更选择检查，不为文档改动重跑模型：

```bash
npm run check
/home/song/miniconda3/envs/plango/bin/python -m ruff check backend vendor/plango_harness/backend/plango_harness
/home/song/miniconda3/envs/plango/bin/python -m mypy backend/plango vendor/plango_harness/backend/plango_harness
npm run test:transport
npm run test:ui
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:desktop
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:browser
python scripts/check_lifecycle.py
npm run test:trial
python scripts/inspect_trace.py --check
```

D1关注 `scripts/check-harness-transport.ts`、`check-harness-ui.ts`、`backend/tests/test_{turn_budget,message_history,queue_delivery,receipt_atomicity,structured_requirements}.py` 并新增必要失联接受回归；D2沿已有配置/模型连接检查扩展。`npm run check`不包含全部Electron回归。

需要部署链时先核对隔离资源、端口和待命令，再显式连接专属测试项目；不要绕过脚本主服务拒绝检查：

```bash
PLANGO_SERVICE_PORT=18011 docker compose -p plango-e2e build api
PLANGO_SERVICE_PORT=18011 docker compose -p plango-e2e up --no-build -d --wait
PLANGO_TEST_BACKEND_URL=http://127.0.0.1:18011 PLANGO_TEST_COMPOSE_PROJECT=plango-e2e env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:deployed
```

它使用受控页面及真实模型，可能重启隔离API/worker；不代表真实交易。共享镜像先build api一次再up --no-build，避免此前并发导出同标签竞争。对当前主服务更新也须先确认静止、备份、保持数据连续性，不能拿主profile做干净安装测试。

新证据使用新目录/文件名，公开材料脱敏；原始SQL/profile/配置留私有output。完成后关闭临时窗口与专属测试进程，保留数据，最后核对Git与用户原文哈希。**D1–D4/D6已有代码及相应受控或真实只读验收；D5长时稳定、D7真实表单/独立用户仍未完成。**
