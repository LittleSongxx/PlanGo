# 评分器 v1.8 修订记录（2026-09-13）

v1.7 及更早报告与 attempts 不动；v1.8 评分写新文件名（`*-llm-v1.8.json`）。

## 1. 两处修订

### 1.1 Faithfulness 只评 `outcome=completed` 的交付

v4 把一个老问题放大：失败轮的兜底文案（「Discovery 没有返回可用地点」「本轮未能形成
可用的下一步」）进了 F 分母、被评委判 unsupported，把 persist/boundary 的 F 拉成 0。
F 的定义是「交付断言被观测支持的比例」，失败轮没有交付——该失败由 TSR 的 outcome 检查
承载。v1.8 起非 completed 的 attempt 离开 F 分母（记 n/a），计入 coverage 块；
下界口径不变（未计分按 0）。

### 1.2 新增第七种 check：`structure_declared`

读 `delivery.uncertainty`（v1.5 起产品可填的结构化缺口声明）：
- `kind`（可选约束）：须为 `missing_value` 或 `conflicting_records`；
- `min_records`（可选约束）：`records` 非空条数下限（conflict 层要求双方值）。

这是「两/没」单字针的根修路径：缺口类型由结构考核，不由措辞考核。
配套 `ATTEMPT_CONTRACT.md` 已修订（check 清单七种、值路径加 `delivery.uncertainty.*`、
密封包投影补 `uncertainty`、F 的 completed 规则），供 v5 出题使用。

## 2. 验证

- 单测：structure_declared 的 kind/min_records/缺失三种行为；非 completed 交付离开分母。
- TSR 对既有数据集完全兼容（无数据集使用该 check 前，评分行为不变）。
- **注意**：1.1 是判据变更——旧 attempts 重评 v1.8 时，失败轮任务将从计分集移出
  （v4-r2 的 persist/boundary F 将显著变化），历史 v1.7 报告保持原样不回改。
