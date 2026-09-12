# holdout（金标已审，等待 runner）

独立审金标已通过：`gold_review.json` 的 `verdict=accepted`，`errata=[]`。题面 / 世界包 / oracle 未被审查会话改写。

- `evaluation_kind=holdout_reviewed`
- `report_kind=provisional_holdout`（有真实 runner 密封 attempt 之前，不得报 official TSR / Faithfulness）
- 规模：6 层 × 34 题 = 204
- 执行器只读 `tasks.json` 与 `worlds.json`，不要打开 `oracles.json`
- 审查记录：[gold_review.json](gold_review.json)

两条审查 note（不构成勘误）：模板重复但层名实相符；未使用 `marker_absent`（允许集不是必用集）。

直播（真实模型，仍不爬活网）：

```bash
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py run --dataset eval/trustworthy-v1/holdout --output output/trustworthy-v1/holdout-attempts.json --live --work-dir output/trustworthy-v1/runner-work
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py score --dataset eval/trustworthy-v1/holdout --attempts output/trustworthy-v1/holdout-attempts.json --output output/trustworthy-v1/holdout-report.json --judge llm
```

`run` 不得打开 `oracles.json`。有密封 attempt 之前不要报 official 分。
