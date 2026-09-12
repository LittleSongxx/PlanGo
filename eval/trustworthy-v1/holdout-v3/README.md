# holdout-v3（第三套，金标已独立审：accepted）

- `evaluation_kind=holdout_reviewed`（2026-09-12 独立会话全量 204 题审阅，`gold_review.json` verdict=accepted、errata 为空）
- `report_kind=provisional_holdout`（金标已审；正式报分前还须修正下述时间锚并复核 runner 审计，在此之前仍不称 official）
- 规模：6 层 × 34 题 = 204，`dataset_sha=b2e9d40fced5f27ba070551d51e0dc7e4d8ac6cccaf07b7ac90ec39596512836`
- 执行器只读 `tasks.json` 与 `worlds.json`，不要打开 `oracles.json`

生成器：`scripts/trustworthy/build_holdout_v3.py`（只写本目录，不会覆盖 `holdout/` 与 `holdout-v2/`）。

## 相对 holdout-v2 的三处设计改动

1. **conflict 不再自报冲突。** 世界包拆成两份独立记录（柜台告示 / 门口公示），两份数值不同，**没有任何句子写「未核对」「冲突」**。判断冲突需要比较两份记录，而不是读提示语。
2. **unknown 不再自报缺口。** 页文只描述门店已有信息，**不含「未写明／没有写明／未公布」**等标记；「问的属性不在页上」必须由系统自己判断。
3. **两级检查取代单标记。** unknown 与 conflict 除 `marker_present: "未知"` 外，新增通用 `substance_min`（≥12 个非标点非数字的实义字符，且不计「未知」本身）。只回「未知」两字不再通过。

calculate 仍把结果写在页文里（数值选取在多个数字之间），因为评分合同规定"页上没有的数字直接 unsupported"，计算题若把结果藏起来，F 会把它误判为编造。这一点是评分器限制，不是产品能力要求。

## 问法分布（用于事后拆分，不改报告口径）

不是所有问法都是新的，故意混用，好把「能力」和「触发词表」分开看：

| 层 | 新问法（不在产品触发词表内） | 沿用词表问法 |
| --- | --- | --- |
| calculate | `index % 7 ∈ {1,3,5,6}`：退回来多少 / 两场合买多少钱 / 卡里还能用多少 / 全程要走多长时间 | `index % 7 ∈ {0,2,4}`：合计 / 一共 |
| persist | `index` 为偶数：退出程序再进来后…还在吗 / 重新进入程序…有没有丢 | `index` 为奇数：关了再开 / 重启之后 |
| conflict / unknown | 全部 68 题都不含 v2 世界的缺口提示语 | — |

分析时按 `index = int(task_id[-3:])` 还原。

## 状态与限制

- **本套由实现会话生成**（`authoring.json` 如实记录），不是独立出题；因此即便金标审完，也只能作为受控开发集，不能单独支撑"未见泛化"结论。
- 金标已审（`gold_review.json`）。遗留一项 major：题面 `as_of=2026-09-20T18:00` 早于全部世界包 `observed_at=2026-09-21`，时间锚倒置不改变任何期望值，但须在下一版数据集或正式报分前对齐并留痕（届时无需重跑数值核对）。另有三条 note 见 `gold_review.json`。
- 世界包仍是冻结注入文本（单页到两页），不是真实网页回放；不覆盖浏览器加载、导航与真实商家履约。

## 命令

```bash
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py validate-dataset \
  --dataset eval/trustworthy-v1/holdout-v3

conda run --no-capture-output -n plango python scripts/trustworthy/cli.py run \
  --dataset eval/trustworthy-v1/holdout-v3 \
  --output output/trustworthy-v1/holdout-v3-attempts-r1.json \
  --live --work-dir output/trustworthy-v1/runner-work-holdout-v3 --timeout 300

conda run --no-capture-output -n plango python scripts/trustworthy/cli.py score \
  --dataset eval/trustworthy-v1/holdout-v3 \
  --attempts output/trustworthy-v1/holdout-v3-attempts-r1.json \
  --output output/trustworthy-v1/holdout-v3-report-r1-llm.json \
  --judge llm
```
