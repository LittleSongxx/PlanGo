# 评分器 v1.6 修订记录（2026-09-12）

v1.5 及更早的报告与 attempts 一律不动；v1.6 评分写新文件名（`*-llm-v1.6.json`）。

## 1. 改了什么：本跑计算器验算进入 F 的合法观测

背景（`holdout-v3/RESULTS-r6-r7.md` 与 `SCORER_V1_3_NOTES` 限制#2）：评分合同规定
「数字不在观测中即 unsupported」，而下一套（v4）的 calculate 题面将只印分项、不印合计
——正确走计算器得到的数字反而会被合同层当编造杀掉。

v1.6 在**评分时**（`cli.py score`）把 attempt 的
`end_state.execution_outcome.data.calculations` 里 `ok=true` 的行合成为观测文本
（`faithfulness.with_calculator_evidence`，形如 `本跑计算器验算：calc-1 = 112`），
追加进送评委的 observation。合同层数字闸门与评委据此放行计算器数字；
`ok=false` 或无 value 的行不进。pack 是拷贝，不改 attempt 原文。

## 2. 验证

- 单测：ok 行过滤、无计算行时 pack 原样返回、计算器数字送评委而非死在合同门、
  未经验算的数字仍死在合同门（`test_trustworthy_metrics.py` 4 条新增）。
- 旧产物不变性：v1.5 与 v1.6 的 rules 判分在 holdout-v3 r5 与 holdout-v2 首跑上
  **逐题 0 差异**（旧套页文本来就印着计算结果，合成观测不新增合法数字）。
  旧批次因此不需要为 v1.6 重评。
- TSR 路径完全不涉及。

## 3. 语义边界

1. 计算器行是**本跑自己的验算事实**，与页文同等待价；评委仍会否决与页文矛盾的
   断言（contradicted 路径不变）。
2. rules 对照判分对计算器数字的文本覆盖仍按原文子串规则（合成行本身可被引用），
   主分以 llm 评委为准——与既有分工一致。
3. `answer_number` 的 TSR 检查不变：仍要求最后一条 ok 计算的 value。
   v1.6 只解决 F 一侧的合法来源，不放松 TSR。

## 4. 与 v4 的关系

v1.6 是 v4（calculate 页文只印分项）的前置条件：没有它，v4 的正确计算答复
会被 F 误判。v4 出题合同草案见 `V4_AUTHORING_DRAFT.md`（正式出题另开会话）。
