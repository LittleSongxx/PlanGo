# Trustworthy 评测交接（给新会话，2026-09-13 版）

- 仓库：`/home/song/code/Agent/multi-agent/PlanGo`，分支 `eval/trustworthy-v1.3`
- 环境：`conda run --no-capture-output -n plango python …`（Python 3.12，模型配置在 `.env`）
- 回复用简体中文。本文取代旧版交接；旧内容在 git 历史。

## 0. 给新会话的开场指令（整段粘贴）

你接手 PlanGo 的 trustworthy-v1 质量评测与产品优化。先读 `AGENTS.md` 和本文件，再动代码。

**最高原则（AGENTS.md）**：功能实现和任务完成优先，全面避免 Agent 过拟合。失败沿完整调用链诊断
（意图理解与拆分、规划与协作、上下文与状态传递、过多规则造成的保守拒答）。不能针对单个 badcase
叠问法、实体、字段特例或指定任务规则；把局部补丁搬进共享函数不算根因修复。发现历史过拟合机制
（尤其中文词表/字符清单类判据）可以直接清理、重设计。

**绝对不要**：
1. 覆盖既有产物：`eval/trustworthy-v1/` 下已提交文件与 `output/trustworthy-v1/` 全部现有文件。新跑必须用新文件名。
2. 低分改金标。只有 oracle 算错（期望值在任何合法输入中不存在）或世界包自相矛盾才能勘误，
   且作废旧分、更新 `dataset_sha`、在 README/RESULTS 留痕、交独立审阅会话复确认。
3. 为抬分给评分器加店名/问法 few-shot、扩「未写明/未核对」类词表；产品侧同样不许加触发词表。
4. 把任何分数称为 official 或「未见泛化」：全部 provisional_holdout，评委与被测同模型。
5. 自审金标：独立审阅必须另开会话。
6. 产品不能自动履约（不下单/不预约/不支付），`business_completed` 恒 false。
7. 密钥不进回复与 commit；用户没要求不 push。

## 1. 当前状态快照

### 评分器：`trustworthy.v1.8`（scripts/trustworthy/）

演进史 v1.3→v1.8，每版有 `eval/trustworthy-v1/SCORER_V*_NOTES.md`：
- v1.5：产品决策结构 `uncertainty{kind: missing_value|conflicting_records, subject, records}`；
- v1.6：计算器 ok 行并入评委合法观测；
- v1.7：用户本轮原话并入合法观测（复述用户数字不再判编造）；
- v1.8：**F 只评 `outcome=completed` 的交付**（失败轮兜底文案是过程不是断言，离开分母计入
  coverage）；新增第七种 check `structure_declared`（读 `delivery.uncertainty` 的
  kind/min_records）。`ATTEMPT_CONTRACT.md` 已修订为七种 check + uncertainty 值路径。

### 数据集与最新分数（全部 provisional）

| 套 | 金标 | 最新 TSR / F | 说明 |
| --- | --- | --- | --- |
| holdout（v1） | reviewed（旧轮） | 历史留档 | 污染集，勿引用 |
| holdout-v2 | 未审 | 0.985 / 0.858（v1.4） | 已被迭代过，只作历史 |
| holdout-v3 | accepted | 0.926–0.931 / 0.99 | 同码方差 ±1 题；calculate 旧合同矛盾已由 v4 设计消除 |
| holdout-v4 | accepted_with_errata | 0.662 / 0.505（r3+v1.8）；去针反事实 0.917 | 「两/没」针保留并声明为测量偏差 |
| holdout-v5 | **未审** | **0.799 / 0.432（r4）** | 见 §1.1 的 r1–r4 对照 |

### v5 四轮对照（`holdout-v5/RESULTS-r2-r4.md`）

基线 0.588 → 文本推断回填 0.745（修出两个自制缺陷）→ 推断修补 0.799 →
**架构版（准入重试）0.799，保留实现（`6eafe17`）**。架构版：未知答复缺 `uncertainty`
声明 → 抬 `uncertainty_declaration_required` → 适配器带错误对 schema 重试（与
`quantity_requires_calculate` 同机制同额度，`graph.py` 约 1225 行）。
**conflict 34/34 满分**；unknown 8/34 是当前主缺口。

### 已验证并固化的机制（新会话应沿用，勿回退）

1. **准入重试优于合同侧文本推断**：语义归属决策模型（它读了记录），合同只命名要求。
   r2 教训：合同从文本反推语义必然产出下一个词表补丁，且会制造页上没有的数字。
2. **结构化判据优于触发词表**：v3 的问法族拆分（词表内 1.000 vs 新问法 0.350）是词表
   过拟合的直接证据；`_REOPEN_TURN`/`_COMPUTE_ASK` 已删，由状态判据替代。
3. **runner 多轮**：stateful 任务按 `user_turns` 顺序发全部轮；persist 在写入轮后模拟
   重启再发末轮。曾经的「只发第一轮/重发第一轮」假设已修（有回归测试）。
4. **溯源**：attempts 记跑前快照（product_sha/commit/model）；改产品先 commit 再跑，
   避免不可复现中间态（v3-r4 曾踩过）。
5. **拒答文案与八禁词解耦**：提示词与模板不得包含
   「已预订/预订成功/已支付/支付成功/已下单/下单成功/履约成功/业务已完成」字面。
6. 评分器改版必须：升 `SCORER_VERSION`、写 NOTES、新文件名重评、旧报告不动。

## 2. 后续计划（按优先级）

1. **v5 金标独立审**（另开会话）：先照 `holdout-v4/GOLD_REVIEW_PROMPT.md` 的模式为 v5 写
   一份（路径换 holdout-v5；task_id 前缀 `hv5-`，层缩写 bnd/calc/cflt/sprs/prst/unk）。
   裁决点：①无 outcome 轮的 `business_completed=None` 被 `field_equals false` 双重惩罚的
   检查形状；②calculate/persist 的 substance_min=30 阈值（新加的可解释性要求）；
   ③`structure_declared` 的 kind/min_records 形状。审后 accepted 才改
   `evaluation_kind=holdout_reviewed`。
2. **unknown 层产品工作（8/34，主缺口）**：准入重试后仍失败的主因是答复实义 <30 与
   重试后仍未声明。方向：让缺值场景的答复路径像 conflict 一样成句说明（复述页上有什么、
   缺什么），声明由模型在重试中完成——不要再回到合同侧推断。
3. **persist 残余（28/34）**：口述多字段时偶发字段丢失（约 5–8 题/轮）。已知护栏：
   人均/总预算伪清除已修（`to_trip_spec` 只在清除标志单独出现时生效）。
4. **F 覆盖**（v5 计分 84/204）：sparse 空交付是设计使然；boundary F 低是拒答句被判
   unsupported，与 substance 要求相关，属合同语义的已知限制，改动需版本化讨论。
5. **official 前置**（不急）：已审金标 + 已审 runner。runner 本轮改了多轮发送与浏览器
   命令，若要走 official 需一次独立 runner 审计。

## 3. 常用命令

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
conda run --no-capture-output -n plango python scripts/trustworthy/analyze.py \
  output/trustworthy-v1/holdout-v5-report-rN-llm.json

# 门禁
conda run --no-capture-output -n plango python -m ruff check backend/plango backend/tests
conda run --no-capture-output -n plango python -m pytest backend/tests -q
```

## 4. 关键文件

| 路径 | 用途 |
| --- | --- |
| `AGENTS.md` | 反过拟合最高原则 |
| `eval/trustworthy-v1/ATTEMPT_CONTRACT.md` | 评测合同（2026-09-13 修订版：七种 check、uncertainty 投影、F completed 规则） |
| `eval/trustworthy-v1/SCORER_V*_NOTES.md` | v1.3–v1.8 演进与验证记录 |
| `eval/trustworthy-v1/holdout-v{3,4,5}/RESULTS-*.md` | 各轮结论与归因 |
| `eval/trustworthy-v1/V{4,5}_AUTHORING_DRAFT.md` | 出题草案（出题须另开会话） |
| `scripts/trustworthy/` | cli/runner/tsr/faithfulness/provenance/analyze* |
| `backend/plango/task.py` | 交付合同：准入重试、算式附注、unknown 标记 |
| `backend/plango/graph.py` | settle/确认卡/echo 归一化/重试额度 |
| `vendor/.../decisions.py` | `to_trip_spec` 字段合并与清除护栏 |
| `output/trustworthy-v1/` | 全部 attempts/report/logs（勿覆盖） |

## 5. 历史教训（防重蹈）

- 每次引入文本判据前自问：这是结构还是词表？词表必被下个问法打穿，然后诱发下一个补丁。
- 改产品后先 commit 再跑 live（脏树中间态不可复现）。
- 同码复跑方差 ±1–2 题，单层 ±3 题内的波动不要读成能力变化。
- 数据集「失败」先查三处再怪产品：runner 是否把所有轮送达、oracle 期望值是否在
  合法输入中存在、检查是否在惩罚措辞而非能力（v4 的 5 题 sparse「失败」其实是 runner
  丢轮，v3 的 14 题 calculate 是合同矛盾）。
