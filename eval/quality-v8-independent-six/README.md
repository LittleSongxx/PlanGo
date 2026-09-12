# quality-v8-independent-six 采集完成（未见集已揭示）

2026-09-10。冻结后独立编题、独立审金标（6 题 approve），本仓库只做 prepare / 跑测 / 打包。**未计算 TSR**，输出审核留给另一个独立会话。

- 数据集：`eval/quality-v8-independent-six/`
- 冻结：`output/quality-post-loop/product-freeze.json`（`03673618`，dirty）
- 账本：`output/quality-v8/model-budget.jsonl`（本轮 12 次 / 25958 tokens）
- 原件：`output/quality-v8/independent-session/batch-20260910T154527Z-c2c9a6/`
- 打包：`output/quality-v8/independent-session/output-bundle.json`
- gold 未改。作者多写的实体卡在 `sources.authored.json`，采集用的 `sources.json` 只有 6 条主资料。

| 题 | 采集停点 | 调用 | tokens |
| --- | --- | ---: | ---: |
| v8-read-moss-paper-fees | completed（226 元 + 未知分层） | 3 | 6537 |
| v8-offers-stringwood-combos | completed（保留资料、无下一步） | 3 | 5983 |
| v8-routes-cypress-conflict | unscripted_clarification（索要已给的步行记录） | 1 | 1644 |
| v8-edit-copper-button-draft | script_finished（Electron 两轮改动） | 2 | 6825 |
| v8-recover-glass-gallery | script_finished（保存/重启，0 次模型） | 0 | 0 |
| v8-bound-ebb-tide-access | unscripted_clarification（追问城市与日期） | 3 | 4969 |

本目录自本轮采集起即为已揭示集。TSR 须等独立输出审核后才能算，且不可与旧六题/v6/v7 比分。

揭示之后的调用链修复与各轮**采集停点**对照（仍不是分数）在 [output/quality-v8-r6/README.md](../../output/quality-v8-r6/README.md)。旧 freeze / 旧 batch 均未覆盖。当前完整六题复跑原件是 `output/quality-v8-r6/session/batch-20260910T165244Z-b52d98/`。
