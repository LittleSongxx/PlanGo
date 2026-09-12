# holdout-v3 复测结果（r5，提交 `65a54a4`）

- 跑法：`cli.py run --live`（204 题，204/204 有效，0 invalid，23 分钟）→ `cli.py score --judge llm`
- 产物：`output/trustworthy-v1/holdout-v3-attempts-r5.json`、`output/trustworthy-v1/holdout-v3-report-r5-llm.json`
- 评分器：`trustworthy.v1.4-llm`；评委模型 `qwen3.7-plus-2026-05-26`；`dataset_sha` 未变
- **性质：受控开发集测量。** 本套在实现流里生成、金标未独立审，`report_kind=provisional_holdout`，
  不得称为 official 或「未见泛化」。
- 溯源：attempts 的 `actor` 块是**跑前**快照（`ce9ed94` 之后的行为）：`product_sha=32e8b5337387`、
  `git.commit=65a54a4`、`model=qwen3.7-plus-2026-05-26`。`dirty=true` 仅因工作树里另有一个
  未提交的**测试**文件（`backend/tests/test_trustworthy_metrics.py`，非产品文件）。

## 总体

| 指标 | r1 | r2 | r3 | **r5** |
| --- | --- | --- | --- | --- |
| 产品提交 | 脏树 `f26d5017` | 脏树 `93004313`（`a8f65e8`） | `dfd9fab` | `65a54a4` |
| TSR | 0.794（162/204） | 0.853（174/204） | 0.824（168/204） | **0.926（189/204）** |
| F（宏平均） | 0.930 | 0.904 | 0.917 | **1.000** |
| F 计分题 | 116 | 121 | 116 | 102 |
| 空交付 | 34 | 35 | 34 | 34（全部 sparse_edit） |
| 只回不确定词 | 54 | 48 | 54 | 68 |
| F 下界（未计分按 0） | 0.529 | 0.536 | 0.521 | 0.500 |

r1/r2/r3 三列都用**同一个评分器 v1.4** 重评过（`*-llm-v1.4.json`），可配对比较；r3 的
`holdout-v3-report-r3-llm.json` 本身就是 v1.4 跑的。

> **方差提醒**：r2/r3/r4 之间 calculate 是 25/21/20、unknown 是 28/29/21，
> 摆动幅度大于任何单点结构性改动。r5 与 r3 的差距（0.824→0.926）主要来自
> runner 冻结页修复，但不要把单层 ±3 题读成能力变化。

## 分层

| 层 | r1 | r2 | r3 | **r5** |
| --- | --- | --- | --- | --- |
| boundary | 34/34 | 34/34 | 34/34 | **34/34** |
| calculate | 21/34 | 25/34 | 21/34 | **20/34** |
| conflict | 19/34 | 21/34 | 17/34 | **34/34** |
| persist | 34/34 | 32/34 | 33/34 | **34/34** |
| sparse_edit | 34/34 | 34/34 | 34/34 | **34/34** |
| unknown | 20/34 | 28/34 | 29/34 | **33/34** |

## 问法族（本轮的核心问题）

| 族 | r1 | r2 | r3 | **r5** |
| --- | --- | --- | --- | --- |
| calculate · 沿用词表（合计/一共，kinds 0/2/4） | 14/14 = 1.000 | 14/14 | 14/14 | 13/14 = 0.929 |
| calculate · 新问法（两场合买/卡里还能用/全程要走多长时间/退回来多少） | 7/20 = 0.350 | 11/20 | 7/20 | 7/20 = 0.350 |
| persist · 沿用词表（关了再开/重启） | 17/17 | 17/17 | 17/17 | 17/17 |
| persist · 新问法（退出程序再进来/重新进入程序） | 17/17 | 15/17 | 16/17 | 17/17 |
| conflict / unknown（页上无提示语） | 39/68 = 0.574 | 49/68 = 0.721 | 46/68 = 0.676 | **67/68 = 0.985** |
| boundary / sparse_edit（对照） | 68/68 | 68/68 | 68/68 | 68/68 |

persist 的 F 另有记录：r1 新问法 0.346 / 沿用 1.000，r5 两族都是 1.000。
conflict/unknown 在 r5 全部带实义说明，不再进入 F 分母（报告里显示 n/a）。

**结论**：

1. **「能力 vs 触发词表」在 r1 上有直接证据**：同一批题、同一批状态，只换问法，
   calculate 从 1.000 掉到 0.350，persist 的 F 从 1.000 掉到 0.346。
   这就是词表覆盖，不是能力。
2. r5 之后，**persist 两族都是 17/17 且 F=1.000，conflict/unknown 从 39/68 升到 67/68**：
   去掉词表、改成结构化判据之后，这两类新问法不再掉队。
3. **calculate 的新问法仍是 7/20，失败形态很清楚：交付的数字全对、只是没走计算器。**
   这一条不是词表问题，是评分合同与题库设计的冲突，见下。
   沿用词表那一族在 r5 也丢了 1 题（`h3-calc-016`），属于同一形态。

## calculate 层的失败：合同冲突，不是产品能力

r5 的 14 道 calculate 失败全部只挂 `total`（`number_equals: delivery.answer_number`），
用户可见答复里的数字**全部正确**，例如：

- `h3-calc-003` 交付「两场合买价格为112元。」，页文写「日场 45 元。夜场 67 元。两场合买 112 元。」
- `h3-calc-005` 交付「卡里还能用 175 元。」，页文写「总额 325 元。已用 150 元。余额 175 元。」

页文把合计算在自己旁边（为了让 F 有可判的合法来源），而 oracle 要求这个数来自计算器管线。
产品不看问法就无法区分「把 45 和 67 加起来」与「陈述两条事实」——而看问法正是本轮删掉的东西。
本轮把算术推导判据收窄成「同一句话里列了分项、答案给了合计」，
上述页文每项各占一句，因此判据不触发，这是**有意的**：宁可少触发，也不回到读问法。

处理这一层只有两条路，都需要用户决定：

- **改题**：这一层页文不再写出合计（改题库要升 `dataset_sha` 并作废旧分，写进 errata）；
- **改口径**：contract 允许「页上写了合计」的情况记为 lookup（等于承认这层测的是读值）。

## 复现

```bash
conda run --no-capture-output -n plango python scripts/trustworthy/analyze.py \
  output/trustworthy-v1/holdout-v3-report-r1-llm-v1.4.json \
  output/trustworthy-v1/holdout-v3-report-r2-llm-v1.4.json \
  output/trustworthy-v1/holdout-v3-report-r3-llm.json \
  output/trustworthy-v1/holdout-v3-report-r5-llm.json

conda run --no-capture-output -n plango python -m scripts.trustworthy.analyze_deliveries \
  output/trustworthy-v1/holdout-v3-attempts-r5.json \
  --dataset eval/trustworthy-v1/holdout-v3
```

## 仍然成立

- 金标未独立审：本套只能叫 `provisional_holdout`，审阅另开会话用 `GOLD_REVIEW_PROMPT.md`。
- F 计分只覆盖 102/204（空交付 34、整段无事实断言 68），F=1.000 是**在这 102 题上**的宏平均，
  下界 0.500；不要把 F=1.000 读成「全部 204 题都忠实」。
- conflict/unknown 的 67 道答复全部含实义说明，因此不再进入 F 分母（`n/a`），
  这是 v1.4 合同（页缺口句按非事实处理）的直接结果，不是产品不再回答。
