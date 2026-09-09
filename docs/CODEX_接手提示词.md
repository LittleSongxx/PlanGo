# PlanGo 新 Codex 接手提示词

更新：2026-09-09，质量优化与复测。复制下面代码块到本项目新Codex对话；完整事实、操作入口及历史证据见[当前handoff](CODEX_HANDOFF.md)。

```text
请接手 /home/song/code/Agent/multi-agent/PlanGo，直接实施后续系统性质量优化与有界复测，不要只分析或询问“是否开始”。

先读 AGENTS.md、docs/CODEX_HANDOFF.md（重点0/1/4/5/6/7节）、docs/架构决策.md、docs/Agent架构与选型.md、docs/质量评测协议.md、eval/quality-v2-30/README.md及docs/实施进度.md末尾。旧handoff的D7优先、尚未登录/安装、所有测评暂停或无百分比不是当前状态。

用户要用于秋招AI应用/Agent开发作品集，优先落实产品与可复验证据，不改成仅写简历。当前主线是选门店/优惠→改人数日期预算→有来源的适用性判断→保存→中断后继续。已有局部流程不代表新表达/新资料可靠。

最重要的优化原则：避免逐badcase加关键词、门店名、题号或专用分支。沿共享调用链找共同根因，从任务路由、需求稀疏修改/状态合并、事实与费用范围的交付边界修复；允许修补影响整个系统的共享契约小问题。用同义表达、否定、单位/口径变化、歧义、其余不变、多轮/重启等反例验证，不通过改gold或删失败涨分。不另建事实库/评分框架。

当前分支 feat/planora-browser-harness，交接前HEAD bed0aac，后续可能只有文档提交，以实际Git为准。先只读核实工作树、资源归属、有效租约/pending/未回执/UNKNOWN。写交接时主plango API/PG/Redis健康、worker运行、8任务，未决均0；无主桌面和活跃隔离评测服务。plango-e2e停止，trial卷/profile/备份保留。主17业务表1413行及3个Cookie/身份/回执文件保持，最近私有备份output/friendly-errors-main-20260909/。不按旧PID/CDP操作。

30例基线已完成独立AI审核：TSR 10.0%（3/30），Groundedness 54.3%（30题宏平均，384/541事实有支持，N/A=0）。15组虚构来源、6家族各5题、20交付/10范围判断；20题原始观测注入、10题实际Electron状态流程。是同模型家族独立上下文AI审核，人工0，不称真实网站盲测、人工校准或第三方成绩。用户已明确让新开独立子Agent代为审核，延续该偏好，不再次卡在人审；新对话重新开隔离上下文，不假设旧Agent ID可复用。

区分版本：基线绑定冻结de37fecd3dc4d1792fe9efb11108126d4bfd5ef1。当前b55371d/a7b8b53已部署共用中文故障提示并构建桌面，尚无新版质量成绩；旧试用包来自a308b20，不含最新修复。原30题输出封存后才改UI，没有重渲染旧结果。

39次尝试=30有效+9独立裁定无效；无效为8次来源ID初始化违约与NEW12一次输入前连接错误，统一替代且保留旧记录。有效54调用/63702tokens，含无效共76调用/97924tokens，无缺usage，不含Codex审核消耗。另有原运输证据补包：只补已采集回执/事件、无Actor重跑，不能混作产品失败或新尝试。NEW17/19/30整题通过；NEW18运输和字段已通过，但未达声明draft_review停点，仍失败，不放宽标准。

最终可复算包为eval/quality-v2-30/review/bundle.json及同目录gold-review.json/output-review.json，collection SHA ea2f16a56a8b6ef99aaae55170cc6bdbe34b2c122cd2b044fdea4b1c1f044029；scores/usage/verification在上级目录。原包、初审、无效记录、SQLite/profile/模型日志在output/quality-v2-30/session-20260909-r2/保留。先按handoff第7节新目录离线复算，不启动模型。旧12例和已揭示30例后续只能叫开发/回归集，新泛化评测须先冻结新版，再独立按来源组采样/审gold/运行/审输出。

复测入口尚需最小接线：quality_acceptance.py的DATA固定v2，product()检查旧冻结，原registry禁止替换有效attempt；当前新版直接调用旧prepare/run/bundle会被拒绝。不要改旧freeze、删registry或移除hash保护。沿现有collector/driver加显式新数据集/冻结路径及新工作目录，先离线检查输入/gold隔离、路径/hash、运输证据/时间，再给真实可用命令；quality_runner.py --run-dev默认是v1，不能冒充v2。不要先无差别重跑30例。

R0更名/独立conda/迁移、P0–P3、D1/D2幂等恢复与执行配置、D3/D4选用保存、D6距离公交、聊天历史、明黄暖白主题及共用自然语言错误提示已做，不重复。源码仍有约束漏改/误写/规范清空、无关坐标澄清、费用/来源范围混淆等跨案例问题；优先沿handoff第5节实施。保持原请求身份、同文不同轮、预算、审批、UNKNOWN与来源时效契约。

用户三个项目在WSL运行并曾崩溃，swap64GB；串行检查、单临时窗口，不猜崩溃根因、不做额外长压测。常规实现、必要有界模型/高德只读和优化复测已授权；保留本项目实际预算（默认每轮12000tokens/48tools/300执行秒），记录跨分批累计，不能拆批或涨预算凑成功。大规模质量、旧20任务、角色收益、DOM/Vision sweep仍暂停。没有用户指定的目标通过率，不自设好看指标。

只改本仓库及专属资源，禁止修改/依赖/重启../Planora或原planora环境。保持PlanGo/plango/PLANGO_、默认重庆、现有模型/高德配置，Python用/home/song/miniconda3/envs/plango/bin/python；不输出/提交密钥，保留作者许可证固定上游。docs/YOYU_Planora_融合方案.md保持未跟踪，不修改/删除/提交。不清库/重置profile/另建任务/重授权/重放UNKNOWN造成功。

保留Electron+React、可见浏览器、WCV+Playwright/CDP、DOM-first与按需只读Vision；集中式工作流、外层Plan-and-Execute、浏览器有界ReAct，single/multi不是自治团队。UI用明黄色暖白，聊天保留每轮回复；异常以普通用户能懂的语言解释，未送达/已接收/待核实分开，不引导不确定写入重试。

大众点评已扫码且原run f8f8ad2719074b0a9c62cbb08367892f已到v4草案并重启恢复，真实profile保留，完整菜单/规则需App；不要重新扫码、撞风控、复制Cookie或编造URL。悦廊参数run82c0f6fbcb394a8eaba38f3c8d6990ea已预填2人/2026-09-11 15:00，但只booking_parameters，私有cart/查询因副作用不明仍阻断。D7/独立试用和长时稳定是支线，不抢本轮质量主线。

用户只允许不提交预约、不影响真实业务的验证。真实预约/下单/支付/锁位/外部消息无授权，准备输入仍逐项审批。测试仅独立DB/profile，临时窗口不用就关并保留资料，不做产品闲置自动退出，不留用途不明窗口。必要验证后更新进度/handoff/使用文档，可本地提交并继续；不push/PR/发布/发消息，不新建goal工具目标，不把阶段完成说成全项目完成。
```
