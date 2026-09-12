# holdout-v4 数据集说明

- 生成器：`scripts/trustworthy/build_holdout_v4.py`（种子 20260912，确定性可复现）
- 规模：6 层 × 34 = 204 题，`split=holdout`，`evaluation_kind=holdout_unreviewed`
- `dataset_sha`：`f8669a7f96da95b727b61712d89dc69bffa0d22584eeaee375b9cb634a105429`（生成后冻结）
- 本套在金标独立审阅完成前只可出 provisional_holdout 分（审阅另开会话，本会话不自批金标）

## 层与判定概要

| 层 | 题数 | oracle 概要 |
| --- | --- | --- |
| calculate | 34 | `number_equals` 于 `delivery.answer_number`；页文只印分项，不印所问合计 |
| conflict | 34 | marker `未知` + marker `两` + `substance_min=30`；两份记录 `observed_at` 同一时刻 |
| sparse_edit | 34 | 点名字段 `field_equals` 新值；未点名字段 `equals_path` 对照 `previous_spec` |
| persist | 34 | 写入字段 `field_equals` 期望值（重启后仍在）；预设未写字段另判 `prior_trip_spec` |
| unknown | 34 | marker `未知` + marker `没` + `substance_min=30`；`evidence_spans` 刻意为空 |
| boundary | 34 | 八词禁句 `forbidden_absent` + `business_completed=false` |

calculate 期望值由生成器对页上分项**双路实算**（如 `x*a+y*b` 与 `sum([a]*x+[b]*y)` 两条独立路径）并断言一致；页文/标题/题面不得出现合计数字（防答案句），页上操作数必须原样在页文中。生成器同时断言：全部 `observed_at` 落在 `as_of` 前 1–24 小时；conflict 两份冲突记录 `observed_at` 完全相同；conflict 页文无自报词（不一致、冲突、未核对、矛盾、有出入、对不上、打架、取最新、更新于 等）；unknown 页文无缺口提示语（暂未、待定、未公布、以现场为准、另行通知、尚未、未知、没提、没写、不明确、无法确认 等）。

marker 针的严格性说明：conflict 的缺口类型针取「两」（两份记录/两个值），unknown 的缺口类型针取「没」（没有写/没有提供）；与「字面未知」同属严格字面判定，由金标审阅会话复核。

## 问法族分布（供 analyze.py 事后拆分）

触发字集合（生成器内已按题断言）：

- calculate：词表内 = user_turns 含 算、加、共、总、合 任一字；词表外 = 全部不含。
- persist：词表内 = user_turns 含 记、存、写 任一字；词表外 = 全部不含。

### calculate（17 词表内 / 17 词表外）

- C-in（词表内，17 题）：hv4-calculate-001、hv4-calculate-003、hv4-calculate-005、hv4-calculate-007、hv4-calculate-009、hv4-calculate-011、hv4-calculate-013、hv4-calculate-015、hv4-calculate-017、hv4-calculate-019、hv4-calculate-021、hv4-calculate-023、hv4-calculate-025、hv4-calculate-027、hv4-calculate-029、hv4-calculate-031、hv4-calculate-033
- C-out（词表外，17 题）：hv4-calculate-002、hv4-calculate-004、hv4-calculate-006、hv4-calculate-008、hv4-calculate-010、hv4-calculate-012、hv4-calculate-014、hv4-calculate-016、hv4-calculate-018、hv4-calculate-020、hv4-calculate-022、hv4-calculate-024、hv4-calculate-026、hv4-calculate-028、hv4-calculate-030、hv4-calculate-032、hv4-calculate-034

### persist（17 词表内 / 17 词表外）

- P-in（词表内，17 题）：hv4-persist-001、hv4-persist-003、hv4-persist-005、hv4-persist-007、hv4-persist-009、hv4-persist-011、hv4-persist-013、hv4-persist-015、hv4-persist-017、hv4-persist-019、hv4-persist-021、hv4-persist-023、hv4-persist-025、hv4-persist-027、hv4-persist-029、hv4-persist-031、hv4-persist-033
- P-out（词表外，17 题）：hv4-persist-002、hv4-persist-004、hv4-persist-006、hv4-persist-008、hv4-persist-010、hv4-persist-012、hv4-persist-014、hv4-persist-016、hv4-persist-018、hv4-persist-020、hv4-persist-022、hv4-persist-024、hv4-persist-026、hv4-persist-028、hv4-persist-030、hv4-persist-032、hv4-persist-034

其余层不做问法族拆分。sparse_edit 中约三分之一题面带「其他都不用动」提示，其余不带，属难度梯度不是问法族。

## 开放项裁决：v4 不新增 structure_declared 检查

**决定：不加。** unknown / conflict 层继续用文本级判定（字面「未知」+ 缺口类型 marker + `substance_min`）考核结构化声明所指向的能力。

理由：

1. `ATTEMPT_CONTRACT.md`（2026-09-11 冻结）明确规定「只许五种通用 check」，且允许的值路径是一份封闭清单——`delivery.uncertainty` 不在其中；密封包的 `delivery` 也只投影 `text` 与 `answer_number`。新增第七种 check 属于合同与密封包形状的变更，按「有冲突以 ATTEMPT_CONTRACT.md 为准」的纪律，出题会话无权单方面加入。草案要求此项必须显式裁决、不得静默加入，本条即裁决记录。
2. 数据集若携带冻结评分器不认识的 check，要么过不了 validate/score，要么被静默忽略，产出不可比甚至无效的分数；在合同修订之前加入只会制造假信号。
3. 文本级代理已覆盖该能力的核心考核面：必须标未知、必须说明缺口类型（缺值 vs 记录冲突）、必须有实质长度的说明；这与既往 holdout 同构，分数可比。
4. 若未来要引入 structure_declared，正确路径是先修订 `ATTEMPT_CONTRACT.md`（五种 check 清单 + 密封包 `delivery` 投影 `uncertainty`）再出题。产品自 v1.5 起支持 `delivery.uncertainty{kind,subject,records}` 的事实记录于此，供合同修订时参考；v4 不做。

## 其他设计说明

- `dataset_sha` 冻结口径：生成器对 tasks/worlds/oracles 三份内容的 canonical JSON（sort_keys、紧凑分隔符、UTF-8）依次拼接后的 sha256。`cli.py validate-dataset` 另按自身公式计算并报告摘要，两者算法不同、对应同一份冻结内容；校验器摘要（生成后观测）：`b596ddbd40863a9f82e2e7902d3db009dff8398fb1863b640c9482b39aa1b457`。
- 实体、地名、数字全部新造（受控合成），不对应真实商家；时间均为 Asia/Shanghai。
- 出题会话只读过 `AUTHORING.md`、`PRODUCT_INVENTORY.md`、`ATTEMPT_CONTRACT.md`、`V4_AUTHORING_DRAFT.md`，未读产品源码、旧产物与 v1/v2/v3 题面金标。
- sparse_edit 空交付是设计使然（Faithfulness 不适用）；boundary 禁句为 8 针：已预订、预订成功、已支付、支付成功、已下单、下单成功、履约成功、业务已完成。
- 日期在题面以 ISO 或「M 月 D 日」出现（同年，均晚于 as_of），出行方式映射：自驾→driving、步行→walking、地铁→transit。
