# 新账本下的揭示六题完整回归

2026-09-10。同一六题（NEW-01/07/14/18/21/29）、同一 gold，**新独立账本**跑完全部六题。这是回归，不是未见泛化；**未计算 TSR**，不把采集停点当质量提升。

- 数据集：本目录；gold/tasks/sources/runtime-fixtures 与 [quality-v5-regression-six](../quality-v5-regression-six/) 逐字节一致。
- 产品冻结：`output/quality-post-loop/product-freeze.json`（`03673618`，`worktree_dirty: true`）。
- 账本：`output/quality-v5-r3/model-budget.jsonl`（80 calls / 120000 tokens，本轮用 11 次 / 21180 tokens）。
- 采集原件：`output/quality-v5-r3/regression-session/batch-20260910T152106Z-eea9bc/`。
- 旧 `eval/` 成绩与 gold **未改**。未写 `scores.json`（无独立输出审核）。

| 题 | 采集停点 | 调用 | tokens |
| --- | --- | ---: | ---: |
| NEW-01 | unscripted_clarification（索要菜单，未用受控资料） | 1 | 1776 |
| NEW-07 | completed（A/B 对照 + 不够算总花费） | 3 | 5252 |
| NEW-14 | completed（步行 vs 公交候车未知） | 4 | 5834 |
| NEW-18 | script_finished（Electron 丢响应恢复；开始时间改为 19:20） | 1 | 3223 |
| NEW-21 | completed（到店卡，但 0 次工具，桌费写成待确认） | 1 | 1777 |
| NEW-29 | unscripted_clarification（人数改 4，180 口径未替用户选） | 1 | 3318 |

未见集仍须另开互不读产品的编题/审金标会话。
