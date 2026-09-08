# 受控原生表单准备链：最终验收通过

在原任务 `d51586748f934797a6c8b202ccd8df5b`、原 profile 与原 SQLite 上，已通过真实 Electron UI 完成：保存草案 → 刷新原门店身份证据 → 仅准备表单 → 逐项批准时间输入 → 3 人 `ready_to_review` → 仅一次人数编辑为 4 → 新版本/新审批 → 逐项批准人数输入 → 4 人 `ready_to_review`。

终态是方案 **v4**，原生表单实际值为 **4 人、2026-09-09 18:31**。后端结果 `business_completed=false`，`plan_verification=draft`，原有 2 条待核验事项完整保留。它证明受控页面的参数准备与核对闭环，**不证明真实门店接受预约或产生订单**。

## 验收范围与隔离

- Electron、SQLite 后端、配置中的真实模型及高德身份查询均真实运行；业务表单内容是明确标注的本地受控样本。
- 姓名/地址来自 UI 选中的规范高德 POI；样本中的价格、营业、座位文字不是对真实门店的事实声明。
- 最初 localhost 的自动填写被生产站点策略正确拦截，原值不变。随后按项目 `browser-regression.cjs` 已有办法，仅在测试 Electron 的 `host-resolver-rules` 将 `fixture.meituan.com` 映射到 `127.0.0.1`，仍访问同一个端口 55887 的本地表单。生产 allowlist、系统 hosts 与真实商家站点均未修改。
- 所有任务创建、更正、草案选择、继续核对和操作批准均通过真实 UI；没有直接注入 Goal、plan、批准状态或 browser observation，也没有直接设置表单值来凑结果。
- 原失败任务、checkpoint 与回执保留。额外新建的起点误判任务仅保留失败证据并从 UI 停止，不用于替代原任务恢复。

## 最终证据

| 证据 | 内容 |
|---|---|
| `17-final-ready-card-priority.png` | 最终首屏：准备就绪、4 人、18:31、待核验事项、未提交范围 |
| `16-native-four-person-form.png` | 系统级截图：真实 WCV 原生表单值 4 / 18:31，页面明确标注受控样本 |
| `four-person-ready-to-review.json` | 原 run v4 最终 canonical snapshot、Goal、来源与范围 |
| `three-person-preparation-result.json` | v3 时间修正后准备结果 |
| `four-person-new-draft.json` | 单次人数编辑后的 v4 新草案与新 interrupt |
| `final-command-ledger.json` | 实际 SQLite 命令审计 |
| `actions.json` | UI 行为、恢复、版本失效、预算 baseline 与清理记录 |
| `localhost-write-correctly-blocked.json` | localhost 写入被站点策略正常阻止的正向权限证据 |

实际命令共 **17 条：12 extract、2 snapshot、3 type**。3 条 type 中，localhost 的一次被拦截；支持域名映射的两次经 UI 批准后真实执行，分别将时间 **18:30 → 18:31**、人数 **3 → 4**。没有 click 命令，本地 `/reserve` 提交计数自服务启动至关闭始终为 **0**。

人数编辑后，原 Goal、原 Outcome 与 preparation-resume 元数据先被清除，v4 使用新 plan/interrupt/approval；原 v3 确认入口不再出现。这里只将实际 UI 和持久状态所证明的失效记为通过，没有通过直接 API 重放旧批准来改变状态。

最终累计模型用量为 117705，本轮 baseline 为 91945；固定 cap 保持 60000，累计历史没有清零，也没有把 cap 提高到 120000。此次是一个场景的诊断与修后恢复，不是质量指标测评。

最终界面排序、折叠过程、来源/范围说明已在真实 Electron 中查看。辅助的类型检查、UI 授权边界回归与构建通过。隔离 Electron、后端 56427、表单服务 55887 和 runner 均已关闭；数据保留在 `output/preparation-e2e/session-plD5Ds`。

## 实施期间阶段记录（历史证据）

以下记录保留当时的失败与中间状态；当前验收结论以上述终态及对应证据为准。

### 初始阶段

这是本地受控页面与真实 Electron/模型/SQLite 后端的用户流程验收，不是真实商家履约，也不是质量指标测评。

- 隔离 profile：`output/preparation-e2e/session-plD5Ds`。
- 独立 API：`127.0.0.1:56427`；本地页面：`127.0.0.1:55887/restaurant`。
- 通过真实 UI 的高德发现选择 canonical POI `amap:B0MD1HTUN5`，读取后端规范姓名与地址用于表单身份；供给与价格是本轮受控样本内容，不能解释为真实门店事实。
- 页面包含普通 `form method=get action=/reserve`、人数 number、预约日期 date、预约时间 time、同表单“确认预约”submit button。服务端累计 `/reserve` 请求数必须为 0。
- 所有任务创建、要求更正均通过实际 UI；没有注入 ExecutionGoal、plan、审批或合成 browser observation。

当前 run `d51586748f934797a6c8b202ccd8df5b` 在需求澄清阶段暴露问题：

1. 初始选择地点后，UI 补充 3 位成人和不设预算，仍保留默认预算并将人数计为 4（用户 1 + 成人 3）。
2. UI 更正总共 3 人后，旧角色计数仍为 4；“当前网页这家餐厅”又被送去地理解析，产生山东坐标，被当成地区变更。
3. UI 再次明确保留重庆原门店和总共 3 人后，旧角色计数触发冲突，仍未产生地点候选/方案/审批/ExecutionGoal。

上述状态原文保存在三个 requirements JSON 与 `actions.json`；`02-controlled-native-form-blocked.png` 是包含真实 WebContentsView 的系统级截图。当前提交计数为 0。后端修复后的复验结果将继续记录于此目录；在真正达到 `ready_to_review` 前不把此场景标为成功。

## 原任务修后恢复（继续实施）

- 新增的 `bff5d130840f4a9ab9517257ef0b8c43` 仅保留“目前”被误当起点的额外失败证据，随后从 UI 停止；它不用于替代原任务恢复。
- 原任务已从真实历史入口恢复，明确 UI 更正后 `party_size=3`、角色计数合计 3、预算为 `null`、日期与时长正确，并恢复重庆规范搜索中心。
- UI 已实际生成独立草案卡，保留 unknowns；身份来源过期时“仅准备表单”保持禁用。“保存草案”已实际点击成功，`scope=draft_ready`、`business_completed=false`，旧 interrupt 清除。证据：`original-run-draft-saved.json`、`05-original-run-draft-saved.png`。
- 同一个原任务再由 UI 请求“重新核验这家门店，其他要求不变”。高德按原 ID 刷新身份来源，新的 `selected-poi:<run>:9` 证据真实传播到 PlanStop，v3 草案 `can_prepare=true`。证据：`refreshed-draft-can-prepare.json`。
- UI 已点击“仅准备表单”，服务器实际生成 `ExecutionGoal.kind=itinerary_preparation`、`plan_verification=draft`，原 pending_checks 完整保留；没有直接注入 Goal/plan/批准状态。
- 准备首个读取暴露跨 Electron 重启的旧 tab ID；两个读取均真实返回 `blocked/tab_closed`，UI“已处理，继续”当时仍沿用旧 tab。此恢复问题已交后端窄修，准备完成和人数修改尚不能在该阶段记为通过。
- 模型累计用量未清零；当前每次明确用户轮次使用新 baseline，按轮固定上限为 60000。已记录 baseline 56381 与累计 85213 的实际状态，本轮未通过提高 60000 上限或清零累计值继续。

本阶段 `/reserve` 提交计数始终为 0。
