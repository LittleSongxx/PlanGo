# holdout-v2（未见集，金标未独立审）

相对 `eval/trustworthy-v1/holdout` 的新题面：新合成实体、数字和问法。v1 目录未覆盖，仍作历史污染集保留。

- `evaluation_kind=holdout_unreviewed`
- `report_kind=provisional_holdout`（独立审金标之前，不得报 official TSR / Faithfulness）
- 规模：6 层 × 34 题 = 204
- 执行器只读 `tasks.json` 与 `worlds.json`，不要打开 `oracles.json`

直播（真实模型，仍不爬活网）：

```bash
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py run \
  --dataset eval/trustworthy-v1/holdout-v2 \
  --output output/trustworthy-v1/holdout-v2-attempts.json \
  --live --work-dir output/trustworthy-v1/runner-work-holdout-v2 --timeout 300
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py score \
  --dataset eval/trustworthy-v1/holdout-v2 \
  --attempts output/trustworthy-v1/holdout-v2-attempts.json \
  --output output/trustworthy-v1/holdout-v2-report.json \
  --judge llm
```

`run` 不得打开 `oracles.json`。本趟分只作当前代码的暂定验证。
