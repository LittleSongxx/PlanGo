# 评分器 v1.3 修订记录（2026-09-12）

上一轮（v1.2）的报告全部保留原件，本文记录 v1.3 改了什么、为什么改、以及重评后的数字。
**v1.3 报告一律写新文件名**，`output/trustworthy-v1/*-v1.2*.json` 与 `*-r3-llm.json` 等原件不动。

## 1. 三处评分缺陷

### 1.1 列表编号被当成断言数字（假阴性，压低 F）

`faithfulness.py` 的合同层在送 LLM 评委之前，对句子里任何 `\d+` 做「不在观测中即 unsupported」的硬判。
`NUMBER` 会匹配列表序号，于是「1. 包含两站：…」被判 unsupported，**根本没有送给评委**。

- v1.2 的 r3 报告里 `by=contract` 的 unsupported 断言共 10 条，**10 条全是列表编号**，集中在 5 道 boundary「整理稿要点」题，每题 F 被压到 0.0。
- 早先的 `holdout-v2-llm-v1.2-layer-audit.json` 盲审已把这几条记为「judge 与 reviewer 分歧」，但结论写成「分歧是评委模型不一致，不是缺原则」，于是只检查了要不要改提示词，没检查送进评委之前的那层过滤。
- v1.3 用 `asserted_numbers()` 先剥掉行首序号（`1.` `2、` `（3）` `• 4)` 等）再取数字；句中编造的数字照样拦得住。

### 1.2 marker 检查读的是整个 attempt，不是用户可见答复

`tsr.py` 的 `_haystack` 把 `delivery + end_state` 整体 dump 成 haystack，`marker_present` / `forbidden_absent` 在其中搜字面串。
于是**用户可见答复为空、只要状态里某处出现「未知」也能拿满分**。v1.3 起只读 `delivery.text`。

实测影响：对 v1/v2 已有全部产物，**0 题翻转**（产品本来就把「未知」写在可见答复里）。这一条是堵洞，不改已发布的分数。

### 1.3 只回「未知」两字即可通过 unknown / conflict

v1.2 里这两层 68 题的唯一检查是 `marker_present: "未知"`，且含「未知」的整句被排除出 F 分母。
按 `delivery.text` 统计实义字符（去掉标点、数字与「未知」本身）：

| 报告 | unknown 实义字符 < 12 | conflict 实义字符 < 12 |
| --- | --- | --- |
| v2 首跑 | 21/34（中位数 0） | 8/34 |
| r2 | — | — |
| r3 | 25/34（中位数 0） | 12/34 |

即 r3 的 conflict 层有 12 题答复就是「未知」两个字。v1.3 新增通用检查 `substance_min`（v3 数据集用 `chars=12`），
并在报告里加 `coverage` 块，把「空交付 / 只回不确定词 / F 实际覆盖多少题 / 把未计分题按 0 算的下界」全部写进报告头。

> 口径说明：`substance_min` 只剔除「未知 / 无法确定 / 无法给出确定结论」这类纯标记；
> 「未核对 / 未写明」算实义内容——它们是解释，不是标记。数字也算内容。
> 校准样例（阈值 12）：`未知`=0、`当前值未知。`=3、`末班时间未知。`=4、
> `停车费未知，柜台5元门口27元。`=12（通过）、`末班时间未知：柜台告示写 21 点，门口告示写 23 点，两份记录未核对。`=27。
> 阈值取在「只回标记（≤5）」与「真实答复（≥27）」之间的空档，宁可放过简短但完整的答复，也不误杀它。

## 2. 溯源缺陷

| 字段 | v1.2 的问题 | v1.3 |
| --- | --- | --- |
| `actor_sha` | 只哈希 `protocol/tasks/worlds`，两趟不同产品代码会得到同一个 actor 标识（r2 与 r3 实测同为 `99b0350cb03c`，其间 `task.py`／`graph.py` 在 03:31 被改过） | 新增 `actor` 块：`product_sha`（`backend/plango` + `vendor/.../plango_harness` + `skills/*/SKILL.md` 全量哈希）、`product_files`、`git{commit,branch,dirty}`、`model{model,base_url_host,configured}`（不含密钥） |
| `scorer_sha` | 只哈希版本字符串 + 评委元数据，两个不同的评分器构建会得到同一个指纹（所有 v1.2-llm 报告同为 `69a70c7d…`） | `scorer_sources_sha()` 哈希 6 个评分器源文件后并入指纹 |

### 2.4 已知：v3-r1 那份 attempts 的 actor 记的是跑完时的树

`output/trustworthy-v1/holdout-v3-attempts-r1.json` 的 `actor.product_sha` = `f26d5017bd3b…`，
`actor.git.commit` = `a1c711b`，但这一趟实际执行的是 **`a7ccd0b`** 的产品代码。
原因是身份当时仍按「跑完时」计算（15:29 开跑，15:52 落盘），中途的提交被算了进去；
修法见 commit `ce9ed94`（身份改为构造时快照），**之后的跑不会再出现**。

注意：`9281f8c1ffcf1609` 是 `a7ccd0b` 这棵**干净树**的哈希，并不等于那份 attempts 记录的
`f26d5017bd3b`——r1 开跑时工作树是脏的（上一轮的产品改动尚未提交），所以本趟分数只能绑到
`f26d5017bd3b` 这份树，不能写成「跑的是 a7ccd0b」。可自行复核两个干净提交的哈希：

```bash
git worktree add /tmp/plango-at-a7ccd0b a7ccd0b
conda run --no-capture-output -n plango python -c "import sys;sys.path.insert(0,'scripts');\
from pathlib import Path;from trustworthy.provenance import product_identity;\
print(product_identity(Path('/tmp/plango-at-a7ccd0b'))['product_sha'])"
git diff a7ccd0b a1c711b --stat -- backend/plango vendor/plango_harness skills   # 只有 import 增删与排序
```

两个 revision 之间产品文件只差两处：`graph.py` 删掉一个未使用的 import、`task.py` 调整两行 import 顺序——
都是 ruff 的 CI 门禁修复，不改变行为。原始 attempts 不作改写，以此处记录为准。
实测哈希：`a7ccd0b` → `9281f8c1ffcf1609`，`a1c711b` → `93004313a3ac5ed6`，r1 记录的 → `f26d5017bd3b`。

## 3. 重评与结论

- 重评产物：`output/trustworthy-v1/holdout-v2-report-*-v1.3.json`（新文件名，旧报告不动）。
- 金标未改：`dataset_sha` 全程不变（`7d29e4d9…`）。

实际重评结果（同一批交付、只换判据）：

| 交付 | v1.2 F | v1.3 F | TSR | F 计分题 | 空交付 | 只回不确定词 | F 下界（未计分按 0） |
| --- | --- | --- | --- | --- | --- | --- | --- |
| r3 | 0.945 | **1.000** | 1.000 | 110/204 | 34 | 60 | 0.539 |
| r2 | 0.846 | 0.887 | 0.966 | 115/204 | 34 | 55 | 0.500 |
| r2b（12 题定向） | 1.000 | 1.000 | 1.000 | 12/12 | 0 | 0 | 1.000 |
| v2 首跑 | 0.745 | 0.770 | 0.985 | 107/204 | 38 | 59 | 0.404 |

TSR 与 v1.2 完全一致（四趟都未变），说明 v1.3 的改动只影响 F 与报告口径，没有借判据变化改分。

r3 那 0.055 的差距**全部**来自列表编号假阴性（boundary 层 5 题从 F=0 回到正常）。修好后 r3 在 110 道计分题上
每一条被评委看到的断言都 supported；但请注意 F 仍是 110/204 题上的数，另外 94 题里 60 题只回了一个不确定词。
- 旧 attempt 在新合同下的对照（`delivery` 未变，只换判据）见下表；这是**同一批旧交付换判据**，不是新跑。

| 交付 | v1.2 口径 TSR | 加「未知 + 说明缺口」后 | 其中 unknown / conflict 掉分 |
| --- | --- | --- | --- |
| r3 | 204/204 = 1.000 | 169/204 = 0.828 | 23 / 12 |
| r2 | 197/204 = 0.966 | 163/204 = 0.799 | 21 / 13 |
| v2 首跑 | 201/204 = 0.985 | 173/204 = 0.848 | 20 / 8 |

### 2.3 runner 从未真正服务过浏览器命令（v3 才发现）

`/api/v1/browser/commands` 的返回把命令字段**摊平在顶层**（`operation`、`arguments`…），
runner 却按 `command["payload"]["operation"]` 取，永远取到 `None`，于是**每一条真实浏览器命令都被回执为
`blocked / frozen_world_readonly`**（`runner.py` 的 `drain_frozen_browser`）。

v1／v2 全程没有暴露这个问题，因为页文同时被拼进了用户消息（`actor.compose_user_text`），
产品不需要真的去读页面。v3 的 unknown 题页上不写所问属性，产品于是发起 `snapshot`，
拿到 blocked 回执后停在 `browser_wait` 不再前进，整题挂到 settle 超时。

修好后同一题 **6 秒完成**，并给出「页面只写了…，未包含…，因此今天几点关门未知」。
含义有两层：

1. 这是一个真实的 runner 缺陷，修前所有"浏览器"行为都没有被测到；
2. v3 与 v1／v2 因此不在同一测量制度下——v3 才第一次让产品走浏览器命令环路，跨套比较要说明这一点。

### 3.1 已知遗留：一个产品测试在本次提交的树上就是红的

`backend/tests/test_browser_harness.py::ActionAndPlanningCheck::test_real_provider_contract_compiles_and_exact_selection_preserves_spec`
在提交后的树上失败（全套 607 项中 1 项）。

- 现象：run 停在 `REQUIREMENTS_READY` 的地点澄清「未能取得望京的真实起点坐标，请提供定位，或配置高德 Key 并明确出发地点。」
- 原因：`world.geocode` 现在无 Key 就返回 `None`（不再用浏览器兜底造坐标），而该测试的 `settings()` 传 `amap_webservice_key=""`，
  同时结构化输出只给了 `location_name="望京"` 这个名字，没有坐标。
- 归属：来自产品侧改动（`a45a5ca` 那一批，进入仓库时即如此），不是本轮评测改动的结果；
  本轮对产品代码的唯一改动是删掉一个未使用的 import 与一次 import 排序。
- 修法二选一（未做）：测试提供可解析的起点（客户端坐标或桩地理编码），或产品在 Key 缺失时对同城名称回落到客户端坐标。

## 4. 仍然存在的限制（不要靠改代码掩盖）

1. **F 的分母仍由产品自己的输出决定**：空交付与整句不确定都不进分母。v1.3 只能把覆盖率和下界写进报告，不能替产品补答。
2. **计算题的答案仍写在页文里**：评分合同规定「页上没有的数字直接 unsupported」，计算题若把结果藏起来，F 会把它当编造。这是评分器限制，不是产品能力要求；要真正考算术，得先让计算结果能进 F 的合法来源。
3. **被测模型与评委仍是同一个模型**（`.env` 的 `OPENAI_MODEL`）。文档口径只能写「AI 自评 + 盲审」。
4. **worlds 是冻结注入文本**，页文同时进入用户消息；不覆盖真实浏览器加载、导航与真实商家履约。
5. **204 题的实际问法远少于 204 个**（每层 5–10 个模板 × 3–7 次重复），Wilson 区间与 bootstrap 都偏窄。
