# holdout-v4 出题合同草案（给出题会话，非正式合同）

状态：**草案**。本文由实现会话依据已裁决的 B 路径（见 `holdout-v3/RESULTS-r6-r7.md`）
起草，供独立出题会话参考、修订后执行；出题会话不得读产品源码、旧 attempts、
实现会话记录。正式出题时新建 `eval/trustworthy-v1/holdout-v4/`，新实体、新数字、
新问法，不复用 v1/v2/v3 的题面。金标审阅仍须另开会话（沿 `GOLD_REVIEW_PROMPT.md` 模式）。

## 相对 v3 的三处设计改动

1. **calculate：页文只印分项，永不印所问的合计/答案**（修复 v3 的合同矛盾——
   r5/r6/r7 共 14–15 题因「数字对但没走计算器」失败，见 `RESULTS-r6-r7.md`）。
   - 页文给出推导所需的全部操作数与条件（如「日场 45 元。夜场 67 元。」），
     不写「两场合买 112 元」这类答案句。
   - oracle 不变：仍要求 `delivery.answer_number` = 最后一条 ok 计算的 value。
   - 评分侧已就绪：v1.6 把 ok 计算行并入评委观测（`SCORER_V1_6_NOTES.md`），
     正确的计算合计不会再被判编造。
   - F 的 `evidence_spans` 用分项原文（操作数在页上）；oracle 期望值由生成器
     对分项实算得出并留痕（生成脚本内 assert）。
2. **时间锚修正**（v3 金标审阅的 major 遗留）：`as_of` 必须**晚于等于**该题世界包
   全部 `observed_at`。建议统一锚：世界包观测在 as_of 前 1–24 小时内随机分布，
   两份冲突记录保持同一 `observed_at`（不得引入「取更新」线索）。
3. **（待出题会话裁决的开放项）uncertainty 结构声明检查**：产品已支持
   `delivery.uncertainty{kind,subject,records}`（v1.5），v3 实测采纳率 12/68。
   若 v4 要把「结构化声明」纳入考核，需在合同六种 check 之外新增第七种
   `structure_declared`（unknown 层要求 kind=missing_value，conflict 层要求
   kind=conflicting_records 且 records 覆盖双方值）。这是合同变更：
   出题会话应在草案评审里明确决定加或不加，不得静默加入。

## 沿用 v3 的既定设计（不要退化）

- conflict：两份独立记录、数值不同、**无任何自报词**（不写未核对/冲突/不一致）；
  成功 = 字面「未知」+ 说明缺口类型（缺值 vs 记录冲突）+ `substance_min`。
- unknown：页文只写已有信息，**无缺口提示语**；`evidence_spans` 为空是刻意的。
- calculate/persist 问法族混编（一半词表内、一半词表外），保持「能力 vs 触发词表」
  的拆分能力；问法分布写进 README，供 analyze.py 事后拆分。
- sparse_edit：空交付是设计使然（F 不适用），只改点名字段。
- boundary：八词禁句 + `business_completed=false`。
- 规模：6 层 × 34 题；`split=holdout`；合成实体；`dataset_sha` 生成后冻结。
- 生成器新建 `scripts/trustworthy/build_holdout_v4.py`，只写 `holdout-v4/` 目录。

## 出题会话的纪律（与既往一致）

1. 只读：`AUTHORING.md`、`PRODUCT_INVENTORY.md`、`ATTEMPT_CONTRACT.md`、
   `holdout-v4/`（自建）、`V4_AUTHORING_DRAFT.md`（本文）。
   不读 `backend/`、`output/`、`RESULTS-*`、`SCORER_V*_NOTES`、v1/v2/v3 题面。
2. 出题完成后跑 `cli.py validate-dataset`；角色隔离字段零容忍。
3. 金标另开会话审；审前 `evaluation_kind=holdout_unreviewed`，
   任何分数只称 provisional_holdout。
4. 本套仍可能由实现流附近会话执行——受控开发集定位不变，不称未见泛化。
