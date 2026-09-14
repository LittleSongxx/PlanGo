# Scorer v1.9 — 观测面与原子子句（2026-09-14）

## 动机（来自 v5-r7b 的 F=0.51 归因）

r7b 的 99 个计分题里，`persist` 层 34 题 F 全部 0.000：读卡摘要
（"人数是 4，总预算是 300，开始时刻是 13:30，时长是 1020。"）整句被规则层判
`unsupported`。逐 claim 复核发现三个评分器缺陷，不是产品幻觉：

1. **预设值无观测来源**：`initial_trip_spec` 的种子值（4 人、300 元）不在页文、
   不在用户话——它是任务给产品的合同，观测面里却没有它。
2. **整句聚合连坐**：一个句号句是一条 claim，句中任一数字无来源就整句判死；
   "时长是 1020"（口述"17 小时"的规范化分钟值）拖死了同句其他全部正确字段。
3. **声明语境半句裸露**：把句拆细后，"两份记录不一致"这类声明句的语境半句
   失去了整句豁免，被当独立事实断言审判。

TSR 与 F 的裁决矛盾是最强证据：同一份读卡交付，`field_equals` 全部通过
（确定性校验值正确），F 却判"编造"。

## 改动（`scripts/trustworthy/faithfulness.py` + `cli.py`）

1. **`with_contract_values`**：任务 `initial_trip_spec` 文本化并入观测
   （"任务初始卡片（任务给定合同，视为给定）"）。**只读任务定义的种子卡**，
   不读本跑 `end_state` 的任何 spec——产品写错的值不能给自己作证。
2. **原子子句**：claim 先按句号拆，再按逗号拆；一个无来源字段不再连坐整句。
   （RAGAS / FActScore 均为原子 claim 粒度。）
3. **声明句豁免传递**：含声明 subject 的句子里，**无数字子句**继承
   `non-factual by structure`（声明的语境描述不是独立断言）；有数字子句照旧
   独立进分母。`with_user_turns` 标签同步更名为"用户全部输入"。

## 不做的事

- 不并读本跑 spec（防自我循环）；不给"小时↔分钟"开换算特例（1020 类规范化
  值仍按无来源处理，那条子句独立承担 unsupported，不连坐邻句）。
- 不动 `_contract_label` 的幻觉数字判死规则、不动 boundary 拒答复述的已知限制。

## 验证

- `backend/tests/test_trustworthy_metrics.py` 42 passed：豁免单元（句号级）保持、
  原子粒度断言更新（编造子句独立 unsupported、页上事实不再连坐）、
  `with_contract_values` 的"种子卡进观测、本跑写入不进"双向断言。
- fixtures 的 conflict pass 样本补上 uncertainty 结构（v1.3 时代的裸声明样本，
  与 v1.5+ 的结构声明合同对齐）；fail 样本不动。
- 对 r7 attempts 以 v1.9 重评（`holdout-v5-report-r7c-llm.json`）见 RESULTS。
