# 观察当前页之后的冻结未见集（v7）

2026-09-10。产品冻结于 `0367361`（`2026-09-10T09:29:28Z`）后，由一个全新上下文独立编题，另一个全新上下文独立审判据并跑测。`import_reviews` 状态 `complete`，issues 空。**TSR 0/6。**

本目录自本轮起即为已揭示集，后续只能作回归。这六题与旧六题、v6 不可比分：题目、资料、实体全新，`must_pass` 每题 4–6 条。

## 结果

| 题 | family / class | driver | outcome | 成功 | 说明 |
| --- | --- | --- | --- | --- | --- |
| v7-reading-yanhu-handcraft | reading / delivery | read | completed | | 资料取到，零作答 |
| v7-offers-wujing-diner | offers / bounded | read | completed | | 同上 |
| v7-routes-qinghuai-walk | routes / delivery | read | completed | | 同上 |
| v7-edits-luting-teahouse | edits / delivery | edit | product_failure | | 两处改动正确，交付降成 INFEASIBLE |
| v7-recovery-mianyu-studio | recovery / delivery | save_restart | script_finished | | 保存/重启通过，优惠标价保留项失败 |
| v7-boundaries-taoke-workshop | boundaries / bounded | read | completed | | 资料取到，零作答 |

`import_reviews`：successes 0 / valid 6，TSR 0，Wilson 95% [0.0%, 39.0%]。Groundedness 宏平均 98.25%，微平均 73/75。**这两个 Groundedness 数字不能当质量信号**：四道 read 题的 supported 几乎全是资料原文摘录卡，作答为空。

原件在 `output/tightening-F9A9ea/independent-v7-session/`（gold-review、output-review、output-bundle）。共享账本本批后为 **67/80 调用、112706/120000 tokens**。

## 第 1 条修过了，第 2 条被新的断点挡住

四道 `driver=read` 的题里，模型第一次决策仍然选择 `navigate` 到高德或点评。执行层把用户未给出的地址换成了对当前页的 `extract`。四题各有且仅有一条 `operation=extract`、`ok=true`、`outcome=observed`，受控资料进入了卡片。

**所以「先看当前页」在执行层成立。** 外站导航没有发出。

取回之后没有第二次模型调用。事件序列是：`task_decided(read)` → 浏览器中断 → `BROWSER_OBSERVATION` → `task_decision_unusable{error: model_token_budget}` → 固定话术「本轮未能形成可用的下一步，已保留读到的资料」。第一次调用大约 1900–2000 tokens 之后，下一次提示（中文说明 + 决策 schema + 刚读到的页面）的准入估计超过剩余额度，适配器直接回退，循环结束。预算不是原因：单题上限 12 次，本批总共 7 次调用。

失败按层：**控制流**（资料到手后决策被额度估计掐掉，四题零作答）> 资料路由（模型意图仍要外跳，执行层已拦住）> 交付组装（edits 把排队未知当成硬约束，整单 INFEASIBLE）。意图理解正常；算术从未执行。

## 独立性与额度

编题在冻结之后。作者为确认冻结读过 `product-freeze.json`，因而看到了实现路径和 `skills/` 文件名；审核者评估为真实但有限的独立性瑕疵，并刻意未读该冻结文件。双方都披露了 `AGENTS.md` 自动注入。gold 6 题 approve、0 reject。

**本目录不能再用来声称后续修改的未见泛化。** 交付组装的修复（取回页面后把下一次决策压进剩余额度）发生在本批跑完之后，效果未在未见集上测量。共享账本只剩 13 次调用 / 7294 tokens，不够再跑一轮完整六题；下一轮未见集需要新的额度账本。
