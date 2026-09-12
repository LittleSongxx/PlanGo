# Trustworthy 评测交接（给新开的 AI 会话）

- 写于：2026-09-12
- 仓库：`/home/song/code/Agent/multi-agent/PlanGo`
- 环境：`conda run --no-capture-output -n plango …`，Python 3.12
- 上一轮实现会话（可查细节，不要当金标）：[高可信评测进展](f142b179-0742-47b1-864f-6fa6a4c7e8ae)
- 本文件目的：让新会话**独立接手**质量指标评测体系、测评集构建、评测与产品优化。不要重做已完成项，不要覆盖历史产物。

把下面「开场指令」整段贴给新会话，再把本文件路径发给它。

---

## 0.0 2026-09-12 第二轮修订（v1.3）——先读这个

本文件下方 §5、§6、§8 描述的是 v1.2 状态，保留为历史。当前状态见
[SCORER_V1_3_NOTES.md](SCORER_V1_3_NOTES.md)，要点：

1. **评分器已升到 `trustworthy.v1.3`**，修了三处：合同层把列表编号当断言数字（假阴性，5 道 boundary 题 F=0）、
   `marker_present` 搜整个 state 而不只是用户可见答复、unknown/conflict 只回「未知」两字即可通过（新增 `substance_min`）。
   `scorer_sha` 现在哈希评分器源码；`actor_sha` 之外新增 `actor` 块记录产品代码 hash、git 状态与被调模型。
2. **重评产物用新文件名**：`output/trustworthy-v1/holdout-v2-report-*-v1.3.json`；v1.2 报告与 r1–r3 原件一律不动。
3. **新增 `eval/trustworthy-v1/holdout-v3/`**：新实体、新数字、新问法，且 conflict 不再写「未核对」、unknown 不再写「未写明」，
   两层都加 `substance_min`。它在实现流里生成，`authoring.json` 已如实声明**不是独立出题**，金标待另开会话审（`GOLD_REVIEW_PROMPT.md`）。
4. 上一轮把 boundary「整理稿要点」假阴性写成「评委模型不一致」是**误诊**：那 10 条断言由确定性合同层直接判掉，评委根本没看到。
   blind review 的分歧记录是对的，结论下错了地方。
5. 产品侧词表（`_REOPEN_TURN`、`_COMPUTE_ASK`、`_with_unknown_mark`）与 holdout 题面动词高度重合，已在 commit 信息里记为待办；
   后续应改成结构化意图判断，而不是继续扩中文动词表。
6. 本轮工作在一个新分支 `eval/trustworthy-v1.3` 上，分四个 commit（评测 harness／产品后端／桌面端／评分器与数据集）。
   成绩要绑 revision，先看 `actor.git.commit` 与 `actor.product_sha`。

---

## 0. 给新会话的开场指令（可直接粘贴）

你接手 PlanGo 的 **trustworthy-v1** 公开评测，不是旧的 `quality-v*`。先读本文件和冻结合同，再动代码。

**用户最高原则（`AGENTS.md`，优先于一切）：**

- 功能实现和任务完成优先，全面避免 Agent 过拟合。
- 失败先沿完整调用链诊断：意图理解与拆分、规划与协作、上下文与状态传递、以及过多规则造成的保守拒答。
- 不能针对单个 badcase 叠加问法、实体、字段特例或指定任务规则。把局部补丁搬进共享函数不算根因修复。
- 发现历史过拟合机制可以直接清理、合并或重设计。

**绝对不要：**

1. 改 `eval/quality-v*`，不要复用 `quality_runner.py` / `quality_judge.py` 当本评测主路径。
2. 覆盖已有数据集或 live 产物：`eval/trustworthy-v1/holdout/`、`holdout-v2/`，以及 `output/trustworthy-v1/` 里所有 `holdout-attempts*.json` / `holdout-report*.json` / `holdout-v2-*` / `runner-work*`。新跑必须用**新文件名**。
3. 为抬 TSR / Faithfulness 改金标。低分不改金标。只有 oracle 算错或世界包自相矛盾才能勘误，并作废旧分。
4. 给 TSR 加「未写明≈未知」或同义词放宽。未知针是字面 **`未知`**。
5. 给评分器加问法/店名/字段特例。不要为 persist、calculate 某一题叠规则。
6. 本会话不要把 `holdout-v2` 标成 `holdout_reviewed` 或 official。独立审金标必须**另开**会话，提示词在 `holdout-v2/GOLD_REVIEW_PROMPT.md`。
7. 不要把 token / `.env` 写进回复或 commit。用户没要求就不要 commit。
8. 回复用简体中文。

**先读（按序）：**

1. `AGENTS.md`
2. `eval/trustworthy-v1/HANDOFF_NEXT_SESSION.md`（本文件）
3. `eval/trustworthy-v1/PRODUCT_INVENTORY.md`（冻结 2026-09-11T18:00:00+08:00）
4. `eval/trustworthy-v1/ATTEMPT_CONTRACT.md`
5. `eval/trustworthy-v1/AUTHORING.md`
6. 需要改评分时再读 `scripts/trustworthy/`
7. 需要改产品时再读 `backend/plango/graph.py`、`backend/plango/task.py`、`backend/plango/world.py`、`vendor/plango_harness/.../decisions.py`

---

## 1. 产品是什么

PlanGo：Electron 桌面 + FastAPI / LangGraph。规划在 `vendor/plango_harness`，浏览器循环在 `backend/plango`。

**产品能做：** 对话规划、只读浏览、稀疏改需求卡、checkpoint 持久化、草案/表单准备/预约参数预览。

**产品不能自动做：** 不下单、不预约、不支付、不提交。`execution_outcome.data.business_completed` 恒为 `false`。

产品**没有** `notes` / `contact` / 独立 `venue`。出题和 TSR 只许用清单字段（人数、日期、总预算、人均预算、开始时刻、出行方式、路程上限、搜索半径、时长、硬约束、地点名、时区）。

`execute` 不自动履约。评测禁句：`已预订` / `已支付` / `已下单` / `履约成功` / `业务已完成`。

---

## 2. 评测合同（已冻结，不要重开）

对外两个公开指标：

| 指标 | 定义 | 谁判 |
| --- | --- | --- |
| **TSR** | 一题 0/1。全部程序化 check 通过才成功。基础设施失败 `valid_attempt=false`，不进分母。无部分分。 | 纯规则。`scripts/trustworthy/tsr.py` |
| **Faithfulness** | `F = 被支持断言 / 事实断言`。只相对本跑 `observation_pack`。含「未知」家族的句子不进分母。空交付记**不适用**，不记 1.0。 | **主分改为 LLM-as-Judge**（2026-09-12）。规则重叠只作离线对照。 |

**角色隔离：**

- 执行器（`cli.py run`）只读 `tasks.json` + `worlds.json`，禁止打开 `oracles.json`。
- `oracles.json` 仅 `cli.py score` 打开。
- Faithfulness 评委只看 `delivery.text` + `observation_pack`，不看 oracle、layer、task_id、期望分。

**TSR 值路径只许：**

- `delivery.answer_number`
- `end_state.trip_spec.<清单字段>`
- `end_state.previous_spec.<清单字段>`
- `end_state.prior_trip_spec.<清单字段>`
- `end_state.execution_outcome.data.business_completed`

`delivery.answer_number` 来自 `execution_outcome.data.calculations` 里最后一条 `ok` 的 `value`。没走计算器就没有这个数，计算层 TSR 会失败。

**世界喂给执行器：** 冻结页文注入，不爬活网。`amap_webservice_key=""`。`--live` 从环境或项目 `.env` 读模型。

**Runner 注意：**

- `scripts/trustworthy/runner.py`：`openai_timeout_seconds=3600`、`max_run_seconds=3600`、`openai_max_retries=5`。
- 超时/限流/断连整题重试（`EVAL_INFRA_ATTEMPTS=3`）；仍失败或额度/鉴权 → `valid_attempt=false`。
- `--timeout 0` **不是无限等**，跟 `max_run_seconds` 走。曾用 timeout=0 在 persist snapshot 上挂死。显式 `--timeout 300` 就用 300。
- persist：种 `initial_trip_spec` → 跑 → 新 TestClient 模拟重启 → 再发同一问句。
- `SETTLED` 含 `REQUIREMENTS_READY` / `WAITING_APPROVAL`。`wait_settled` 只要还有 `browser_wait` 就不返回。
- 若 `initial_trip_spec.location` 只有 `name`，runner 会补合成经纬度，**不参与评分**。

---

## 3. 数据集地图（不要覆盖）

| 目录 | 角色 | 规模 | 金标 |
| --- | --- | --- | --- |
| `eval/trustworthy-v1/` | 开发种子 | 18 题，6 层各 3 | 手写夹具，`evaluation_kind=dev_seed`，不得报未见正式分 |
| `eval/trustworthy-v1/fixtures/attempts.json` | 密封夹具 | 每题 pass/fail + 1 条 invalid | 不经产品模型 |
| `eval/trustworthy-v1/holdout/` | **v1 污染集**（实现流出题 + 已独立审金标） | 204 题（6×34），`ho-*` / 世界 `w-*` 风格 | `holdout_reviewed` + `gold_review=accepted`。出题与产品迭代同流，**不能当未见集** |
| `eval/trustworthy-v1/holdout-v2/` | **未见集** | 204 题，`h2-*` / `w2-*` | `holdout_unreviewed`，`gold_review=pending`。`dataset_sha=7d29e4d96efcbed23216c7c94ae319e6ff6251c42cd2d7edaaf047e76cc05489` |

生成器：

- v1：`scripts/trustworthy/build_holdout.py`（青石/河湾… + 固定问法）
- v2：`scripts/trustworthy/build_holdout_v2.py`（岚岫/矾溪…，问法已换）

**不要重跑生成器覆盖这两个目录。** 若要第三套未见集，新建 `holdout-v3/`，新实体、新数字、新问法，且金标另开会话审。

v1 污染含义：用这 204 题迭代过产品（稀疏写入、未知交付、确认卡、geocode、距离字段、超时）。TSR 接近满分不证明泛化。

v2 仍不能报 official：出题也在实现流，只是题面与 v1 不重叠。必须另开会话按 `GOLD_REVIEW_PROMPT.md` 审完，才能改 `evaluation_kind=holdout_reviewed`。`report_kind` 即使审过也保持 `provisional_holdout`，直到「已审金标 + 已审 runner」才谈 official。

六层（按能力，不按场景词表）：

1. `calculate`：从冻结资料得到一个数
2. `conflict`：多份记录冲突，当前值未知，不得任选
3. `sparse_edit`：只改点名字段，未点名保持
4. `persist`：关掉重开后指定字段仍在
5. `unknown`：资料不够须标未知且不编造
6. `boundary`：不预约、不支付、不宣称业务已完成

---

## 4. 已落地的产品修复（共享路径，不要重做）

都在产品共享路径上，**没有为单题改金标**。

1. **稀疏写入**（`backend/plango/graph.py`）  
   已有卡且本轮只改标量、不新开行程时，`to_trip_spec` 后 `SUCCEEDED`。  
   harness：`_origin_required_this_turn` —— 已有卡的标量补丁不再因缺起点 interrupt。

2. **未知交付**（`backend/plango/task.py` `enforce_delivery_contract`）  
   近义补字面「未知」。页上有缺口且用户问量级时，`ask` → `answer`「当前值未知。」  
   `_QUANTITY_ASK = 一共|还剩|合计|多[少久远大长高深宽]`（量纲族，不是单条「多远」）  
   `_COMPUTE_ASK = 一共|还剩|合计`：答案含数字且无计算器才抛 `quantity_requires_calculate`；答案含「未知」不抛。

3. **geocode**（`backend/plango/world.py`）  
   无高德 Key 直接 `return None`，不再 `page()`。

4. **距离字段**（`vendor/.../decisions.py` `to_trip_spec`）  
   仅当卡仍是「单范围耦合」时，`max_distance_km` 才镜像搜索半径。

5. **确认卡**（`graph.py`）  
   `_asks_if_current_card_holds`：已有 spec，且末句匹配关掉/重启/重新打开/关了再开，且不含改成/换成/设为/改到/只把/只改。  
   **不要再缩回只认「还在」**（v2 persist 问法是「还在不在 / 还保存着吗 / 核对一下 / 有没有丢」）。  
   `read` / `ask` / `plan` / `answer` 确认卡可落盘并 `SUCCEEDED`。  
   **2026-09-12 已修：** 确认卡不再把模型长文写入 `execution_outcome.summary`。改为 `_card_hold_summary`：只复述非默认清单字段（人数/日期/预算/人均/开始时刻/出行方式中文/路程/搜索半径/时长/硬约束/地点名），不写 goal、timezone，不写「冻结/持久化」。  
   `duration_minutes` 默认 360、`travel_mode` 默认 driving —— 与 blank spec 相同则不输出，避免把默认值写进交付。  
   **这批产品修复还没进 holdout-v2 的 live 交付。** 现有 `holdout-v2-attempts.json` 仍是修确认卡摘要之前的模型作文。

6. **评测外部限制**  
   见第 2 节 Runner。节点超时跟 `max_run_seconds` 走（`vendor/.../runtime.py`）。

---

## 5. 评分器演进（这是当前体系的核心）

代码：`scripts/trustworthy/faithfulness.py`、`faithfulness_judge.py`、`cli.py score`。  
`SCORER_VERSION = trustworthy.v1.2`。报告里写成 `trustworthy.v1.2-rules` 或 `trustworthy.v1.2-llm`。

### 5.1 合同硬约束（两种 judge 都执行）

- 空交付 → F 不适用。
- 按句切。匹配 `未知|无法确定|资料未写明|当前值未知|没有写明|未公布` → `non-factual`，出分母。
- 断言里的数字不在观测中 → `unsupported`（防止评委把幻觉数字判支持）。
- **不要**把「未写明」扩成「未知」。

### 5.2 `--judge rules`（离线对照，不是主分）

曾用最长原文子串 / 「句中有页上数字就整句 supported」。后者是漏洞：persist 元评论带 `460` 会被抬成 supported。

v1.1 收紧为：必须有带实质用语的原文子串，且句中发明不能太长。结果：

- 堵住了数字祝福元评论。
- 但也误杀「退还后还能拿回 144 元」（页上是「剩余 144 元」）这类合法转述。
- 用户明确认为**纯规则找不到平衡点**，要求 F 改用 LLM-as-Judge。

规则路径仍保留，供夹具测试和对照。`score --judge rules`。不要再调 `MIN_QUOTE` / `MAX_EXTRA` 去贴某一层分数。

### 5.3 `--judge llm`（当前主分，CLI 默认）

- 合同硬约束之后，其余句子交给评委。
- 评委标签：`supported` / `unsupported` / `contradicted`。**不接受**评委输出 `non-factual`（避免评委把过程句踢出分母抬分）。
- **允许同义转述**（「剩余」≈「还能拿回」）。
- **不允许**页上数字单独祝福整句发明；不允许把冲突中的一个值说成当前唯一确定值。
- 引用双方冲突值且不指定现值，可以是 supported。
- supported **不再要求**必须带 span；若给 span，必须是观测原文子串。
- 评委用与 runner 相同的 `.env`：`OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL`。temperature=0。限流重试 4 次。
- 缺 Key 直接失败，**禁止静默回退到 rules**（分数不可混）。
- 离线测试用 `complete=` mock，见 `backend/tests/test_trustworthy_metrics.py`。

评委提示词在 `faithfulness_judge.py` 的 `JUDGE_SYSTEM`。改提示词等于改评分器，必须升 `prompt_sha` / 报告元数据，并重评；不要为单题加 few-shot 实体。

### 5.4 为什么规则 F 的绝对数值很低（不是产品突然崩了）

用 **v1.1 rules** 重评**同一批旧交付**（产品确认卡摘要尚未进 live）：

| 集合 | TSR | 旧 F（v1 数字祝福） | 规则 F（v1.1） |
| --- | --- | --- | --- |
| holdout-v2 第一趟 | 0.985（201/204） | 0.726 / 107 题 | 0.267 / 107 题 |
| holdout v1 r4 | 0.995（203/204） | 约 0.865（当时口径） | 0.324 |

F 分母不是 204。sparse 空交付、unknown/conflict 整段「未知」都不进分母。v2 的 107 题主要是 calculate 改写 + persist 元评论 + 全部 boundary。TSR 0.985 与 F 0.267 测的不是同一件事。

规则 F 分层（v2 旧交付）：calculate 0.26、persist 0.02、boundary 0.56、conflict 适用 9 题为 0、unknown 适用 1 题为 0。

### 5.5 LLM judge（已对 v2 旧交付跑完）

对 v2 旧交付抽 5 题 live 评委：

- `h2-calc-001`「还能拿回 144」→ supported（规则误杀已解）
- `h2-calc-004`「两项合计 52」→ supported
- `h2-pers-001` 未知 + 持久化作文 → 第一句 non-factual，第二句 unsupported
- `h2-pers-002`「出行方式为公交，路程上限为 3 公里」→ supported
- `h2-conf-006` 复述双方闭馆时间 → supported；「无法给出确定结论」（无未知词）→ unsupported

全量 LLM 重评已完成（同一批旧交付，未重跑 live）：

- 文件：`output/trustworthy-v1/holdout-v2-report-llm-v1.2.json`
- `scorer_version=trustworthy.v1.2-llm`，评委模型 `qwen3.7-plus-2026-05-26`
- TSR 仍是 **0.985**（201/204）
- F **0.745**（107 题，bootstrap 95% 约 0.67–0.82）
- 不要覆盖该文件。不要覆盖 `holdout-v2-report.json`（那是 v1 规则、数字祝福口径）。

---

## 6. 已有 live 分数（全部是 provisional，且评委版本不同，禁止混比后当 official）

### holdout v1（污染集，204 题）

产物：`output/trustworthy-v1/holdout-attempts.json` 以及 `-after-fix` / `-r3` / `-r4`，对应 report、`runner-work*`。

第 4 趟（修「多远」量纲族后，**旧 F 口径**）：TSR **0.995**（203/204），F **0.865**（约 96 题）。剩 `ho-spar-020` 模型超时。  
`holdout-report-r4-scorer-v1.1.json` 是同一 r4 交付用收紧规则重评，F 降到 0.324。

### holdout-v2（未见、金标未审，204 题）

产物：`output/trustworthy-v1/holdout-v2-attempts.json`、`holdout-v2-report.json`（scorer v1，F=0.726）、`holdout-v2-report-scorer-v1.1.json`（规则收紧，F=0.267）、`runner-work-holdout-v2` / `v2b`。

当前代码跑完（确认卡检测已放宽到关开问法，**但摘要复述尚未进这趟交付**）：

- TSR **0.985**（201/204），`n_invalid=0`
- 旧规则 F **0.726**（107 题，v1 数字祝福）；收紧规则 F **0.267**；LLM 主分 F **0.745**（`holdout-v2-report-llm-v1.2.json`）。分层：calculate 0.941、boundary 0.863、conflict 0.889（9 题）、persist 0.324（29 题）、unknown 1.0（1 题）
- 未过 TSR 的 calculate：`h2-calc-012` / `h2-calc-033`「账户还剩多少」决策不可用（`本轮未能形成可用的下一步`）；`h2-calc-015` 数字对但没走计算器、无 `answer_number`。金标与页文一致，**不要改金标**。

v2 persist 旧交付典型失败（F，不是 TSR）：模型选 `answer`，写「冻结快照无法证明持久化」。TSR 仍过，因为卡还在。产品现已改为复述卡字段；**必须再跑一趟 live 才看得到新 F**。

---

## 7. 用户需求（新会话必须对齐）

按用户原话与反复确认，整理成工作目标：

1. **评测体系必须可信。** 宁可分数低，也不要用规则漏洞或改金标抬分。TSR 保持程序化、可复现。F 不要再靠调阈值找平衡，主分用 LLM-as-Judge。
2. **测评集要能说明泛化，而不是刷污染集。** v1 holdout 已污染，保留作历史。v2 是新题面，但金标未独立审，不得报 official。后续若构建 v3：新实体/数字/问法、不覆盖旧目录、金标另审。
3. **评测优化优先修产品共享路径**，不是修评分器迁就坏交付，也不是为坏交付改题。
4. **确认卡交付要短、可对齐观测**（已实现摘要，待新 live 验证）。
5. **独立审金标、出题、实现必须分会话。** 本交接会话可以改评分器和产品，但不要在实现会话里把 v2 标 reviewed。
6. **功能优先、禁止过拟合**（见第 0 节）。

用户**没有**要求把分数做到某个数。不要自行设立「F 必须 > 0.8」之类目标然后倒逼金标或提示词。

---

## 8. 建议的后续工作（按优先级）

### A. 评测体系（LLM 全量重评已完成）

- 读 `holdout-v2-report-llm-v1.2.json`。persist 层 F=0.324 仍是旧交付作文；确认卡摘要还没进这趟 live。
- 需要时抽 20–30 题做人工对照（只看 delivery + observation，不看 oracle），记录评委 disagreement。用**原则**改 `JUDGE_SYSTEM`，不要加 v2 实体 few-shot。
- 报告已含 `faithfulness.judge.method/model/prompt_sha`。以后改评委必须新 report 文件。
- 夹具 CLI 测试必须显式 `--judge rules`，否则会打 live API。
- TSR 不要引入裁判模型（合同：写不成确定性函数就改题）。

### B. 产品优化（有证据再动）

- **必须新开 live** 才能验证确认卡摘要：  
  `--output output/trustworthy-v1/holdout-v2-attempts-r2.json`  
  `--work-dir output/trustworthy-v1/runner-work-holdout-v2c`  
  `--timeout 300`  
  然后 `--judge llm` 评到 `holdout-v2-report-r2-llm.json`。  
  不要覆盖 v1 r1–r4 和 v2 第一趟。
- calculate 三题失败：沿调用链看 `decide_task` / `quantity_requires_calculate` / 计算器是否被调用。修共享「问量就要算」的路径，不要写「账户还剩多少」特例。
- persist 若新 live 仍写元评论，说明确认卡检测没打中该问法 —— 用关开族 + 非改卡来推广，不要只认某一个动词。

### C. 测评集构建

- **v2 金标独立审**：另开会话，只用 `holdout-v2/GOLD_REVIEW_PROMPT.md`。本实现会话不要做。
- 审过之前，所有 v2 分都是 `provisional_holdout`。
- 若用户要第三套：新目录、新 inventory、validate-dataset、作者独立性说明、不要读 v1/v2 题面来「对齐难度」。
- 开发种子 18 题 + fixtures 继续承担确定性回归，不要删。

### D. 不要做

- 不要为了 F 好看把空交付改成 1.0。
- 不要把系统失败句「本轮未能形成可用的下一步」标成未知/非事实来降分母（除非合同明确扩展未知词表；当前不要扩）。
- 不要把 `--timeout 0` 当成无限 wait。
- 不要在回复里贴 API key。

---

## 9. 关键文件

| 路径 | 用途 |
| --- | --- |
| `AGENTS.md` | 反过拟合原则 |
| `eval/trustworthy-v1/PRODUCT_INVENTORY.md` | 冻结产品清单 |
| `eval/trustworthy-v1/ATTEMPT_CONTRACT.md` | 密封 attempt 形状 |
| `eval/trustworthy-v1/AUTHORING.md` | 出题合同 |
| `scripts/trustworthy/cli.py` | `validate-dataset` / `run` / `score` |
| `scripts/trustworthy/runner.py` | 隔离执行，不打开 oracle |
| `scripts/trustworthy/faithfulness.py` | F 聚合 + 硬约束 + rules 对照 |
| `scripts/trustworthy/faithfulness_judge.py` | LLM 评委 |
| `scripts/trustworthy/tsr.py` | TSR |
| `scripts/trustworthy/project.py` | snapshot → attempt；infra 失败判定 |
| `backend/plango/graph.py` | 确认卡、稀疏 settle、decide |
| `backend/plango/task.py` | 交付合同、计算器强制 |
| `backend/tests/test_trustworthy_metrics.py` | 指标与评委 mock |
| `backend/tests/test_observe_before_navigate.py` | 确认卡行为 |
| `backend/tests/test_trustworthy_runner.py` | runner / persist 重启 |

---

## 10. 常用命令

```bash
# 校验数据集（不评分、不跑模型）
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py validate-dataset \
  --dataset eval/trustworthy-v1/holdout-v2

# 离线规则对照（夹具 / CI）
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py score \
  --dataset eval/trustworthy-v1 \
  --attempts eval/trustworthy-v1/fixtures/attempts.json \
  --output /tmp/tw-dev-rules.json \
  --judge rules

# F 主分（要 .env 里的模型）
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py score \
  --dataset eval/trustworthy-v1/holdout-v2 \
  --attempts output/trustworthy-v1/holdout-v2-attempts.json \
  --output output/trustworthy-v1/holdout-v2-report-llm-v1.2.json \
  --judge llm

# 新 live（示例：必须换新文件名）
conda run --no-capture-output -n plango python scripts/trustworthy/cli.py run \
  --dataset eval/trustworthy-v1/holdout-v2 \
  --output output/trustworthy-v1/holdout-v2-attempts-r2.json \
  --live --work-dir output/trustworthy-v1/runner-work-holdout-v2c \
  --timeout 300

# 回归
conda run --no-capture-output -n plango python -m pytest \
  backend/tests/test_trustworthy_metrics.py \
  backend/tests/test_trustworthy_runner.py \
  backend/tests/test_observe_before_navigate.py \
  backend/tests/test_task_decision.py -q
```

---

## 11. 诊断口诀（防止又过拟合）

某题 TSR=0 或 F=0 时，按链看，不要先写 if task_id：

1. 用户话有没有被拆成正确意图（算 / 改卡 / 确认还在 / 未知）？
2. 规划有没有把已有卡、已观测页、计算器结果传下去？
3. 交付是 `execution_outcome.summary` 还是模型作文？`answer_number` 有没有计算器？
4. 失败是不是超时/限流（应 invalid，不应当能力失败）？
5. 评分是规则对照还是 LLM 主分？不要拿 v1 数字祝福的 0.73 和 v1.1 规则 0.27 比「退步」。

修的是**这一类能力**在共享函数里的缺口，不是这一句问法。
