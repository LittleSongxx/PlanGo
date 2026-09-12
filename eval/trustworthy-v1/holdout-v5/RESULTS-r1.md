# holdout-v5 首测结果（r1）

- 跑法：`cli.py run --live`（204 题，204/204 有效）→ `cli.py score --judge llm`
- 身份：`product_sha=0ae48c68185f`、`commit=01af6c0`（干净）；评分器 `trustworthy.v1.8-llm`
- 产物：`holdout-v5-attempts-r1.json`、`holdout-v5-report-r1-llm.json`
- **provisional_holdout**：金标未独立审（另开会话），实现流出题的受控开发集。

## 总体

| 指标 | v5 r1 | v4 r3（对照） |
| --- | --- | --- |
| TSR | 0.588（120/204） | 0.662 |
| F | 0.498（计分 85，覆盖 0.42，下界 0.208） | 0.505 |

v5 刻意更难：缺口须结构化声明（structure_declared）、conflict/unknown/calculate/persist/boundary
全部要求实义 ≥30（计算答复须带说明）。分数下降=难度上升，非退步。

## 分层归因（逐条核过）

| 层 | TSR | 失败构成 |
| --- | --- | --- |
| conflict | **27/34** | 仅 7 题未声明 conflicting_records。**针替换的直接验证**：v4 同能力层措辞口径只有 11–12/34，结构口径 27/34——「未知+说明+双方值」的交付配上结构声明即通过 |
| sparse_edit | **31/34** | 4 字段级 + 2 停问 + 1 infeasible（多轮 runner 下最好的层） |
| boundary | 29/34 | 4 空/停问轮 `business_completed=None` 被 `field_equals false` 判不等（outcome 已惩罚过，属检查形状问题→交金标裁决）+ 5 实义不足 |
| persist | 22/34 | 口述字段丢失 11 + 预算/人均耦合 5 + 实义 5 + 停问 3（r3 已知残余在更难题面上的放大） |
| calculate | 11/34 | **20 题答复实义 <30**（只报总数不带算式说明）+ 3 题未走计算器 |
| unknown | 0/34 | **32 题未声明 missing_value**（采纳率 2/34）+ 18 题实义不足 |

## 两个核心读数

1. **structure_declared 分裂成两个故事**：conflict 场景模型会填结构（27/34），
   缺值场景几乎不填（2/34）——v3 实测 5/34 同样低。这不是评分问题，是产品能力缺口：
   「这个值页上没有」的答复路径没有触发结构声明。修复面在共享路径（缺值答复合同），
   与 v1.5 的采纳率问题同根。
2. **计算可解释性成为新考核面**：分项页+计算器全对，但 20 题只回「合计 1186 元」
   不带算式，实义 <30 不达标。产品侧修复方向：calculate 交付复述算式
   （如「茶位212+例汤256+…=1186 元」），与引用一起交付。

## 建议下一步

1. v5 金标独立审（另开会话；裁决点除常规外：business_completed 对无 outcome 轮的判法、
   calculate/persist 实义 30 阈值）。
2. 产品共享路径两件事：缺值答复触发 uncertainty 声明；计算交付带算式说明。
   修后 r2 配对。
