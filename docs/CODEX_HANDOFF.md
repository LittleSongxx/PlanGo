# PlanGo 当前实施交接

更新：2026-09-10，共享质量修复及6旧+6新有界复测后。仓库：`/home/song/code/Agent/multi-agent/PlanGo`。产品提交`53d4ef5`，采集器修复`20dcd94`；之后的报告/交接提交以实际Git为准。

**2026-09-10当前会话补充（优先于以下历史状态）：** 用户反复明确“任务完成优先，全面清理过拟合规则”，已永久记录根AGENTS与ADR-005。HEAD仍`8dcf2d3`，新工作树正在做广泛重构，尚未提交/部署：统一TaskDecision读取完整请求、规范、来源和工具结果，删除TaskIntent/SourceAnalysis规则编译与需求正则fallback；固定工具负责计算，答案作为task_answer交付；规划协调去重，未知允许草案，严重外部写仍保留审批/幂等/UNKNOWN边界。已完成50项核心、62项集成验证，当前正适配并运行全量测试；不能把旧模型stub失败用恢复生产fallback解决。改动与具体未完成检查见私有`output/tightening-F9A9ea/WORK_STATE.md`最新节。

旧实现`28c1278`与`8dcf2d3`的同六题回归TSR均1/6，G分别89.38%与86.68%，仅NEW-29通过。重构后尚未实模评测，不能宣称质量改善。本阶段累计34次模型调用/45292tokens，共享上限80次/120000tokens不增加。`eval/quality-v4-independent-six/`是8dcf2d3冻结后独立编写但未执行的资料，不能读取它调参；当前重构冻结后再独立编题。原主服务未部署本轮实现，原profile未动，新试用包未打，独立真人试用未完成。

## 0. 新对话应接什么

**当前主线仍是系统性质量优化，不能转回D7或只写简历。** 本轮已实施共享修改并完成6题旧集回归及6题独立新资料评测，详细结果见第9节。工程检查通过，但回归TSR仍0/6，新集仅保存恢复1/6，尚未证明可靠交付；接续应修共同交付与稀疏修改机制，不按题号加规则。

先读本文件，尤其第1节约束、第4节基线、第5–7节优化与复测入口和第9节最新结果；再读根[AGENTS.md](../AGENTS.md)、[架构决策](架构决策.md)、[Agent架构与选型](Agent架构与选型.md)、[质量协议](质量评测协议.md)、[30例报告](../eval/quality-v2-30/README.md)及[实施进度](实施进度.md)末尾。简版入口是[接手提示词](CODEX_接手提示词.md)。

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
| 分支／写文档前HEAD | `feat/planora-browser-harness`／`20dcd94`（报告提交另查Git） |
| 工作树 | 产品与采集器已提交；本轮报告/交接随后提交，保护原文保持未跟踪 |
| 主Compose | `plango`，目录标签归属本仓库；API/PG/Redis healthy，worker运行 |
| 主API／DB | `http://127.0.0.1:8011`；PostgreSQL database/role均为`plango` |
| 主任务 | 8条：6 SUCCEEDED、1 REQUIREMENTS_READY、1 PARTIAL_FAILED；只读/草案终态不是6次交易 |
| 未决 | 有效租约或pending_command=0；未回执浏览器命令=0；RUNNING/UNKNOWN动作=0 |
| 桌面／临时评测 | lifecycle未发现主桌面；本仓库未发现活跃评测Electron driver、独立Uvicorn或人工审核UI服务 |
| 隔离Compose | `plango-e2e`所有容器停止；原trial容器已移除，卷/profile/备份保留 |
| Python | `/home/song/miniconda3/envs/plango/bin/python`，独立`plango` conda |
| WSL本次采样 | 内存约36GB、可用约11GB；swap64GB、已用约5GB，仅代表当时负载 |
| 旁支工作树 | `output/friendly-errors-worktree`，detached `4e8a429`，改动已合入；仅遗留未跟踪node_modules符号链接，不重复合并或提交它 |

**区分三个产品版本：**

| 版本 | 范围 |
| --- | --- |
| 30例冻结被测版 | `de37fecd3dc4d1792fe9efb11108126d4bfd5ef1`，140个产品/构建/依赖文件和安全模型配置；10.0%／54.3%绑定此版 |
| 当前源码／部署版 | `53d4ef5`包含友好提示、TaskIntent、需求出处校验与规范保留；桌面已构建，主API/worker的71个后端源码hash一致；本轮6+6结果见第9节，不能套用旧30题成绩 |
| 最新既有试用包 | `release/plango-0.1.0-linux-x64-booking-preview.tar.gz`，产品提交`a308b20`；含预约参数保护，不含后续质量驱动修复和最新友好提示，未重打包/发布 |

评测提交：`b1434b0`冻结/独立审核管线，`23c6201`来源契约/无效替代，`d29b7b1`回环检查，`0707335`运输证据，`4215d9f`观测时间校验，`ca920ca`审核范围。它们不是新产品成绩。

主数据：Docker卷`plango_postgres-data`、`plango_redis-data`、`plango_runtime-data`，桌面`~/.config/plango/`。最近备份`output/quality-v3-main-20260910/`含一致性SQL与行/profile指纹，旧备份仍保留。更新后原18表1414行（含迁移表）中的17业务表1413行及3个Cookie/身份/回执文件保持，见[连续性记录](../eval/plango-quality-v3/main-continuity.json)。旧备份不覆盖，历史脚本先读硬编码输出路径再复用。

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

先用证据复现共同路径，再落地可解释的共享改动。53d4ef5已修复部分共享边界，以下仍是未完成的质量方向；本轮真实失败优先看第9节，不把新契约存在当正确泛化。

### 5.1 保护用户意图与需求状态

共同症状：人数/时长漏改，半径与路程混写，总预算/人均混淆，未要求字段被设置，澄清时清空原规范。

追踪`ChatPanel → store → preload/IPC → HarnessClient → app/runtime → graph → RequirementAgent → TripSpec合并/依赖刷新 → persistence`。重点读`vendor/plango_harness/backend/plango_harness/agent/subagents/requirement.py`的`_grounded_patch/_stabilize_explicit_fields/_fallback`和`agent/decisions.py::RequirementOutput.field_evidence`，以及`agent/requirements.py`、`agent/contracts.py`、`backend/plango/requirements.py`和`runtime.py`。新版真实模型补丁已有出处校验，旧fallback仅在适配器确实返回fallback时使用。继续检查空/default字段、完整子句门槛和部分成功修改为何仍触发额外澄清；没有保存的原模型提案不能靠失败状态倒推。仍需分清未识别、未提及、清除与未知，不能扩大词表。

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

## 6. 接续应实际完成什么

1. 只读重查Git、归属、未决及第9节，保留本轮所有有效失败/无效尝试/原包，不重跑迁移、登录或主任务。
2. 离线复算原30基线及本轮两个最终包；旧集、新集和工程测试分别解释。
3. 沿共享语义补丁与目标交付路径复现共同问题。优先处理已知参数有效修改却无法形成草案、资料已取得却没有计算/比较/结论；必要诊断应记录原模型结构化提案至既有私有日志，不能把它作为评分来源或凭结果猜原提案。
4. 完成针对性工程检查后再冻结下一版。本轮两组6题均已揭示，只能作回归；新泛化资料仍须冻结后独立按来源组起草、审gold、运行和审输出。
5. 保持实际预算及共享累计账本，不追加样本/改gold/放宽停点追分；本轮没有达到一个用户指定的通过率，也没有自行设置目标。

**新版接线已实现，不要重复实现：** `quality_acceptance.py freeze/prepare/run/bundle`支持显式`--dataset/--freeze/--work`，新数据集须有`plango.controlled-bounded.v1`协议及完整计划/审核，旧默认仍是30题。路径、产品文件清单、模型配置、初态、gold和运输证据均绑定hash；原目录/有效attempt禁止覆盖。`quality_runner.py --run-dev`仍默认v1，不能冒充本轮入口。

保存恢复采集器已在API重开后增加原profile/run的只读桌面恢复，明确禁止再保存/发消息/新任务/模型。原API/UI附件按真实捕获时间组包，不称独立SQL，也不回证早期输出。`adjudicate-transport`只用于已有独立裁定及原hash匹配的采集错误；不是产品失败重跑入口。

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

**本轮两个最终包可直接离线复算：**

```bash
quality_score_dir=$(mktemp -d output/quality-v3-score-XXXXXX)
for dataset in quality-v3-regression-six quality-v3-independent-six; do
  /home/song/miniconda3/envs/plango/bin/python scripts/quality_ai_import.py \
    --bundle "eval/$dataset/review/bundle.json" \
    --gold-review "eval/$dataset/review/gold-review.json" \
    --output-review "eval/$dataset/review/output-review.json" \
    --output "$quality_score_dir/$dataset.json"
done
```

预期两包均`complete`、`issues=0`；回归0/6与91.1779448622%，独立新集1/6与82.0833333333%。这是数学/格式/证据定位复算，不调用模型，也不证明审核语义无误。

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

默认每轮12000tokens/48tools/300执行秒，模型timeout45秒/retry1，高德timeout8秒，以实际配置为准。旧v1 collector仍按单run计批次；本轮新版协议另限每attempt12次，并对绑定同一账本的分批共享80次模型/120000已报告tokens停止阈值，下一轮仍须预先声明并保留阶段累计，不拆批绕预算。本轮两数据集共用`output/quality-v3-stage/model-budget.jsonl`，上限80调用/120000已报告tokens，已用42/37018；日志/无效记录不删除。受控业务时间可固定，SQL租约/超时/真实捕获时间不可冻结；单次响应超阈值照实计入。

主服务确需更新时：重新核对归属/未决，备份SQL/profile指纹到新目录，静止后仅更新本项目API/worker，核对源码与原行/登录连续；PG/Redis卷不重建，主profile不做隔离试验。旧辅助脚本可能写固定证据目录，先读后复制修改输出路径。本轮已经按该流程更新API/worker，连续性记录见第9节；后续不要无必要再重启。

## 8. 阶段收尾

区分源码修复、工程回归、旧集复测、新集评测、真实商家验收及部署/安装包版本。保留失败/invalid/附录和累计消耗，缺审核不补百分比，UI文案变化不回写旧输出。关闭不用的临时窗口/专属服务、保留数据，最后核对Git、主未决及保护原文hash；更新本文件、进度与接手提示词，本地提交，不push/PR/发布。


## 9. 2026-09-10 最新一轮：已完成与仍失败

产品`53d4ef5`冻结158个文件；产品SHA=`221adb3d444868e655989d5b8da293df70814d5f4f0214857d4bc8f3c153bfbb`。共享TaskIntent负责本轮目标分类；field_evidence验证本轮稀疏修改，规范在Runtime重入/澄清中保留，旧计划/审批仍失效；增加严格单位/时间边界、显式来源过期判断及中性费用/场所类别。不添加门店/题号分支、事实库或Agent框架。

| 批次 | TSR | Groundedness任务宏平均 | 事实计数／N/A | Actor |
| --- | --- | --- | --- | --- |
| 已揭示6题回归 | 0/6（0.0%） | 91.18% | 105/116；0 | 21调用／14443tokens |
| 冻结后独立6题受控新资料 | 1/6（16.7%） | 82.08% | 163/167；0 | 21调用／22575tokens |

两个最终包均经新独立上下文AI逐项审核、原聚合器复算complete/0issues；人工0、非模型身份盲审、没有独立模型家族交叉验证。新集6来源组与旧集无重合；四例观测注入+两例Electron。原6回归同样没有整题通过，当前不能声称TSR提升；高G主要来自资料摘录，未代替所需交付。两小集也不能与旧30题直接相减当总体泛化提升。

共同失败仍在：资料核算/比较/时段判断缺交付，FRESH01仍误入坐标澄清；NEW29明确4人未落盘；NEW18字段19:20/120分钟正确且运输通过，但未到draft_review；FRESH03多轮字段部分正确却停在额外澄清，没有最终草案。FRESH05仅保存恢复通过，不代表商家履约或语义能力整体可靠。

区分两类评测修复：一是已有完整UI正文/API状态遗漏组包，统一按原hash/时间追加，API不是独立SQL，无重渲染/Actor重跑。回归初审94.33%及原包保留，完整UI补审后为91.18%。二是FRESH05原采集器未执行双端重开后的桌面读取，独立裁定为runner_error invalid；`20dcd94`修通用流程后仅替代一次，0模型。共13尝试=12有效+1无效，所有原件保留；累计42调用/37018tokens，无缺usage，无预算扩大。后端重启是LocalAPI生命周期退出、新app/DB重开，不是父Python进程退出或WSL重启。

证据入口：[总报告](../eval/plango-quality-v3/README.md)、[回归包](../eval/quality-v3-regression-six/README.md)、[独立新包](../eval/quality-v3-independent-six/README.md)。私有原模型日志、SQLite/profile、所有初审/裁定/原包在`output/quality-v3-stage/`，不清理或重置。

工程检查：产品版全量489pytest+87subtests、Ruff/mypy71、TS/UI/运输/构建通过；采集器后续17项定向及实际只读恢复通过，不能合称为一次新的全树全量检查。主API/worker已部署产品53d4ef5，71后端源码hash匹配；API/PG/Redis健康、worker运行，原17业务表1413行/3身份文件保持、8任务及未决0。新备份`output/quality-v3-main-20260910/`，旧trial包仍为a308b20、未重打包/发布。临时桌面/API/代理全部关闭，资料保留。

[独立作品集/复杂度审查](作品集主线与复杂度审查.md)结论：主线清楚且窄场景开发闭环已有证据，独立使用与泛化交付尚不足；核心checkpoint/审批/回执/UNKNOWN/SSE有必要，旧IPC/空IM入口、重复子图声明和两个未用直接依赖有收缩空间。本轮仅审查这些删减，不用外围清理抢质量主线。README和三分钟演示已聚焦选店优惠→改条件→核依据→保存→中断继续，未改成仅写简历。

新集最终审核另有一次语义标签更正（旧文件保留，未改gold/Actor输出）：FRESH03的budget=null与4人/每人115元仍表达460元有效上限，不能称预算漏算。原金标C3绑定具体表示的局限已独立注明；该题因无最终草案仍失败。纠正一条事实及误报完成标签后G为82.08%，TSR不变。后续不要为匹配该字段金标给产品写冗余约束；需改gold时必须另行独立裁定、版本化及统一重判。

收尾时另出现未跟踪文件`AI应用_AI全栈_Agent_Java后端面试知识图谱.md`，本轮未创建、读取、修改或提交；与保护的融合原文一起保留，接续不要使用git add全目录误收。
