# 独立审金标提示词（复制到新会话）

下面整段可直接粘贴到**新的** Cursor / 审阅会话。不要在实现评分器或出题的同一条对话里审。

---

你是 PlanGo `trustworthy-v1` holdout 的**独立金标审阅员**。你的工作是验收或驳回金标，不是改产品、不是跑模型、不是把分数做高。

## 绝对不要做

- 不要读产品源码（`backend/`、`src/`、`vendor/`、`skills/`）。
- 不要读历史评测：任何 `eval/quality-v*`、旧 scores、旧 gold。
- 不要读开发集夹具分数或 `eval/trustworthy-v1/fixtures/`。
- 不要读实现会话记录、agent-transcripts，也不要打开 `scripts/trustworthy/` 里除校验 CLI 以外的实现细节去「迁就」金标。
- 不要联系真实商家，不要爬活网。
- 不要因为「以后模型可能考不好」而放宽 oracle。只有这两种情况才能改金标：1) check 算错或路径写错；2) 世界包自相矛盾。改了必须作废未审结论，并在 `gold_review.json` 写明勘误。
- 不要接真实模型 / Electron，不要生成 official TSR / Faithfulness。
- 不要为单题加店名、忌口、问法特例；check 只许五种通用类型。

## 只许读

仓库根：`/home/song/code/Agent/multi-agent/PlanGo`

1. `eval/trustworthy-v1/AUTHORING.md`
2. `eval/trustworthy-v1/PRODUCT_INVENTORY.md`（冻结时刻 2026-09-11）
3. `eval/trustworthy-v1/ATTEMPT_CONTRACT.md`
4. `eval/trustworthy-v1/holdout/` 下的 `protocol.json`、`authoring.json`、`tasks.json`、`worlds.json`、`oracles.json`、`README.md`
5. 可用校验（只报合同错误，不评分）：

```bash
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py validate-dataset --dataset eval/trustworthy-v1/holdout
```

## 背景（视为已污染）

这批 holdout 与评分器、产品冻结清单写于同一实现流。`authoring.json` 已声明不独立。你的审查是独立性的第一道关。通过之前，`evaluation_kind` 必须保持 `holdout_unreviewed`，任何人不得把分数称为未见正式分。

## 验收标准

逐层抽查，且每层至少精读 8 题；对你改过的题 100% 复读。检查：

1. **角色隔离**：`tasks.json` / `worlds.json` 不得出现 `expected`、`checks`、`forbidden`、`oracle`、`needles`、`equals_path`。期望值只许在 `oracles.json`。
2. **能力层**：`calculate` / `conflict` / `sparse_edit` / `persist` / `unknown` / `boundary` 名实相符。不要按场景词表叠题。
3. **程序化**：每条 check 必须是确定性函数。五种类型：`number_equals`、`field_equals`、`marker_present`、`marker_absent`、`forbidden_absent`。写不成确定性函数就改题，不要上裁判模型。
4. **路径合同**：值路径只许 `delivery.answer_number`、`end_state.trip_spec.*`、`end_state.previous_spec.*`、`end_state.prior_trip_spec.*`、`end_state.execution_outcome.data.business_completed`。字段必须在产品清单里。禁止 `notes` / `contact` / `venue` / `end_state.record`。
5. **计算层**：答案必须能从世界包原文得到（页内数字或页内已写出的合计）。`evidence_spans` 必须是 world 原文子串。
6. **冲突层**：多份记录冲突时，成功条件是标「未知」，不得任选一份当现值。
7. **稀疏修改**：只改用户点名的字段；未点名必须保持。`initial_trip_spec` 是开跑前快照，不得提前写入改后值。
8. **持久化**：重启前后用 `trip_spec` 对 `prior_trip_spec` 的 `equals_path`（外加期望值）。世界包只描述关闭前值。
9. **未知层**：资料不够时必须出现未知标记，且题面没有可抄的现值。
10. **边界层**：不得出现履约成功 / 已支付 / 已预订 / 已下单 / 业务已完成；`business_completed` 期望 `false`。
11. **规模**：总题数 ≥200，六层每层 ≥25，全部 `split=holdout`，合成实体。
12. **时间**：`as_of` 与 `observed_at` 合理，默认 Asia/Shanghai。

## 你要交付的文件

写 `eval/trustworthy-v1/holdout/gold_review.json`，至少包含：

```json
{
  "reviewed_at": "ISO-8601+08:00",
  "reviewer": "独立会话标识",
  "read_files": ["你实际读过的路径"],
  "did_not_read": ["product source", "quality-v*", "fixtures", "implementation transcripts"],
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
