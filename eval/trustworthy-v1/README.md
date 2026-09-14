# trustworthy-v1：可信评测主线（唯一权威文档）

PlanGo 的质量评测与产品优化主线。双指标：**TSR**（任务成功率，程序化判定 + Wilson 95% CI）
与 **Faithfulness**（事实支持率，规则层 + LLM-as-Judge 双层，幻觉数字在规则层即判不支持）。
本文是这条线的唯一权威入口；被清理的早期文档与 v1/v2 数据集在 git 历史中（`1b17cc0` 及更早）。

- 评分器：`trustworthy.v1.9`（`scripts/trustworthy/`），演进实录见 `SCORER_V1_3_NOTES.md` … `SCORER_V1_9_NOTES.md`
- 合同：`ATTEMPT_CONTRACT.md`（七种 check、uncertainty 投影、F 只评 completed）
- 出题与审阅纪律：`AUTHORING.md`（出题另开会话）、`PRODUCT_INVENTORY.md`（字段白名单）
- 最高原则：见仓库根 `AGENTS.md`——功能与任务完成优先，避免过拟合；不能为抬分改金标/加词表。

## 三套已审 holdout（各 204 题 = 6 层 × 34，均 accepted）

| 套 | 金标 | 最新 TSR / F | 分层快照与轮次记录 |
| --- | --- | --- | --- |
| holdout-v3 | accepted | 0.926–0.931 / 0.99 | `holdout-v3/RESULTS-*.md`；同码方差 ±1 题 |
| holdout-v4 | accepted_with_errata | 0.662 / 0.505（r3+v1.8）；去针反事实 0.917 | `holdout-v4/RESULTS-*.md`；「两/没」针为已声明测量偏差 |
| holdout-v5 | accepted（2026-09-13，无勘误） | **0.951 / 0.846**（r7c，评分器 v1.9） | `holdout-v5/RESULTS-r2-r4.md`、`RESULTS-r6-r7.md`、`RESULTS-r8-v1.9.md` |

全部 provisional_holdout（评委与被测同模型）；official 还差一次独立 runner 审计。
分数出处：`output/trustworthy-v1/holdout-v{3,4,5}-*.json`（attempts 内嵌 product_sha /
scorer_sha / prompt_sha 溯源；报告重评不重跑）。

## v5 优化主线（TSR 0.588 → 0.951、F 0.43 → 0.85 的修复链）

| 轮 | 产品 commit | TSR | F* | 修复 |
| --- | --- | --- | --- | --- |
| r1 | `01af6c0` 基线 | 0.588 | — | 结构声明仅靠 schema 说明 |
| r4 | `6eafe17` | 0.799 | 0.432 | 准入重试（`uncertainty_declaration_required`），conflict 34/34 |
| r6 | `7f63c82` | 0.912 | 0.528 | ①缺值答复复述页上相邻事实；②goal 回声剔除（口述落卡不再被 plan 卡死） |
| r7c | `89adacb` | **0.951** | **0.846** | ③合计口径合同；④已赋值澄清不扣卡；⑤距离组清除护栏（F 为 v1.9 重评） |
| r8 | `5db7127` | 0.931 | 0.861 | ⑥算式附注符号与变量解析（正确性优先；2 题 substance 阈值边缘交互，见 RESULTS） |

\* F 列在 r4/r6 为当轮 v1.8 口径、r7c/r8 为 v1.9 口径；跨评分器不可直接相减，
v1.8→v1.9 的差是评分器观测面修复（同一份 attempts 重评：0.517→0.846）。
r7 分层：conflict 34/34、sparse 34/34、unknown 33/34、calculate 32/34、persist 31/34、boundary 30/34。

## 常用命令

```bash
# 校验数据集
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py validate-dataset \
  --dataset eval/trustworthy-v1/holdout-v5

# live 跑（务必新文件名、新 work-dir）
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py run \
  --dataset eval/trustworthy-v1/holdout-v5 \
  --output output/trustworthy-v1/holdout-v5-attempts-rN.json \
  --live --work-dir output/trustworthy-v1/runner-work-holdout-v5X --timeout 180

# 评分（llm 主分）
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py score \
  --dataset eval/trustworthy-v1/holdout-v5 \
  --attempts output/trustworthy-v1/holdout-v5-attempts-rN.json \
  --output output/trustworthy-v1/holdout-v5-report-rN-llm.json --judge llm

# 只读分析（不花评委调用）
PYTHONPATH=. conda run --no-capture-output -n plango python scripts/trustworthy/analyze_deliveries.py \
  output/trustworthy-v1/holdout-v5-attempts-rN.json --dataset eval/trustworthy-v1/holdout-v5

# 门禁
conda run --no-capture-output -n plango python -m ruff check backend/plango backend/tests
conda run --no-capture-output -n plango python -m pytest backend/tests -q
```

## 纪律（历史教训的沉淀）

1. 改产品先 commit 再跑 live；新文件名、新 work-dir，绝不覆盖既有产物。
2. 低分不改金标；勘误须作废旧分、更新 `dataset_sha`、留痕、独立会话复确认。
3. 结构化判据优先于词表：v3 问法族拆分（词表内 1.000 vs 新问法 0.350）是词表过拟合的
   直接证据；准入重试优于合同侧文本推断。
4. 同码复跑方差 ±1–2 题；单层 ±3 题内的波动不读成能力变化。
5. 数据集「失败」先查三处再怪产品：runner 是否送达所有轮、oracle 期望值是否在合法输入
   中存在、检查是否在惩罚措辞而非能力。
6. 评分器改版必须：升 `SCORER_VERSION`、写 NOTES、新文件名重评、旧报告不动。
7. 金标独立审阅必须另开会话（各套 `GOLD_REVIEW_PROMPT.md` 为其提示词）。
