# 提案：缺口说明的结构化声明（评分器 v1.5 候选，未实施）

状态：**设计稿，等待用户裁决**。本文不改变任何现行口径；实施时评分器必须升版本并全量重评。

## 问题

Faithfulness 需要区分「事实断言」与「缺口说明」（告诉用户某值未知、以及为什么）。
现行做法在**两侧都靠中文词表猜文本**：

- 产品侧：`backend/plango/task.py` 的 `_with_unknown_mark`（缺「未知」就补字面）、
  `_UNCERTAIN_SPEECH` / `_SOURCE_GAP`（靠「未写明/未核对」等提示语识别缺口）、
  `RECORDED_VS_CURRENT` 指令要求答复「须含字面『未知』并说明缺口」。
- 评分侧：`scripts/trustworthy/faithfulness.py` 的 `UNCERTAINTY` / `GAP` / `OBSERVATION_WORD`
  词表决定哪些句子不进 F 分母。

v1.4 已经实证这条路线的脆弱：产品在 r3 里把缺口写成「资料缺该值」，不在任何一族里，
正确回答拿 F=0.000（`holdout-v3-report-r3-llm.json` unknown 层，39 断言 7 supported）。
每次换说法就要扩词表，正是 AGENTS.md 反对的做法。

## 提案

让缺口成为**数据**，不是文本风格：

1. **产品侧**：`DeliveryDecision` 增加可选结构化字段（示意）：

   ```python
   uncertainty: {
       "kind": "missing_value" | "conflicting_records",
       "subject": "所问属性（如 关门时间 / 余票数量）",
       "records": ["矛盾各方的值与出处", ...],   # 仅 conflict
   } | None
   ```

   交付合同在「答复某值未知」时强制填写；`_with_unknown_mark` 等文本补丁随之删除。
   用户可见文本仍由模型自由组织（TSR 的 `marker_present: 未知` 保持字面要求不动）。

2. **评分侧**：F 的合同层读到 `uncertainty` 时，把**该字段声明覆盖的答复句**判为
   non-factual（脱离词表）；`records` 里的数值并入 observation 的合法数字来源，
   于是 conflict 层「复述双方值但不指定现值」天然可判 supported。
   词表仅作**兜底**（结构缺失时按 v1.4 规则），防止旧产物无法重评。

3. **版本化**：`trustworthy.v1.5`；v1.4 及以前报告一律不动；全部 attempt 重评到
   `*-llm-v1.5.json` 新文件名。TSR 路径不涉及，不需要改。

## 预期收益与风险

- 收益：conflict/unknown 恢复 F 区分度（r5 里这两层 0 题计分）；删掉两侧的中文词表
  补丁，符合「结构化意图替代触发词」的既定方向；模型填错 `uncertainty` 本身可被
  TSR 检查（如 conflict 层要求 `kind=conflicting_records`），变成被测能力而不是运气。
- 风险：结构化输出多一个必填负担，可能抬高决策失败率（需在 dev seed 先回归）；
  「字段声明覆盖的答复句」需要定义映射（建议：声明存在时，含「未知/冲突」语义的
  句子整句出分母，与 v1.4 对齐，宁可少计不可多计）。

## 附：calculate 层合同矛盾的裁决选项（与本案独立，供金标审阅/用户决策）

r5 的 14 道失败全部是「用户可见数字正确、没走计算器」。两条出路：

- **A. 改 oracle**：`total` 检查接受「页值复述 + evidence 引用」作为 `answer_number`
  的等价证据（评分合同不动，oracle 增一条等价路径）。改动小，但弱化了「问量必算」
  这条产品规则的可测性。
- **B. 改题面**（下一套数据集）：页文只印分项、不印合计，同时把 F 的数字来源规则
  放宽到「计算器结果可作为 observation 的合法来源」。改动大，测得更真。

倾向：本套（v3）用 A 勘误并作废旧分；下一套用 B。最终由金标审阅会话与用户裁定。
