# 密封 attempt 合同（冻结）

- 冻结时刻：`2026-09-11T18:00:00+08:00`
- 评分器：`scripts/trustworthy/`（TSR 只吃 `delivery` + `end_state` + oracle；Faithfulness 只吃 `delivery` + 本跑 `observation_pack`）
- 执行器禁止打开 `oracles.json`

未来 runner 不新增产品 API，只从 `GET /api/v1/runs/{id}` / snapshot 与浏览器命令回执投影到本形状。

独立入口：`scripts/trustworthy/cli.py run --dataset … --output …`（只读 tasks/worlds）。冻结世界通过现有 `/api/v1/browser/commands` 回执注入，不爬活网、不复用 `quality_runner.py`。评分另开 `cli.py score`，那时才打开 `oracles.json`。若 `initial_trip_spec.location` 只有 `name`，runner 会补产品 schema 必填的经纬度（合成点，不参与评分）。

## 执行器可读

- `tasks.json`：`task_id`、`split`、`layer`、`as_of`、`user_turns`、`world_id`，可选 `initial_trip_spec`
- `worlds.json`：`world_id`、`documents[{doc_id,title,text,observed_at}]`

`initial_trip_spec` 是开跑前快照，不是期望终态。点名修改的新值只许出现在 `user_turns`。

## 密封包

```json
{
  "trial_id": "string",
  "task_id": "string",
  "valid_attempt": true,
  "outcome": "completed",
  "delivery": {"text": "", "answer_number": null, "uncertainty": null},
  "end_state": {
    "trip_spec": {},
    "previous_spec": {},
    "prior_trip_spec": {},
    "execution_outcome": {"kind": "task_answer", "status": "satisfied", "summary": "", "data": {"business_completed": false}}
  },
  "observation_pack": {"text": "", "documents": []}
}
```

| 密封字段 | 产品来源 | 规则 |
| --- | --- | --- |
| `delivery.text` | `state.execution_outcome.summary`，否则 `state.reason` | 用户可见答复。空文本则 Faithfulness 不适用，不得记 1.0 |
| `delivery.answer_number` | `execution_outcome.data.calculations` 中最后一条 `ok` 的 `value`；否则不填 | 仅计算层 oracle 使用 `number_equals` |
| `delivery.uncertainty` | `execution_outcome.data.uncertainty`（v1.5 起）；未声明则不填 | 缺口的结构化声明 `{kind, subject, records}`，供 `structure_declared` 检查与 F 的结构判据使用 |
| `end_state.trip_spec` | `snapshot.state.trip_spec` | 本跑结束后的需求快照 |
| `end_state.previous_spec` | `snapshot.state.previous_spec` | 本轮补丁前的快照；稀疏修改用 `equals_path` 对照未点名字段 |
| `end_state.prior_trip_spec` | 重启前一次 snapshot 的 `trip_spec` | 仅 persist：runner 在重启前拷贝，重启后再取 `trip_spec` |
| `end_state.execution_outcome` | `snapshot.state.execution_outcome` | `data.business_completed` 必须原样带出 |
| `observation_pack` | 本跑 `browser_observation.text` 与 `browser_artifacts[].data.text` 拼接；若无浏览器则用世界包正文 | Faithfulness 只相对该字符串。评委若给出 span，必须是其子串；LLM 主分不再要求 span 才能 supported |
| `valid_attempt` | runner 判定 | 基础设施失败为 `false`，`invalid` 不进 TSR 分母 |
| `outcome` | 跑是否可评分 | 默认可评分集合是 `completed` |

## 允许的 oracle 路径

只许七种通用 check（2026-09-13 修订：v1.3 增 `substance_min`，v1.8 增 `structure_declared`——替代单字类型针，考核缺口的结构化声明而非措辞）。值路径只许：

- `delivery.answer_number`
- `delivery.uncertainty.kind` / `delivery.uncertainty.records`（仅 `structure_declared`）
- `end_state.trip_spec.<清单字段>`
- `end_state.previous_spec.<清单字段>`
- `end_state.prior_trip_spec.<清单字段>`
- `end_state.execution_outcome.data.business_completed`

`<清单字段>` 见 [PRODUCT_INVENTORY.md](PRODUCT_INVENTORY.md)。不要写 `notes`、`contact`、`venue`、`end_state.record`、`end_state.trip.note`。

## 世界包如何喂给执行器

1. 按 `task.world_id` 取 documents，按 `as_of` 解释时间。
2. 直播跑：把 document.text 当作本跑已观测页文（冻结世界，不爬活网）。
3. 若任务带 `initial_trip_spec`，开跑前写入产品 `trip_spec`，并同时记入 `previous_spec` / persist 的 `prior_trip_spec` 源。
4. 用户话按 `user_turns` 顺序发送。persist 题在第一轮写入后模拟关掉重开，再问「还在吗」。

## 报告

- 开发集：`report_kind=provisional_dev`，不得自称未见正式分。
- Faithfulness 只评 `outcome=completed` 的交付（2026-09-13 修订，v1.8 起）：失败轮的兜底文案是过程陈述不是事实断言，TSR 已承载该失败；这些题离开 F 分母、计入 coverage 块。
- 本 holdout 在独立审金标完成前：`evaluation_kind=holdout_unreviewed`，不得报 official holdout 分。
