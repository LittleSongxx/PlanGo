# PlanGo N1→N4 接续验证

日期：2026-09-09。真实调用使用本项目配置及归属明确的独立 Electron profile/SQLite；主库不作测试清空。完整质量测评仍暂停。最终实现与安装验收见 [实施进度](../../docs/实施进度.md) 和 [当前交接](../../docs/CODEX_HANDOFF.md)。

## 真实首站边界

根据已有大众点评/美团入口做有界选择：公开索引中的[顺风123(观音桥大融城店)链接](https://www.dianping.com/shop/HaQlfCl5X2CeJdSs)在真实可见页面跳转大众点评扫码登录；这没有证明当前店名、地址或菜单仍有效。[美团重庆旧入口](https://cq.meituan.com/)跳到企业官网；[移动美食页](https://meishi.meituan.com/i/?ci=45&f=android193359149)在有界等待后仍未加载商家。没有用其他受保护接口绕过登录，也未补造菜单。

当前保留**大众点评为候选首站**，不称用户已经指定或平台已完成业务适配。`dianping-before-login-fix.json` 是真实失败：纯二维码页未被密码/OTP检测覆盖，后续模型请求不可用Vision并PARTIAL_FAILED。`dianping-after-login-fix.json` 保存重启后旧tab关闭的中间暂停；`dianping-manual-paused.json` 才是同一run重绑当前tab后 `authentication_required`。累计2583 tokens保留，修后扫码暂停未增加模型调用。

读取层已识别实见二维码路由，在模型抽取前持久暂停；网页规划入口同样处理manual gate，登录页不满足read outcome。中文“不下单”也不再把只读请求路由成写任务，原请求全文保留。`test_manual_login_resume.py` 的登录后菜单响应为明确受控协议样本，真实登录/门店/菜单/套餐/表单准备仍待用户账号接管。没有真实预约、下单、支付或外部消息。

私有持久数据：`output/merchant-next/session-C0ovDv/`；run `f8f8ad2719074b0a9c62cbb08367892f`。用户报告窗口卡住后重启WSL，数据与登录profile保留；没有证据把卡住归因于某项代码。

## 结构化需求卡：真实同任务5版

私有数据：`output/requirements-next/session-kfOEZ5/`；run `de59413d26ff4b1a958d5d607fc4c6a2`。全部从真实UI操作，模型/高德真实调用，未使用商家fixture。

| 证据 | 实际结果 |
| --- | --- |
| `requirements/01-initial-draft.json` | 重庆解放碑、2人、预算300、步行2km、2026-09-10 14:00；供给未知草案v1 |
| `requirements/02-budget-people.json` | 直接取消预算、人数改3，v2；起点/中心/步行保留；旧说明错误称无需长时间排队，失败原文保留 |
| `requirements/03-locked.json` | UI锁定富豪酒家(大都会店)，同ID/时段、v3；营业/排队仍unknown |
| `requirements/04-unlocked-after-restart.json` | 用户重启WSL后从同profile/SQLite恢复，解锁同站，v4；累计用量未归零 |
| `requirements/05-geography-date-mode.json` | 起点改民族路188号，搜索中心改洪崖洞民俗风貌区，范围2→3km；2026-09-11 15:00、驾车；真实高德对应日期多云预报，v5 |
| `requirements/05-structured-fields.png` | 原生需求卡各字段与服务端TripSpec一致，空预算和3人保持 |
| `requirements/06-saved.json` | 同run v5保存 `draft_ready`，business_completed=false，action_results为空 |
| `requirements/07-restored-grounded-summary.png`、`requirements/actions.json` | 再次重启后原计划加载，UI/分享只显示结构化人数/估算费用/未知项，原自由说明不再提升为事实；没有新增模型用量 |

累计14514 tokens/164 tools；最后轮baseline11948/122，增量2566 tokens/42 tools。每轮上限保持12000 tokens/48 tools/300执行秒，没有提高预算、清除旧失败或把累计值当本轮值。

明确限制：这些是规划/草案和UI需求编辑证据，不是商家可预约或成交证据；完整公共交通路线仍未覆盖。锁定/解锁保持地点和时段，并不冻结过期来源。离线API图回归另覆盖旧审批/版本拒绝、UNKNOWN拒改、清除日期/时间/范围、实际半径缓存键、地理失败后人工澄清及接受编辑后崩溃恢复。

## 回归与安装

`regression-cases.json` 保留8个小场景；`python scripts/inspect_trace.py --check` 复核47条归档断言，输出 `live_tests_run=0`。旧失败/原始累计用量完整保留，后续必要真实调用另行记录；没有做完整角色收益或DOM/Vision sweep。角色选择不再读取历史goal，循环检测只计当前需求流程；后续重规划消息只追加本轮输入，历史原记录保留。

Linux/WSLg试用安装说明见 [试用安装](../../docs/试用安装.md)。独立包、服务、profile与冷备/恢复验收由专属 `plango-trial-check`、`plango-trial-restore-check` 资源完成；中间失败在 `output/trial-check/` 保留。安装验收的受控Cookie、UNKNOWN、审批及队列样本不代表真实业务履约。最终结果清单和包路径以交接文件为准；未发布或push。
