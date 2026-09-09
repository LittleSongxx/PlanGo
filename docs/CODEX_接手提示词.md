# PlanGo 新 Codex 接手提示词

更新：2026-09-10，共享修复和6旧+6新有界复测后。复制下面代码块到本项目新Codex对话；完整事实、操作入口及历史证据见[当前handoff](CODEX_HANDOFF.md)。

```text
请接手 /home/song/code/Agent/multi-agent/PlanGo，直接实施后续系统性质量优化与有界复测，不要只分析或询问“是否开始”。

先读 AGENTS.md、docs/CODEX_HANDOFF.md（重点0/1/4/5/6/7/9节）、docs/架构决策.md、docs/Agent架构与选型.md、docs/质量评测协议.md、eval/plango-quality-v3/README.md、两个quality-v3六题报告、eval/quality-v2-30/README.md及docs/实施进度.md末尾。旧handoff的D7优先、尚未登录/安装、所有测评暂停或无百分比不是当前状态。

用户要用于秋招AI应用/Agent开发作品集，优先落实产品与可复验证据，不改成仅写简历。当前主线是选门店/优惠→改人数日期预算→有来源的适用性判断→保存→中断后继续。已有局部流程不代表新表达/新资料可靠。

最重要的优化原则：避免逐badcase加关键词、门店名、题号或专用分支。沿共享调用链找共同根因，从任务路由、需求稀疏修改/状态合并、事实与费用范围的交付边界修复；允许修补影响整个系统的共享契约小问题。用同义表达、否定、单位/口径变化、歧义、其余不变、多轮/重启等反例验证，不通过改gold或删失败涨分。不另建事实库/评分框架。

当前分支 feat/planora-browser-harness，产品53d4ef5、采集器20dcd94，后续可能只有文档提交，以实际Git为准。先只读核实工作树、资源归属、有效租约/pending/未回执/UNKNOWN。写交接时主plango API/PG/Redis健康、worker运行、8任务，未决均0；无主桌面和活跃隔离评测服务。plango-e2e停止，trial卷/profile/备份保留。主17业务表1413行及3个Cookie/身份/回执文件保持，最近私有备份output/quality-v3-main-20260910/。不按旧PID/CDP操作。

30例基线已完成独立AI审核：TSR 10.0%（3/30），Groundedness 54.3%（30题宏平均，384/541事实有支持，N/A=0）。15组虚构来源、6家族各5题、20交付/10范围判断；20题原始观测注入、10题实际Electron状态流程。是同模型家族独立上下文AI审核，人工0，不称真实网站盲测、人工校准或第三方成绩。用户已明确让新开独立子Agent代为审核，延续该偏好，不再次卡在人审；新对话重新开隔离上下文，不假设旧Agent ID可复用。

区分版本：基线绑定冻结de37fecd3dc4d1792fe9efb11108126d4bfd5ef1。当前产品53d4ef5已部署，71个后端源码hash一致、桌面已构建；本轮旧六题回归TSR0/6、G91.18%（105/116事实），独立新六题TSR1/6、G82.08%（163/167事实），两组N/A均0；旧试用包来自a308b20，不含最新修复。原30题输出封存后才改UI，没有重渲染旧结果。

原30基线39次尝试=30有效+9独立裁定无效；无效为8次来源ID初始化违约与NEW12一次输入前连接错误，统一替代且保留旧记录。有效54调用/63702tokens，含无效共76调用/97924tokens，无缺usage，不含Codex审核消耗。另有原运输证据补包：只补已采集回执/事件、无Actor重跑，不能混作产品失败或新尝试。NEW17/19/30整题通过；NEW18运输和字段已通过，但未达声明draft_review停点，仍失败，不放宽标准。

最终可复算包为eval/quality-v2-30/review/bundle.json及同目录gold-review.json/output-review.json，collection SHA ea2f16a56a8b6ef99aaae55170cc6bdbe34b2c122cd2b044fdea4b1c1f044029；scores/usage/verification在上级目录。原包、初审、无效记录、SQLite/profile/模型日志在output/quality-v2-30/session-20260909-r2/保留。先按handoff第7节新目录离线复算，不启动模型。旧12例和已揭示30例后续只能叫开发/回归集，新泛化评测须先冻结新版，再独立按来源组采样/审gold/运行/审输出。

新版复测入口已接通，不再重复实现：quality_acceptance.py freeze/prepare/run/bundle支持显式--dataset/--freeze/--work，新1–12例须protocol.json(version=plango.controlled-bounded.v1)、预注册计划及完整审核，旧默认30题保护保持。原目录/有效attempt不覆盖，quality_runner.py --run-dev仍默认v1。两个最终包在eval/quality-v3-regression-six/review/与eval/quality-v3-independent-six/review/，都能按handoff第7节离线复算complete/0issues。

本轮6旧+6新共13attempts=12有效+1独立裁定runner_error invalid，42调用/37018tokens，无缺usage；共享账本output/quality-v3-stage/model-budget.jsonl，上限80调用/120000已报告tokens，不能拆批补额度。新集冻结产品后由独立上下文仅读环境契约编题，另一上下文审gold/输出；人工0，非模型身份盲审，仍是虚构受控资料。现在两组六题都已揭示，后续只作回归。

区分两种补救：原已采集UI正文/同期API状态漏组包，统一原hash/时间补入、无重渲染/模型重跑；API不是独立SQL，回归94.33%初审保留，补审后91.18%。FRESH05首次采集器漏执行后端重开后的真实桌面读取，独立判invalid后只替代一次（0模型）；新的真实恢复动作不能冒充仅补旧证据。该题最终通过保存恢复，LocalAPI生命周期/新app/DB重开而父Python PID未退出，不称WSL重启。其他11个有效尝试原样保留。原件/SQLite/profile/初审/裁定保存在output/quality-v3-stage/。

产品已修共享TaskIntent、field_evidence、规范保留、单位/时间边界与部分费用/来源标签；新模型场景仍有错误坐标澄清、资料只摘录未形成答案、部分字段正确却额外澄清不交付草案。不要把工程全过当质量已提升，继续沿统一语义与交付路径修复，不给这些题专用分支。

R0更名/独立conda/迁移、P0–P3、D1/D2幂等恢复与执行配置、D3/D4选用保存、D6距离公交、聊天历史、明黄暖白主题及共用自然语言错误提示已做，不重复。规范清空已修并经Runtime重入/暂停/重启验证，但模型约束漏改/误写、无关坐标澄清、费用/来源范围和完整交付仍有跨案例问题；优先沿handoff第5节实施。保持原请求身份、同文不同轮、预算、审批、UNKNOWN与来源时效契约。

用户三个项目在WSL运行并曾崩溃，swap64GB；串行检查、单临时窗口，不猜崩溃根因、不做额外长压测。常规实现、必要有界模型/高德只读和优化复测已授权；保留本项目实际预算（默认每轮12000tokens/48tools/300执行秒），记录跨分批累计，不能拆批或涨预算凑成功。大规模质量、旧20任务、角色收益、DOM/Vision sweep仍暂停。没有用户指定的目标通过率，不自设好看指标。

只改本仓库及专属资源，禁止修改/依赖/重启../Planora或原planora环境。保持PlanGo/plango/PLANGO_、默认重庆、现有模型/高德配置，Python用/home/song/miniconda3/envs/plango/bin/python；不输出/提交密钥，保留作者许可证固定上游。docs/YOYU_Planora_融合方案.md保持未跟踪，不修改/删除/提交。不清库/重置profile/另建任务/重授权/重放UNKNOWN造成功。

保留Electron+React、可见浏览器、WCV+Playwright/CDP、DOM-first与按需只读Vision；集中式工作流、外层Plan-and-Execute、浏览器有界ReAct，single/multi不是自治团队。UI用明黄色暖白，聊天保留每轮回复；异常以普通用户能懂的语言解释，未送达/已接收/待核实分开，不引导不确定写入重试。

大众点评已扫码且原run f8f8ad2719074b0a9c62cbb08367892f已到v4草案并重启恢复，真实profile保留，完整菜单/规则需App；不要重新扫码、撞风控、复制Cookie或编造URL。悦廊参数run82c0f6fbcb394a8eaba38f3c8d6990ea已预填2人/2026-09-11 15:00，但只booking_parameters，私有cart/查询因副作用不明仍阻断。D7/独立试用和长时稳定是支线，不抢本轮质量主线。

用户只允许不提交预约、不影响真实业务的验证。真实预约/下单/支付/锁位/外部消息无授权，准备输入仍逐项审批。测试仅独立DB/profile，临时窗口不用就关并保留资料，不做产品闲置自动退出，不留用途不明窗口。独立作品集与过度设计检查见docs/作品集主线与复杂度审查.md：核心恢复/审批/证据边界有必要，旧IPC/空IM入口/重复声明/两个未使用直接依赖可精简，尚未批量删除。必要验证后更新进度/handoff/使用文档，可本地提交并继续；不push/PR/发布/发消息，不新建goal工具目标，不把阶段完成说成全项目完成。
```
