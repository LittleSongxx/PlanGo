# trustworthy-v1 评测线最终验收（FINAL_ACCEPTANCE）

- 验收日期：2026-09-14
- 验收对象：数据集—评分器—产品—报告四层证据链的一致性与反作弊性
- 验收方式：只读核证。未改产品、未动金标、未跑 live、未重评（零模型调用）。
- 验收基线：`main` @ `ceadd43`（工作树 clean）。任务指定的 `eval/trustworthy-v1.3`
  为 `main` 的严格祖先（merge-base `4512c76` 即该分支顶端），七个收尾提交
  （评分器 v1.9、r9/r10、文档统一）均在 `main` 上；各轮 attempts 内嵌分支记录
  r1–r7 为 `eval/trustworthy-v1.3`、r8–r10 为 `main`，与分支演进一致。本验收覆盖
  两分支的全部历史。
- 数字口径：本文只复述 README/RESULTS 已公布并经核对的数字，不产生新数字。

## 八项逐条结论

### 1. 数字一致性 — pass

用 python 读取 `output/trustworthy-v1/` 下 9 份报告 json 原始值，与 README /
RESULTS 的全部对外数字机器核对，共 41 项、零误差：

- v5 主线七轮（r1/r4/r6/r7/r8/r9/r10）的 TSR 与 F，与
  `holdout-v5-report-{r1,r4,r6}-v19-llm.json`、`-r7c-`、`-r8-`、`-r9-`、`-r10-llm.json`
  一一吻合（r7 取 r7c 重评值）；
- v3 `0.926 / 0.981`（`holdout-v3-attempts-r7-report-v19-llm.json`）、
  v4 `0.662 / 0.575`（`holdout-v4-attempts-r3-report-v19-llm.json`）；
- r10 分层 TSR（calculate/conflict/sparse 三层 34/34、unknown 33/34、persist 31/34、
  boundary 31/34）与 F 分层（0.985/0.985/0.951/0.893/0.675）；
- r10 TSR Wilson 95% [0.931, 0.983]、F bootstrap 95% [0.871, 0.925]；
  剩余失败 7 题（197/204）。

### 2. 溯源完整性 — pass

`holdout-v5-report-r10-llm.json` 四个溯源字段逐一复算：

| 字段 | 复算方式 | 结果 |
| --- | --- | --- |
| `dataset_sha` | `load_dataset` 重算四文件 canonical 指纹 | 一致（`bb61c4a…`） |
| `attempts_sha` | attempts 文件 sha256 | 一致 |
| `scorer_sha` | 当前源码 `scorer_sources_sha` + 报告内 judge meta 重算指纹 | 一致（`b99b8088…`；r9 同） |
| `actor.product_sha` | 从 git `1202491` 树按 `PRODUCT_GLOBS`（pathlib glob 语义）逐 blob 复算，82 文件 | 一致（`33ea0e19…`） |

附：报告 `actor.git.commit` = `1202491`、dirty=False；报告 actor 块与 attempts
内嵌 actor 逐字段一致（score 透传，未被篡改）。r7/r8 报告的 scorer_sha
（`55004a2e…`）与当前源码不同，属 f06f0c9 词表补丁前的 v1.9 指纹，符合
「词表补丁以 scorer_sha 区分」的自述（见 issue I6）。

### 3. 金标不可动性 — pass

- `tasks.json / worlds.json / oracles.json` 自生成提交 `01af6c0` 至 HEAD **字节级
  零改动**（git blob 逐一比对）；`--follow` 历史各自仅一个提交。
- `dataset_sha` `8126d15…` → `bb61c4a…` 的唯一变化来源是 `3cc2d05` 对
  `protocol.json` 的两行状态字段（`evaluation_kind: holdout_unreviewed →
  holdout_reviewed`、新增 `gold_review: "accepted"`）；旧值四文件指纹复算确为
  `8126d15…` 前缀。
- `gold_review.json`：verdict=`accepted`、errata 为空；独立会话审阅，自述未读
  产品源码与 output/。
- v3/v4 交叉核对：v3 verdict=accepted / 0 errata，v4 verdict=accepted_with_errata /
  2 errata，与主 README 表述一致。

### 4. 归因链可追溯性 — pass（附 note，见 I7）

11 项修复逐一核对 commit 与 diff 语义：

| 归因 | commit | diff 与描述一致性 |
| --- | --- | --- |
| 准入重试 | `6eafe17` | graph.py 准入错误集合 + task.py 合同抛 `uncertainty_declaration_required`；且**撤销**了 `ce94607` 的词表式回填推断（反过拟合方向） |
| ① 缺值答复复述相邻事实 | `5426f6c`（实际落点，RESULTS 记在 r6 行 `7f63c82` 名下） | RECORDED_VS_CURRENT 通用指令，无特例 |
| ② goal 回声剔除 | `7f63c82` | graph.py `item not in goal` 通用规则 |
| ③④⑤ 合计口径/已赋值澄清/距离组护栏 | `89adacb` | 提示词合计规则 + `_without_answered_clarifications` + 距离组 alone-shape 守卫（budget 组规则推广） |
| ⑥ 算式符号与变量解析 | `5db7127` | `operands_resolved` + 运算符→符号映射 |
| ⑦⑧⑨ 冲突引证/带单位/逐字提取 | `f06f0c9` 产品部分 | `conflict_records_required` 准入重试 + 读卡摘要单位 + requirement 提示词逐字规则 |
| ⑩ 词表补「无法确认」 | `f06f0c9` 评分器部分 | UNCERTAINTY 一行 |
| ⑪ 合计主数=全项之和 | `1202491` | 提示词两处改写 |

各轮 attempts 内嵌 `actor.git.commit` 与归因表全部一致（r1/r4/r6/r7/r9/r10 精确
匹配；r8 attempts 的 HEAD 为 `2bce94c`——纯评分器提交，其产品路径
backend/plango、vendor、skills 与 `5db7127` `git diff` 为空，产品指纹口径下归因
成立）。

### 5. 反过拟合抽查 — pass（历史遗留记 I1/I2）

**产品 commit 侧**：六个归因 commit 的产品路径（backend/plango、vendor、skills）
diff 中无评测词表、店名、问法特例、needle 字面的新增。程序化扫描的命中全部落在：

- 单元测试 fixture（`6eafe17`/`f06f0c9` 的测试样本句、`7f63c82` 的
  test_observe_before_navigate.py 以「去南屿灶」复现 r4 badcase 形状作回归锁——
  I9）；
- 提示词整行重写携带的既有文本（89adacb/1202491 的 + 行含既有「未知」字样，
  实际语义新增仅合计规则）；
- 评分器自身文件（f06f0c9 的 faithfulness.py 词表，本就是评分器）。

产品逻辑改动全部为通用规则（组规则推广、准入错误类型、单位、逐字提取、合计
口径、算式符号），无单 badcase 特例堆叠。

**评分器 v1.9 侧**（`2bce94c`、`f06f0c9` 的 faithfulness.py 部分）逐处裁决为
**修误判，非放水**——完整六项裁决见下节。

### 6. 门禁 — pass

- `ruff check backend/plango backend/tests`：All checks passed。
- `pytest backend/tests -q`：589 passed + 43 subtests passed（1 个第三方
  starlette DeprecationWarning，与本线无关）。

### 7. 产物纯净 — pass（子文档 3 处过时引用记 I3/I4）

- `output/trustworthy-v1/`：仅含 v1.9 口径报告（9 份：三份 `-v19-` 历史重评、
  r7c、r8、r9、r10 及 v3/v4 各一份）与 attempts 文件；无 v1.8 命名报告残留、
  无 runner-work/、无 logs。
- `eval/trustworthy-v1/`：无 handoff/提案/草案类过程文档（HANDOFF_NEXT_SESSION、
  PROPOSAL 等已在 `dd0b8a2`/`b2f7ac4` 清理；fixtures/attempts.json 为评分器
  测试 fixture，合法保留）。
- 悬空引用：主 README 与 holdout-v5/RESULTS.md 引用的全部文件（NOTES 系列、
  合同/出题文档、9 份报告、RESULTS-*.md 家族）均存在。子文档 3 处过时引用
  见 I3/I4。

### 8. provisional 限定 — pass

9 份报告 json 的 `report_kind` 全部为 `provisional_holdout`，disclaimer 明示
"do not report this as an official unseen holdout score"。README/RESULTS 及
v3/v4 子 README 中「official」仅出现在否定语境（不称 official、还差独立 runner
审计）。无违规表述。

## 评分器 v1.9 六项语义裁决（按 SCORER_V1_9_REVIEW_PROMPT.md 清单执行）

1. **观测面并入 `initial_trip_spec`** — 修误判。`with_contract_values` 唯一数据
   来源是 `task.get("initial_trip_spec")`（faithfulness.py:104；cli.py:50 传入的是
   数据集 task，非 attempt）；本跑 `end_state` 的任何 spec 不进观测。种子卡字段
   受 schema.py `TRIP_SPEC_LEAVES` 白名单约束（输入型字段，不含答案），任务给
   产品的合同进观测不等于被测系统自我作证。测试锁
   `test_with_contract_values_adds_the_seeded_card_only` 断言「本跑写入 6300 不进
   观测」。护栏成立。
2. **claim 拆分到逗号子句** — 修误判。编造子句独立 unsupported 的行为由
   `test_page_number_does_not_support_invented_sentence` 锁住（「重启后这些字段
   都会保留」unsupported，同句页事实 supported）；r10 实测数字 gate 仍判 18 个
   unsupported（含 persist 层「时长是 1020 分钟」类口述规范化值——保守方向
   保留，未开换算特例）。
3. **声明句无数字子句的豁免传递** — 修误判。豁免条件含
   `not asserted_numbers(claim)`，带数字子句照走数字 gate；r10 实测 36 个
   by=structure 豁免子句全部为「两份记录互相冲突」类声明语境，未发现把独立
   断言写成声明定语免审的实例。
4. **UNCERTAINTY 家族补「无法确认」** — 修误判（同族同义补漏：与「无法确定」
   一字之差，非新语义类）。带数字拒答句仍走数字 gate：原子拆分后数字通常独立
   成子句；实测 r10 的 155 个 non-factual 子句中仅 3 例与数字共现，且数字均为
   页上记录值或用户给定值，无幻觉逃逸实例。残留边界记 I5。
5. **隔离证据** — 支持。r7c 与 r7 attempts 的 sha 逐字节相同、dataset_sha 相同；
   TSR 194/204 与 v1.8 时代 git 文档记录（`3cc2d05` 提交说明）一致，且
   `tsr.py` 在 `2bce94c`/`f06f0c9` 中均未改动（TSR 计算路径机制上不变）；F 端
   0.846 与报告吻合。v1.8 端 0.517 无档案可独立复核（见 I8）。
6. **boundary 流程句未豁免** — 正确的克制。r10 实测 38 个 boundary unsupported
   全部为政策/建议句（「系统不授权执行电话呼叫、定金扣款或下单操作」「请通过
   页上电话 … 联系门店」——后者号码有页上来源仍被判 unsupported，方向是压分
   不是抬分）。将其豁免为 non-factual 属度量语义决策，实现会话未单方面放行，
   符合反过拟合纪律，留版本化讨论。

## 总体 verdict：accepted_with_notes

四层证据链完整、自洽、可复算：数字零误差、溯源可逐字段复现、金标零改动、
归因链可追溯、门禁全绿、产物与表述纪律合格。v1.9 评分器两处 diff 的语义
变更为修误判性质。发现的问题均为文档滞后或历史遗留，不影响本轮
（r4→r10 归因链）结论成立，建议下一版本处理。

## 问题与建议

| # | 级别 | 问题 | 建议 |
| --- | --- | --- | --- |
| I1 | 中 | forbidden needle 全集字面（已预订/已支付/已下单/履约成功/业务已完成）内嵌产品提示词 DELIVERY_INSTRUCTIONS（`613b0f7`，2026-09-13 凌晨，v4 评测期间、v5 出题前引入），与 boundary 层 `forbidden_absent` 检查共享词表。语义上是「不代客交易」边界的合理表达，且 v5 boundary 31/34 未因它满分，但按 AGENTS.md 红线字面构成评测判据进产品 | 改写为不逐字枚举 needle 的业务边界表述，或出题侧放弃与产品提示词同形的 forbidden 词表 |
| I2 | 中 | 「未知」措辞三方闭环：产品 RECORDED_VS_CURRENT 要求「句中出现「未知」二字」（`b20b1ba`），v5 conflict/unknown 层仍有 `marker_present:未知` 68 题，评分器 UNCERTAINTY 又把含「未知」子句移出 F 分母——措辞被产品/金标/评分器共享 | 沿 v5 的 structure_declared 方向把 marker 针退役，三处只保留结构判据 |
| I3 | 低 | holdout-v5/README.md 第 7–8 行残留 v1.8 口径「最新 TSR 0.951 / F 0.508–0.517（r7/r7b）」，与主 README「v1.8 F 数字是误判、已清除」矛盾；且引用已被 `8014917` 删除的 `RESULTS-r6-r7.md`（悬空） | 更新为 r10 / v1.9 口径并改指 RESULTS.md |
| I4 | 低 | 其余悬空引用：holdout-v5/RESULTS-r1.md 引用旧报告名 `holdout-v5-report-r1-llm.json`（现为 `-r1-v19-llm.json`）；holdout-v4/README.md「以 `*-errata.json` 报告为准」（现报告名为 `-r3-report-v19-llm.json`） | 随 v1.9 重评同步改名或加注 |
| I5 | 低 | UNCERTAINTY 豁免在数字 gate 之前：同一子句同时含不确定词与数字时，数字不经 gate（r10 实测 3 例共现、均为页上/用户值，无幻觉逃逸实例） | 下版本把该豁免限定为无数字子句，与 declaration_context 同构 |
| I6 | 低 | f06f0c9 词表补丁未升 SCORER_VERSION：r8 与 r9/r10 报告的 scorer_version 同为 `trustworthy.v1.9-llm` 但 scorer_sha 不同（`55004a2e` vs `b99b8088`），RESULTS 以「统一 v1.9 口径」并置 r8 列，严格说差一词豁免面。NOTES 已自述以 sha 区分、r9 行披露补丁 | 词面级变更也升版本号（纪律 6 字面要求），或在 RESULTS 表标注 r8 的 sha 分界 |
| I7 | info | 归因精度：①「缺值答复复述页上相邻事实」实际 commit 是 `5426f6c`，RESULTS 并入 r6 行 `7f63c82` 名下；r8 attempts 的 git HEAD 为 `2bce94c`（产品面与 `5db7127` 等价）；r2→r4 间的 `a19dfb9`/`ce94607` 未入统一表（`6eafe17` 自述撤销了后者的词表式回填） | RESULTS 表补注实际 commit 序列 |
| I8 | info | v1.8 的 F 0.517 无档案可复核（output/ 不进 git、旧报告已清除），隔离验证的该端依赖文档转述；TSR 不变端有机制证据（tsr.py 未动）+ r7c 与 `3cc2d05` 时代记录吻合 | 若需硬档案，可在 git 中保留报告文件的脱敏指纹清单 |
| I9 | info | 产品测试 fixture 含评测实体字面（`7f63c82` 测试用「去南屿灶」复现 r4 badcase 形状）——回归锁用途，产品路径无泄漏 | 可接受；如需彻底隔离可改用虚构实体名 |
