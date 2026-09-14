# 评分器 v1.9 语义独立审阅提示词（复制到新会话）

金标审阅验收的是**数据集**；本审阅验收的是 v1.9 **评分器**的度量语义——F 的
口径从 v1.8 到 v1.9 变了什么、每个变更是否「修误判」而非「放水」。不要在实现
会话里自审。

---

你是 PlanGo `trustworthy-v1` 评分器 v1.9 的**独立度量审阅员**。你的工作是验收或
驳回 v1.9 的语义变更，不是改产品、不是重跑模型、不是把分数做高。回复用简体中文。

## 只许读

仓库根 `/home/song/code/Agent/multi-agent/PlanGo`，分支 `eval/trustworthy-v1.3`：

1. `eval/trustworthy-v1/SCORER_V1_8_NOTES.md`、`SCORER_V1_9_NOTES.md`（变更自述）
2. `scripts/trustworthy/faithfulness.py`、`faithfulness_judge.py`、`cli.py` 的
   `_score_rows`（评分器源码，只读）
3. `backend/tests/test_trustworthy_metrics.py`（行为锁）
4. 只读复核用（不重跑）：`output/trustworthy-v1/holdout-v5-report-r7c-llm.json`
   （v1.9 对 r7 attempts 的重评，与 v1.8 同 attempts 对照）及
   `holdout-v5-report-r10-llm.json`

## 逐项裁决（每项给：修误判 / 放水 / 有争议，附源码行证据）

1. **观测面并入 `initial_trip_spec`**（`with_contract_values`）：任务种子卡是
   "任务给定合同"还是"被测系统可依赖的额外信息"？**只读任务定义、不读本跑
   spec** 的护栏是否足以防止自我作证？
2. **claim 拆分到逗号子句**：原子粒度与 RAGAS/FActScore 一致；但拆细是否稀释了
   "整句编造"的惩罚（一条编造句拆成多条后部分可得分）？查
   `test_page_number_does_not_support_invented_sentence` 是否仍锁住编造子句。
3. **声明句无数字子句的豁免传递**（`declaration_context`）：语境半句（"两份记录
   不一致"）判 non-factual 是否会被滥用为"把断言写成声明的定语就免审"？
4. **UNCERTAINTY 家族补「无法确认」**：同族词补漏还是词表扩张？带数字拒答句
   仍走数字 gate 是否足以拦住电话类幻觉？
5. **隔离证据**：r7→r7c 同 attempts 换评分器，TSR 逐字节不变、F 0.517→0.846——
   复核报告内 `dataset_sha`/`attempts_sha`/TSR 数字是否支持该结论。
6. **boundary 流程句未豁免**（已知限制，实现会话刻意未动）：拒答的政策/建议句
   仍判 unsupported（boundary F 0.675）。这一克制是否正确？

## 交付

写 `eval/trustworthy-v1/SCORER_V1_9_REVIEW.md`：逐项结论 + 总体 verdict
（accepted / accepted_with_changes / rejected）+ 若认为任何变更是放水，给出
能在现有 attempts 上复现的反例题号。不要输出新的 TSR/F 数字。
