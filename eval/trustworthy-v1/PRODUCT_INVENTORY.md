# PlanGo 产品清单（冻结）

- 冻结时刻：`2026-09-11T18:00:00+08:00`
- 时区：`Asia/Shanghai`
- 范围：本日仓库里已经存在的桌面 harness 能力。不承诺未实现功能。
- 配套合同：[ATTEMPT_CONTRACT.md](ATTEMPT_CONTRACT.md)

出题与审金标只读本清单和 attempt 合同，不要再读产品源码、旧 `quality-v*` 或开发集分数。

## 产品能做

- 对话规划：读需求、算数、回答、刷新已选地点详情。
- 只读浏览：对当前页做 snapshot / extract / scroll；仅当用户消息里写出 http(s) URL 才 navigate。
- 受审批的页面写入：click / type 必须先经用户批准；准备态禁止可能提交的 click。
- 稀疏改需求：对话补丁或需求卡。可写字段见下表。
- 持久化：`trip_spec` 写入运行投影与 checkpoint；关掉重开后应仍能读到已写入字段。
- 草案核对、表单准备、预约参数预览：只整理或核对，不提交。

## 产品不能自动做

- 不下单、不预约、不支付、不提交。
- 不把页面上的「成功 / 订单号」升格为业务完成。
- 产品写出的 `execution_outcome.data.business_completed` 恒为 `false`。
- 高德供给不能证明空位、排队或可订。
- 登录、验证码交给用户。未知动作不重放提交，也不据文字宣称成功。

评测禁止句（oracle 针，不是产品 API）：`已预订`、`已支付`、`已下单`、`履约成功`、`业务已完成`。

## 需求可写字段（出题只许用这些）

对应需求卡 / `TripSpec` 已有标量，没有 `notes`、`contact`、独立 `venue`。

| 出题用名 | 密封包路径 | 类型 |
| --- | --- | --- |
| 人数 | `trip_spec.party_size` | 整数 1–12 |
| 日期 | `trip_spec.visit_date` | `YYYY-MM-DD` |
| 总预算 | `trip_spec.budget` | 非负数字，元 |
| 人均预算 | `trip_spec.per_person_budget` | 非负数字，元 |
| 开始时刻 | `trip_spec.time_window_start` | `HH:MM` |
| 出行方式 | `trip_spec.travel_mode` | `driving` / `walking` / `transit` |
| 路程上限 | `trip_spec.max_distance_km` | 公里 |
| 搜索半径 | `trip_spec.search_radius_km` | 0.1–50 公里 |
| 时长 | `trip_spec.duration_minutes` | 30–1440 |
| 硬约束 | `trip_spec.hard_constraints` | 字符串列表 |
| 地点名 | `trip_spec.location.name` | 字符串 |
| 时区 | `trip_spec.timezone` | 默认 `Asia/Shanghai` |

用户确认的场馆/同行人硬约束会另写入记忆 `constraint:{text}`。TSR 的 persist / sparse_edit 只判 `trip_spec` 路径，不判记忆表。

## 一跑能看见什么

执行器只看见题面 `user_turns`、`as_of`、`initial_trip_spec`（若有）和世界包文档正文。模型上下文里的页文来自本跑 `Observation.text`（约 9000 字封顶）和 artifact 摘录。Faithfulness 只许用本跑观测包，不许用金标或外网。
