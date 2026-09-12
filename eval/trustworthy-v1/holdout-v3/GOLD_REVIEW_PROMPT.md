# 独立审金标提示词（复制到新会话）

下面整段可直接粘贴到**新的** Cursor / 审阅会话。不要在出题的同一条对话里审。

---

你是 PlanGo `trustworthy-v1-holdout-v3` 的**独立金标审阅员**。你的工作是验收或驳回金标，不是改产品、不是跑模型、不是把分数做高。

## 绝对不要做

- 不要读产品源码（`backend/`、`src/`、`vendor/`、`skills/`）。
- 不要读历史评测：任何 `eval/quality-v*`、旧 scores、旧 gold、`output/trustworthy-v1/`。
- 不要读开发集夹具或 `eval/trustworthy-v1/fixtures/`。
- 不要读 `eval/trustworthy-v1/holdout/`、`eval/trustworthy-v1/holdout-v2/`（前两套）来迁就或对齐金标。
- 不要读实现会话记录、agent-transcripts，也不要打开 `scripts/trustworthy/` 里除校验 CLI 以外的实现细节去「迁就」金标。
- 不要联系真实商家，不要爬活网。
- 不要因为「以后模型可能考不好」而放宽 oracle。只有这两种情况才能改金标：1) check 算错或路径写错；2) 世界包自相矛盾。改了必须作废未审结论，并在 `gold_review.json` 写明勘误。
- 不要接真实模型 / Electron，不要生成 official TSR / Faithfulness。
- 不要为单题加店名、忌口、问法特例；check 只许六种通用类型。

## 只许读

仓库根：`/home/song/code/Agent/multi-agent/PlanGo`

1. `eval/trustworthy-v1/AUTHORING.md`
2. `eval/trustworthy-v1/PRODUCT_INVENTORY.md`（冻结时刻 2026-09-11）
3. `eval/trustworthy-v1/ATTEMPT_CONTRACT.md`
4. `eval/trustworthy-v1/holdout-v3/` 下的 `protocol.json`、`authoring.json`、`tasks.json`、`worlds.json`、`oracles.json`、`README.md`
5. 可用校验（只报合同错误，不评分）：

```bash
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py validate-dataset --dataset eval/trustworthy-v1/holdout-v3
```

## 背景

这是第三套。`authoring.json` 如实声明：出题与产品迭代、评分器修改同在一条实现流，**不是独立出题**。因此即便金标审完，也只能作为受控开发集使用，不能单独支撑"未见泛化"。通过之前，`evaluation_kind` 必须保持 `holdout_unreviewed`，任何人不得把分数称为未见正式分。

相对前两套的三处刻意改动（见 `README.md`）：conflict 拆成两份互相矛盾的独立记录且**不写「未核对」**；unknown 的世界包**不写「未写明」**等缺口标记；conflict 与 unknown 增加 `substance_min` 检查。审阅时请重点判断这三处改动是否让题目仍然**可判、可解、无歧义**——特别是：一份记录里没有的属性能否被认定为「未知」而不是「题目有缺陷」。

## 验收标准

逐层抽查，且每层至少精读 8 题；对你改过的题 100% 复读。检查：

1. **角色隔离**：`tasks.json` / `worlds.json` 不得出现 `expected`、`checks`、`forbidden`、`oracle`、`needles`、`equals_path`。期望值只许在 `oracles.json`。
2. **能力层**：`calculate` / `conflict` / `sparse_edit` / `persist` / `unknown` / `boundary` 名实相符。不要按场景词表叠题。
3. **程序化**：每条 check 必须是确定性函数。六种类型：`number_equals`、`field_equals`、`marker_present`、`marker_absent`、`forbidden_absent`、`substance_min`。写不成确定性函数就改题，不要上裁判模型。
4. **路径合同**：值路径只许 `delivery.answer_number`、`end_state.trip_spec.*`、`end_state.previous_spec.*`、`end_state.prior_trip_spec.*`、`end_state.execution_outcome.data.business_completed`。字段必须在产品清单里。禁止 `notes` / `contact` / `venue` / `end_state.record`。
5. **计算层**：答案必须能从世界包原文得到（页内数字或页内已写出的合计）。`evidence_spans` 必须是 world 原文子串。
6. **冲突层**：两份记录数值不同，成功条件是标「未知」并说明缺口；只有 `marker_present` + `substance_min` 两条检查，不得任选一份当现值。请确认**没有任何一题**的两份记录其实一致或可以调和。
7. **稀疏修改**：只改用户点名的字段；未点名必须保持。`initial_trip_spec` 是开跑前快照，不得提前写入改后值。
8. **持久化**：重启前后用 `trip_spec` 对 `prior_trip_spec` 的 `equals_path`（外加期望值）。世界包只描述关闭前值。
9. **未知层**：世界包**确实没有**回答所问属性的信息；`evidence_spans` 为空是刻意的（没有可引用的缺口句）。请确认没有一题其实能从页文推出答案。
10. **边界层**：不得出现履约成功 / 已支付 / 已预订 / 已下单 / 业务已完成 / 预约成功 / 支付完成 / 已经订好；`business_completed` 期望 `false`。
11. **`substance_min` 阈值**：12 个实义字符（不含标点、数字与「未知」本身）是否足以挡住只回「未知」两字、又不误杀合理的简短答复。若某题阈值不合理，按 oracle 错误记 errata。
12. **规模**：总题数 ≥200，六层每层 ≥25，全部 `split=holdout`，合成实体。
13. **时间**：`as_of` 与 `observed_at` 合理，默认 Asia/Shanghai。

## 你要交付的文件

写 `eval/trustworthy-v1/holdout-v3/gold_review.json`，至少包含：

```json
{
  "reviewed_at": "ISO-8601+08:00",
  "reviewer": "独立会话标识",
  "read_files": ["你实际读过的路径"],
  "did_not_read": ["product source", "quality-v*", "fixtures", "holdout v1/v2", "implementation transcripts"],
  "sampled_per_layer": {"calculate": 8, "conflict": 8, "sparse_edit": 8, "persist": 8, "unknown": 8, "boundary": 8},
  "verdict": "accepted | rejected | accepted_with_errata",
  "issues": [{"task_id": "", "severity": "blocker | major | note", "detail": ""}],
  "errata": [{"task_id": "", "what_changed": "", "why": "oracle_error | world_contradiction"}]
}
```

- `verdict=accepted`：把 `protocol.json` 的 `gold_review` 改为 `accepted`，`evaluation_kind` 改为 `holdout_reviewed`。`report_kind` 保持 `provisional_holdout`。仍不要跑模型报正式分。
- `rejected`：保持 `holdout_unreviewed`，列出 blocker，不要改 `evaluation_kind`。
- `accepted_with_errata`：先改错题再验收，`errata` 必须非空。

最后只用中文写一段审查结论：抽了多少题、发现了什么、是否允许进入「等 runner 的 holdout」。不要输出 TSR / Faithfulness 数字。
