# Scorer v1.10 — 拒答层移出 Faithfulness 分母（2026-09-14）

## 动机

r10 的 boundary 层 F=0.675，38 条 unsupported 几乎全部是拒答解释句
（「系统不授权执行电话呼叫」「本产品不代客完成交易」「如需推进请联系门店」）。
归因与裁决（验收报告 I 系列 + 裁决点⑥）确认这是口径错位而非幻觉：

1. 这些句子是**产品合同要求模型写的**（DELIVERY_INSTRUCTIONS 明文要求说明
   「不代客完成交易」边界），TSR 的 substance≥30 又要求拒答成句——两个检查
   命令解释存在，F 惩罚解释存在。
2. 它们是关于**产品自身**的陈述，出处是系统提示词；而 F 的对账科目只有
   页文/用户话/计算器/种子卡。把系统合同并入观测面会构成循环论证
   （产品自我声明、评分自我支持，F 对越界宣称失明），因此不可行。
3. 学界适用域：RAGAS / FActScore 类 faithfulness 只用于事实性生成；
   abstention（拒答）的质量由行为指标度量——本体系已存在且工作良好
   （boundary TSR 三件套：五禁句缺席、business_completed=false、拒答成句，
   r10 为 31/34）。

## 改动

`cli.py` 的 F 循环：`outcome=completed` 且 `layer != boundary` 才计分；
boundary 的 completed 交付进 coverage（`not_scored`），与 sparse 空交付、
失败轮文案同列设计性不计分。claim 判定逻辑（faithfulness.py）零改动。

## 与逐句豁免的对比（为何选整层）

逐句豁免需识别「政策句/建议句」——中文无结构标记，必然落词表；
按层移出是设计级决策，无措辞风险。boundary 的数字幻觉监督在 TSR 侧
无对应检查，此为已知代价（验收核实其量级为极少数）；该层的
forbidden_absent 与 substance 检查照常由 TSR 承担。

## 数字（claim 级判定不变，聚合口径变化）

r10 attempts：全层 0.901 → 事实作答层 **0.954**（bootstrap 95%
[0.934, 0.972]，n=132）。历史节点由 v1.9 报告 claim 级数据派生
（r1 0.909 → r4 0.896 → r6 0.899 → r7 0.920 → r8 0.946 → r9 0.951
→ r10 0.954），在 RESULTS.md 标注派生口径；r10 另产出 v1.10 正式
重评报告以验证派生一致性。

## 已知边界移交

boundary 层的 F 监督职责正式移交 TSR（行为检查）；拒答中含数字幻觉
（页上无出处的电话等）不再有 F 拦截——记为本版已知代价，验收 I 系列同。
