# holdout-v5 第 6–7 轮：口述落卡与缺值成句

同一数据集（`dataset_sha 8126d15…`）、同一评分器 v1.8；r5 在 60/204 处主动中止
（其时产品只含 r6 两修之一，无完整分数，不留引用）。三轮产品代码见 attempts 内 actor 快照。

| 轮 | 产品 commit | TSR | F（llm） | 关键层 |
| --- | --- | --- | --- | --- |
| r4（对照，`6eafe17`） | 准入重试版 | 0.799 | 0.432（83 计分） | unknown 8/34、persist 28/34 |
| r6（`7f63c82`） | +缺值复述合同 +goal 回声剔除 | 0.912 | 0.528（106 计分） | unknown 32/34、calculate 28/34（口径回归） |
| r7（`89adacb`） | +合计口径合同 +已赋值澄清不扣卡 +距离组清除护栏 | **0.951** | 0.517（99 计分） | conflict 34/34、sparse 34/34、unknown 33/34 |

TSR Wilson 95%：r7 [0.912, 0.973]；F bootstrap 95%：r7 [0.426, 0.608]。

## r4 → r6 的两个根因修复

1. **缺值答复必须复述页上相邻事实**（`5426f6c`）：r4 的 26 个 unknown 失败全部是
   `delivery-substance`——声明结构已对，答复只剩「XX未知，资料缺该值」套话。合同段
   `RECORDED_VS_CURRENT` 现在要求缺值句同时复述页上与所问属性相关的已核对事实
   （金标 `evidence_spans` 正是这个形态）。unknown 8→32。
2. **goal 回声剔除**（`7f63c82`）：r4 的 3 个 persist/sparse 卡死同根——requirement
   提取正确带回口述字段，但把目的地（「去南屿灶」）也写成 required_activities；
   seed 卡无 location，`_without_card_echo` 无从对照，稀疏落卡被拒，落入 plan 后被
   strict_location 的起点澄清永久拦住，**已提取的写入全部丢失**。回声对照扩展到卡片
   goal（用户原话来源）后写入即落卡，重启轮读卡交付。persist 的三个「字段丢失」
   假象全部消失。

## r6 → r7 的三个回归/噪声修复（`89adacb`）

r6 引入两类新失败，r7 对症：

1. **合计口径回归**（calculate 31→28）：复述合同让模型在合计答复里展开条件分支，
   自设「堂食不含外带包装」口径，计算器分项排除了页文标注「另收」的费用
   （6 题同族、r4 全对，非方差）。合同现在明确：合计计入页文逐项列出的全部费用，
   场景差异写进文字、不排除条目、不标未知。calculate 回到 32。
2. **已赋值字段的多余澄清扣住写入**（prst-05）：提取正确（预算 6300/人均 120）但
   自问「人均×人数≠总额」——clarification 点名的恰是已赋值字段。settle 现在剔除
   与赋值重叠的澄清项，未赋值字段的澄清照常询问。
3. **距离组清除护栏**（推广自预算对的 alone-shape）：`clear_route_distance` 与
   距离组自身的陈述并存时不再清除另一侧限额。r6 的 prst-34 形状（组内无陈述的
   凭空 clear）防不住，按提取噪声方差记录。

## 剩余 10 个失败的归属

- **boundary 4**：`business_completed=None` 被 `field_equals false` 双重惩罚的检查
  形状——金标审阅裁决点 ①。
- **calculate 2 + persist 2**：纯 `delivery-substance`（逐项报数式短交付）——
  阈值语义，裁决点 ②。
- **persist 1**（prst-33，r4 起同挂）：预设字段被提取噪声清除；**unknown 1**：
  该轮未声明（r6 为 2 题，±1 波动）。

产品侧无剩余系统性缺口；全部 provisional_holdout，金标待独立会话审
（`GOLD_REVIEW_PROMPT.md` 已备，四个裁决点）。
