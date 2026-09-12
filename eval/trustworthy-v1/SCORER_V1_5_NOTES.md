# 评分器 v1.5 修订记录（2026-09-12）

设计来源：`PROPOSAL_STRUCTured_GAP_v1.5.md`（同名文件 `PROPOSAL_STRUCTURED_GAP_v1.5.md`）。
v1.4 报告与 attempts 一律不动；v1.5 的评分一律写新文件名（`*-llm-v1.5.json`）。

## 1. 改了什么：缺口从「词表猜文本」变成「结构化声明」

v1.4 的实证教训：产品把缺口写成「资料缺该值」，不在 `UNCERTAINTY`/`GAP` 任何一族里，
r3 的 unknown 层 39 条断言只有 7 条 supported（`holdout-v3-report-r3-llm.json`）。
每次换说法就得扩词表，两侧（产品补丁 + 评分词表）都在过拟合问法。

v1.5 让缺口成为数据：

1. **产品侧**（`backend/plango/task.py`）：`TaskDecision` / `DeliveryDecision` 新增
   `uncertainty: {kind: missing_value | conflicting_records, subject, records[]}`，
   说明写在字段 description 里随结构化 schema 送达（不占指令 token 预算）。
   交付合同的补「未知」标记改为由结构触发（`uncertainty is not None` 或旧 `_UNCERTAIN_SPEECH`
   兜底），结构经 `execution_outcome.data.uncertainty` 进入 attempts 的 `delivery.uncertainty`
   （`graph.py` answer 路径 + `scripts/trustworthy/project.py delivery_from`）。
2. **评分侧**（`scripts/trustworthy/faithfulness.py`）：
   - `kind=missing_value` 时，**无数字**且含 `subject` 的句子判 non-factual（`by: structure`）。
     页上没有该值、无数字可引用，这样的句子只能是缺口陈述本身。
   - `conflicting_records` 的引用句**留在分母**（它们是事实断言），`records` 里声明的值
     成为合法数字来源（与 observation 同等待价），杜绝「引用双方值被判编造」。
   - 结构缺失（全部旧 attempts）时行为与 v1.4 **完全一致**（词表兜底路径未动）。

刻意不做：不为 conflict 的「结论句」加结构判据——`_with_unknown_mark` 保证「未知」
字面存在，v1.4 词表已覆盖；避免把引用句误踢出分母。

## 2. 顺带修复（非 v1.5 语义）

`test_a_page_just_read_still_fits_the_next_decision` 在 c44abf9 上就是红的：
65a54a4 的指令加长把 `fit_decision_prompt` 的压缩下限顶穿（fitted 8010 > 7745）。
按该测试自带的先例（指令变长→缩夹具而非抬上限）把重复段 34→23。

## 3. 影响面与重评政策

- TSR 完全不涉及（值路径与检查类型未动）。
- 旧 attempts 重评 v1.5 = 与 v1.4 同分（无 `delivery.uncertainty`）——不需要全量重跑来对齐历史。
- 新 live 跑（产品已填结构）才会体现差异：unknown 层「说法新颖但正确」的缺口句不再进分母，
  conflict 层引用双方值不再被合同层误杀。
- `dataset_sha` 不变。

## 4. 已知限制

1. `subject` 与答复句的匹配仍是字符串包含；模型把 subject 写成与答复完全不同的措辞时
   退回 v1.4 词表路径（不比 v1.4 差）。
2. 模型可能漏填 `uncertainty`（schema 有说明、无强制拒绝——拒绝会烧掉整轮）。
   TSR 的 future 版本可以考虑给 unknown/conflict 层加「结构已声明」检查，让漏填可测。
3. mixed 答复（同一答复里既有缺口又有真实事实句）由词表+无数字条件区分，
   极端措辞仍可能误分类——退路都是 v1.4 行为。
