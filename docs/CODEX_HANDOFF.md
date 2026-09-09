# PlanGo 当前实施交接

更新：2026-09-09，30例质量基线交付后。仓库：`/home/song/code/Agent/multi-agent/PlanGo`。写本次交接前HEAD：`bed0aac`；之后可能只有交接文档提交，以实际Git为准。

## 0. 新对话应接什么

**当前主任务：根据已完成的30例受控评测，实施系统层面的质量优化，再做有界回归与新的独立评测。** 直接落实工作，不仅输出分析，不再询问“是否开始”。本次最后只写交接，没有启动下一轮优化或模型复测。

先读本文件，尤其第1节约束、第4节基线、第5–7节优化与复测入口；再读根[AGENTS.md](../AGENTS.md)、[架构决策](架构决策.md)、[Agent架构与选型](Agent架构与选型.md)、[质量协议](质量评测协议.md)、[30例报告](../eval/quality-v2-30/README.md)及[实施进度](实施进度.md)末尾。简版入口是[接手提示词](CODEX_接手提示词.md)。

用户长期方向是**秋招AI应用／Agent开发岗位**。项目需要可演示、可解释、可复验的工程与质量证据；当前优先改产品和评测，不改成只写简历。用户没有指定80%、90%或100%目标，不能自行设好看分数倒逼改题、删失败或放宽标准。

产品主线：选门店与优惠 → 改人数/日期/预算 → 给适用性与缺失规则依据 → 保存行程 → 中断后继续原任务。D1–D4/D6已有实现与局部验收，**不等于新表达、新资料下已可靠泛化**。D5长时稳定、D7真实表单和独立使用验收保留为支线，当前不要回去反复找商家入口或扫码。

[门店/UI归档](CODEX_HANDOFF_2026-09-09_门店UI归档.md)、[迁移前交接](CODEX_实施交接.md)及本文件旧Git版本保存历史。“尚未登录”“需求卡待做”“安装待做”“所有质量测评暂停”“尚无百分比”等旧状态不适用本轮。

## 1. 用户需求、偏好与授权边界

- **反对局部过拟合。** 从宏观泛化与系统设计修复，不为每个badcase加独立补丁、问法关键词、门店名或题号规则；允许修复会影响全系统的共享契约小缺陷。改动应能说明共同根因、统一职责和反例验证，不是把复杂规则搬到另一文件。
- **质量只保留TSR和Groundedness两个主指标，Token另记。** 用户已要求“直接评30例新案例”，并明确“你帮我新开一个独立的子Agent代替我审核吧”。此次审核已完成；后续延续独立上下文AI复核，不重复索要人工签名、不伪造人审。新对话重新创建隔离审查上下文，旧Agent ID不保证可用。
- 常规实现、必要检查、适量真实模型／高德只读调用和有界优化复测已有授权，保持本项目配置与预算。**大规模质量、旧20任务、角色收益和DOM/Vision sweep仍暂停**，不为涨分自动追加大批次或提高预算。
- 只改本仓库及专属资源。禁止修改、依赖、重启兄弟`../Planora`源码、服务、配置、评测和原`planora` conda环境；仅用独立`plango`环境及本仓库固定`vendor`源码。
- 保持PlanGo、`plango`／`PLANGO_`、默认重庆及当前模型/高德配置。不重复更名、建环境、迁目录、清库或重建空库。作者、许可证、固定上游及历史证据保留。
- `.env`密钥不输出、不提交，不打印完整容器环境或展开的Compose配置。真实SQL、profile、配置及原始模型响应留私有`output/`，展示材料先排除敏感信息。
- `docs/YOYU_Planora_融合方案.md`是用户原文，**保持未跟踪，不改、不删、不提交**。SHA256：`4e63df2f9bf469b142ce6fc03b321f04da8337bf6d0a9ad817433197dca3a70d`。
- 完整保留任务、数据库、checkpoint、审批、幂等、UNKNOWN和登录态。不用另建任务、重新授权、补造回执、重放不确定写入制造成功。测试用独立数据库/profile，旧失败和累计消耗保留。
- 用户许可“不提交预约、不对真实世界产生影响”的验证。**真实预约提交、下单、支付、锁位、外部消息仍无授权。** 不因没点最终提交就放行可能创建购物车/占位的请求；准备输入仍沿逐项审批。不得自动解验证码、复制Cookie或编造预订URL，真正新验证才转人工。
- 保留Electron+React、真实可见浏览器、WebContentsView、已验证Playwright/CDP、DOM-first与按需只读Vision，共用会话及Harness授权/回执。集中式工作流、外层Plan-and-Execute、浏览器有界ReAct；single/multi是视角策略，不扩自治团队。不为形式新增MCP、框架、第二事实库或配置真相。
- **UI面向普通用户。** 明黄色（关联美团／大众点评）、暖白底、深色字，避免大面积墨绿；聊天显示每轮真实回复，不只留最后reason，也不编造旧历史。暂不可用用自然语言说明情况和下一步，不暴露异常栈、后端代码和正则；UNKNOWN不引导重复提交。
- 用户三个项目同时运行于WSL，曾多次崩溃，swap已扩至64GB。串行测试、单个临时图形窗口，不做无依据长压测或影响其他项目。未证明崩溃根因，消息倍增修复及180秒循环不能当根因结论。
- 临时窗口不用时及时关闭，保留会话资料；需扫码/验证/审批时明确说明，不留用途不明的窗口，不实现产品空闲自动退出。
- 可分阶段本地提交，不自动push、PR、发布、发消息、联系试用者或投递简历。不未经要求创建goal工具目标，不把阶段完成当全项目完成。

## 2. 当前代码、服务与数据

以下为本次写交接时只读核实；操作前仍须重查归属及未决，不用历史PID/CDP端口。

| 项目 | 状态 |
| --- | --- |
| 分支／写文档前HEAD | `feat/planora-browser-harness`／`bed0aac` |
| 工作树 | 写交接前仅保护原文未跟踪，无未交付产品改动 |
| 主Compose | `plango`，目录标签归属本仓库；API/PG/Redis healthy，worker运行 |
| 主API／DB | `http://127.0.0.1:8011`；PostgreSQL database/role均为`plango` |
| 主任务 | 8条：6 SUCCEEDED、1 REQUIREMENTS_READY、1 PARTIAL_FAILED；只读/草案终态不是6次交易 |
| 未决 | 有效租约或pending_command=0；未回执浏览器命令=0；RUNNING/UNKNOWN动作=0 |
| 桌面／临时评测 | lifecycle未发现主桌面；本仓库未发现活跃评测Electron driver、独立Uvicorn或人工审核UI服务 |
| 隔离Compose | `plango-e2e`所有容器停止；原trial容器已移除，卷/profile/备份保留 |
| Python | `/home/song/miniconda3/envs/plango/bin/python`，独立`plango` conda |
| WSL本次采样 | 内存约36GB、可用约12GB；swap64GB、已用约4.5GB，仅代表当时负载 |
| 旁支工作树 | `output/friendly-errors-worktree`，detached `4e8a429`，改动已合入；仅遗留未跟踪node_modules符号链接，不重复合并或提交它 |

**区分三个产品版本：**

| 版本 | 范围 |
| --- | --- |
| 30例冻结被测版 | `de37fecd3dc4d1792fe9efb11108126d4bfd5ef1`，140个产品/构建/依赖文件和安全模型配置；10.0%／54.3%绑定此版 |
| 当前源码／部署版 | `b55371d`共用友好提示，`a7b8b53`实际桌面验证及测试导入兼容；桌面已构建，主API/worker runtime源码hash与仓库一致；**新版尚无新质量批次成绩** |
| 最新既有试用包 | `release/plango-0.1.0-linux-x64-booking-preview.tar.gz`，产品提交`a308b20`；含预约参数保护，不含后续质量驱动修复和最新友好提示，未重打包/发布 |

评测提交：`b1434b0`冻结/独立审核管线，`23c6201`来源契约/无效替代，`d29b7b1`回环检查，`0707335`运输证据，`4215d9f`观测时间校验，`ca920ca`审核范围。它们不是新产品成绩。

主数据：Docker卷`plango_postgres-data`、`plango_redis-data`、`plango_runtime-data`，桌面`~/.config/plango/`。最近更新备份`output/friendly-errors-main-20260909/`含一致性SQL与行/profile指纹。更新后原18表1414行（含迁移表）中的17业务表1413行及3个Cookie/身份/回执文件保持，迁移0013未变，见[连续性记录](../eval/plango-friendly-errors/main-continuity.json)。旧备份不覆盖，历史脚本先读硬编码输出路径再复用。

## 3. 已有产品成果与未完成边界

| 阶段 | 已有成果 | 不代表 |
| --- | --- | --- |
| R0／P0–P3 | 全面更名、独立环境/部署、Harness核心、Linux安装/升级/冷备恢复 | 需要重新迁移或清库 |
| D1 | request_id/指纹、事务幂等接受、草稿/图片/选店恢复，同文不同轮独立 | 不确定写入可重放 |
| D2 | 执行服务/模型/能力可见，桌面ping与Docker配置分开 | ping通过即任务可用 |
| D3／D4 | 同店比较、canonical POI/地址核对，同一TripSpec选用/保存/重启 | 缺规则优惠最终可用；98元双人餐除以3就覆盖3人 |
| D5 | 单站相关候选/证据收窄，180秒循环 | 长期稳定、WSL根因或严格性能提升比例 |
| D6 | 半径/路程分开，有限同城公交，标准单人票价仅乘人数一次 | 跨城/铁路/出租车全覆盖，已知小计等于全费 |
| D7 | 悦廊官方参数预填、网络保护、同任务恢复，scope=booking_parameters | 已查空位、完整ready_to_review、预约成功 |
| UI | 黄色主题、每轮持久回复、共用中文故障提示 | 可掩盖未交付、伪造已保存/已送达 |

窄场景证据：[产品接续](../eval/plango-product-next/README.md)、[dev修复](../eval/plango-quality-fixes/README.md)、[友好提示](../eval/plango-friendly-errors/README.md)。需求稀疏更新等已有设计与测试，**30例仍揭示漏改、误写、连带清空，不能把“机制已实现”当所有问法正确。**

原会话必须保留：

- 大众点评run `f8f8ad2719074b0a9c62cbb08367892f`，`output/merchant-next/session-C0ovDv/`，SQLite=`data/runs.sqlite`、profile=`electron/`；SUCCEEDED/draft_ready、v4、36243tokens/28tools。47元券面值50，canonical POI `B00178UE0C`；2人、2026-09-11 18:30、120分钟、预算250、半径0.5km／路程2km／步行。真实路线0.255km/4分钟、餐饮估算120、交通0，优惠未抵扣，旧来源过期，完整菜单/规则需App。原12152tokens/5tools的PARTIAL_FAILED历史保留。用户已扫码及一次验证，临时后端/窗口已关、登录态保留，不重复扫码/无限撞风控。
- 悦廊run `82c0f6fbcb394a8eaba38f3c8d6990ea`，`output/d7-next/tealounge-brha972n/`；SUCCEEDED/booking_parameters，2人、2026-09-11 15:00，第1轮initial预算2240tokens/3tools，4条命令含1次旧标签失败；须知暂停、保存均实际重启恢复。cart/init和空位查询私有接口可能有副作用，仍阻断；不能把已预填参数当真实查询授权，见[参数预览](../eval/plango-booking-preview/README.md)。
- `output/requirements-next/session-kfOEZ5/`、`output/preparation-e2e/session-plD5Ds/`、`output/live-ui/profile/`及trial卷/备份保留。受控form不是商家交易，历史60000单轮诊断cap不沿用。

[独立试用验收](独立试用验收.md)尚无真实使用者完成记录，不代发邀约。D7/稳定性支线保留，当前先做质量优化。

## 4. 已完成质量基线与证据

| 指标 | 最终结果 |
| --- | --- |
| TSR | **10.0%（3/30）**，27题失败，30题有效主尝试及审核均完成 |
| Groundedness | **54.3%**，30题支持率宏平均，N/A=0；384条支持/541条事实是计数，不是主百分比算法 |
| Actor用量 | 有效30次：54调用/63702tokens；含9次无效：76调用/97924tokens；无缺usage，不含Codex编制/审核消耗 |

独立作者在产品冻结后起草15组虚构来源、30题，未读产品/旧dev/输出；6家族各5题，20交付/10范围判断。20题观测注入，10题实际Electron编辑/保存重启/真实202丢响应及取回；步行路线、未保存草案明确是受控初态。主审核者与2个分项助手均为AI，最后统一复核；人工0，同模型家族多上下文不等于独立模型交叉验证，不称真实网站盲测、人工校准或第三方榜单。

仅NEW-17、NEW-19保存重启及NEW-30送达待核实保留整题通过。恢复家族3/5，其余家族各0/5。**NEW-18消息取回/字段/范围通过，但停在未预注册澄清，未达原`draft_review`停点，仍失败；不要说成运输恢复失败。** 正确复述事实但缺计算/比较/结论也不算整题成功。TSR描述性Wilson区间3.46%–25.62%，来源组相关，不能宣称总体保证。

分别保存三类记录：

1. **9次无效替代：** 8次导入来源ID在输入前违反类型契约，所有已执行受影响题统一作废；NEW12另一次零用户输入/零模型的启动连接超时。独立裁定才替代，39次记录及消耗保留，普通产品失败不作废。
2. **既有证据补包：** 原导出遗漏request/202、保存基线、持久事件。核对129个原文件hash与15个已有快照事件envelope后，统一补10个桌面题；输出/来源/gold/SQL状态/旧运输字段/尝试关系均未改，无Actor重跑，原包和初审保留。晚事件/无时间汇总不能支持早输出。
3. **友好提示部署：** 30题输出封存后才合入/部署，未用新UI重渲染旧输出或改成绩。截图原错误另有测试初始化缺陷，但UI普遍隔离技术异常的共用修复也已完成。

最终材料（已排除密钥及真实主任务资料）：

| 文件 | 用途 |
| --- | --- |
| `eval/quality-v2-30/{tasks,sources,gold,runtime-fixtures,product-freeze}.json` | 冻结题、来源、gold、声明初态、产品hash |
| `eval/quality-v2-30/review/bundle.json` | **最终运输附录评分包** |
| `eval/quality-v2-30/review/{gold-review,output-review}.json` | 最终独立AI审核，必须匹配上述包 |
| `eval/quality-v2-30/{scores,usage,verification}.json` | 分数/分母、全部用量、hash/独立数学复核 |
| `eval/quality-v2-30/transport-evidence-{amendment,adjudication}.json` | 补包及独立裁定 |
| `output/quality-v2-30/session-20260909-r2/` | registry、manifest、原模型日志、SQLite/profile、UI/截图、原包/初审/修订/invalid裁定，留私有 |
| `output/quality-v2-30/session-20260909/` | 首次gold审查、实际打开但未签核的人工UI记录，不能伪造确认 |

dataset SHA=`21cb25bcc71a695b020bba54c51cec7db68a42f2e57dbdd7eaa3ac00f5602e5a`；product SHA=`70c45c71dc631426fd001cf50dc48ec436cffc277089fa44369ee32c82aa9ed7`；**最终collection SHA=`ea2f16a56a8b6ef99aaae55170cc6bdbe34b2c122cd2b044fdea4b1c1f044029`**。旧collection SHA=`12c26365d9d638b9d6a6909a5ebedfaba3d539b636b90a5e2042c472fe98323a`仅追溯，不与最终审核混配。

旧12例`eval/quality-v1/`仍未完成人工质量标注，原待审/失败保持，已知dev修复为`7e63cc8/aeb798a/0eba55c`。**旧12例与已揭示30例后续均为开发/回归资料。** 当前低分不支持“高成功率、强泛化”的简历主张。

## 5. 后续系统优化顺序

先用证据复现共同路径，再落地可解释的共享改动。以下是待实施方向，不是已经完成的新架构。

### 5.1 保护用户意图与需求状态

共同症状：人数/时长漏改，半径与路程混写，总预算/人均混淆，未要求字段被设置，澄清时清空原规范。

追踪`ChatPanel → store → preload/IPC → HarnessClient → app/runtime → graph → RequirementAgent → TripSpec合并/依赖刷新 → persistence`。重点读`vendor/plango_harness/backend/plango_harness/agent/subagents/requirement.py`的`_stabilize_explicit_fields/_fallback`，以及`agent/requirements.py`、`agent/contracts.py`、`backend/plango/requirements.py`和`runtime.py`。稳定化当前会用规则结果覆盖部分模型字段，需分清“未识别”和“明确清除/未知”，不能仅扩大词表。

验收不变量：只应用本轮有依据的稀疏修改；未提及及“其余不变”保留；预算口径不明不擅选；澄清不丢原TripSpec；POI/优惠/审批版本/请求身份/累计预算连续。模型负责语言理解，确定性边界负责类型、单位、证据、权限、幂等和落库，两者职责明确。

### 5.2 统一任务目标与信息需求的路由

已有资料的核算/比较/时段判断被送去问坐标或重规划；原文卡出现但所需交付没有形成。入口：`backend/plango/outcomes.py::update_task_context`、`graph.py`及Requirement/协调器。

根据用户目标、已有上下文和真正缺失字段选流程，只有完成目标确实需要的信息才澄清。复用现有结构化模型调用/上下文，减少关键词分流；不强行把不确定情况转只读“成功”。资料分析、行程编辑、外部操作权限分清，保持状态连续。

### 5.3 统一事实、费用范围与可见交付

症状：已知单价被称缺失、摄影/桌游费用套成餐费、受控资料被称真实网页、无依据有效期/全价/行为承诺。读`outcomes.py::SourceAnalysis/source_analysis`、`offers.py`、`supply.py`、`src/renderer/src/lib/harnessProjection.ts`、`OutcomeCanvas.tsx`和分享投影。

原文引用、实体、条件、时间、单位、推导共同支撑输出，沿同一事实状态生成卡片和答复；未知保持未知，已知正常交付。不靠一概拒答、少说事实刷Groundedness。业务完成依赖实际结果，不能靠模型措辞/UI状态。共用`src/shared/userMessages.ts`已落地，继续保持自然语言与UNKNOWN安全提示。

### 5.4 回归与新评测

先补少量共同机制的离线/集成反例：同义表达、否定、单位变化、歧义、“其余不变”、多轮/重启/重复送达；检查旧正例及未修改字段。必要模型验证在新独立目录串行运行，记录代码/模型/数据/预算及所有失败。

修复后复跑已揭示30题只能报告“回归集结果”。新泛化评测须先冻结产品，再让未读产品/开发结果的独立上下文按来源组采样，先审gold，再运行，再审完整输出。同源改人数/同题改写不跨dev/test。保持指标和停止条件；真实oracle/采集错误独立裁定、保留原版并明确修订，不因低分改标准。

## 6. 新会话首轮应实际完成什么

1. 只读核实第2节和Git，保留已有未提交工作；不启动旧商家或主profile。
2. 用第7节离线命令复算最终附录包/审核；不调用模型或浏览器。
3. 读失败证据，沿5.1/5.2复现一个跨表达/字段的共同问题，区分产品失败、初始化错误、评审遗漏；实施共享修复和必要反例，不只写建议。
4. 完成工程检查，再接新版采集/回归入口，不先对所有题反复跑模型。每阶段声明版本、范围、累计预算，保留结果。
5. 有界回归/独立审核后报告真实百分比、分母、范围、Token及剩余类别；未完成全批就明确部分/待审，不报最终全批分数。更新进度、交接和使用文档，可本地提交后继续。

**当前新版复测入口尚需接线，不能当现成命令：** `scripts/quality_acceptance.py`的`DATA`固定为`eval/quality-v2-30`，`product()`校验原源码/构建/模型冻结，原registry禁止替换有效尝试。当前产品已更新，直接跑旧`prepare/run/bundle`会被冻结不匹配拒绝，这是保护，不是服务故障。`quality_runner.py --run-dev`默认绑定v1，也不是v2或新版入口。

需做最小显式数据集/冻结路径接线，贯穿materials、manifest、fixture来源及工作目录，复用collector/driver/评分器、保留旧默认兼容。先离线检查不同路径、旧冻结拒绝、原目录不覆盖、输入/gold隔离、回执事件完整和证据时间，再给出真正可用的新命令。**不改旧`product-freeze.json`、删registry或移除hash断言来让重跑通过，不另建评分框架。** 这项接线及系统修复是下一轮工作，本次只完成交接。

现有独立AI汇总入口还要求计划30例和完整审核。小批回归可分阶段收集，但不能填充假案例、拼接旧版本结果或把部分计分说成30例最终成绩；若确需另一规模，必须显式版本化评测协议和校验规则。

## 7. 可用命令、预算与验证范围

只读归属和未决检查，不展开环境：

```bash
git status --short
git log -8 --oneline
git worktree list
docker ps -a --filter label=com.docker.compose.project=plango --format '{{.Names}}\t{{.Status}}\t{{.Ports}}'
docker exec plango-postgres-1 psql -U plango -d plango -Atc "SELECT count(*) FROM agent_run WHERE lease_until>now() OR pending_command IS NOT NULL; SELECT count(*) FROM plango_browser_command WHERE result IS NULL; SELECT count(*) FROM agent_action WHERE status IN ('RUNNING','UNKNOWN');"
```

桌面用`scripts/lifecycle.py::desktop_processes()`核对cwd、开始时间/boot ID和父子关系，不按通用进程名kill。`./start.sh`/`./stop.sh`校验归属并保留卷，但优化不必启动主桌面或停止全栈。

**可直接执行：离线复算，创建新输出目录。**

```bash
mkdir -p output
handoff_score_dir=$(mktemp -d output/handoff-score-XXXXXX)
/home/song/miniconda3/envs/plango/bin/python scripts/quality_ai_import.py \
  --bundle eval/quality-v2-30/review/bundle.json \
  --gold-review eval/quality-v2-30/review/gold-review.json \
  --output-review eval/quality-v2-30/review/output-review.json \
  --output "$handoff_score_dir/result.json"
```

预期`status=complete`、`issues=0`；结果内TSR=10.0、Groundedness≈54.29325578944、valid=annotated=30。只校验旧包/标注/数学，不测试当前产品或证明AI语义无误。

按变更选检查，文档改动不重跑全套/模型：

```bash
npm run typecheck
npm run test:ui
npm run test:transport
npm run build
/home/song/miniconda3/envs/plango/bin/python -m pytest -q backend/tests/test_quality_acceptance_integrity.py backend/tests/test_quality_ai_import.py backend/tests/test_quality_human_import.py
/home/song/miniconda3/envs/plango/bin/python -m ruff check backend vendor/plango_harness/backend/plango_harness
/home/song/miniconda3/envs/plango/bin/python -m mypy backend/plango vendor/plango_harness/backend/plango_harness
```

真实桌面专项：`env -u ELECTRON_RUN_AS_NODE xvfb-run -a -s '-screen 0 1800x1120x24' npm run test:desktop`，用临时数据/受控服务验证旧异常遮蔽、原优惠保留和消息恢复，不是商家质量成绩。完整常规检查`npm run check`不含所有Electron回归；改对应模块再补必要专项，不为数量重复执行。

最近检查：旧dev产品修复阶段完整426pytest+58subtests、后续23项定向；友好提示阶段TS、UI/运输、LLM/历史定向、构建、隔离API投影1项、Ruff/mypy和实际Electron通过；评测补证/时序/AI聚合12项+29subtests，最后观测时间兼容定向3项通过。**这是各自提交的检查，不是当前全树新跑的一次总数，也不是TSR。**

默认每轮12000tokens/48tools/300执行秒，模型timeout45秒/retry1，高德timeout8秒，以实际配置为准。collector另限每case12次、每次run调用80次模型/120000已报告tokens阈值；不是跨所有分批自动共享总账，下一轮预先声明阶段累计上限，不拆批绕预算。受控业务时间可固定，SQL租约/超时/真实捕获时间不可冻结；单次响应超阈值照实计入。

主服务确需更新时：重新核对归属/未决，备份SQL/profile指纹到新目录，静止后仅更新本项目API/worker，核对源码与原行/登录连续；PG/Redis卷不重建，主profile不做隔离试验。旧辅助脚本可能写固定证据目录，先读后复制修改输出路径。本次交接没有重启服务。

## 8. 阶段收尾

区分源码修复、工程回归、旧集复测、新集评测、真实商家验收及部署/安装包版本。保留失败/invalid/附录和累计消耗，缺审核不补百分比，UI文案变化不回写旧输出。关闭不用的临时窗口/专属服务、保留数据，最后核对Git、主未决及保护原文hash；更新本文件、进度与接手提示词，本地提交，不push/PR/发布。
