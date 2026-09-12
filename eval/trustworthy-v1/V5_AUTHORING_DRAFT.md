# holdout-v5 出题合同草案（给出题会话，非正式合同）

状态：**草案**。正式出题另开会话（纪律同 v4：不读产品源码、旧 attempts、实现记录、
v1–v4 题面）。新建 `eval/trustworthy-v1/holdout-v5/` + `scripts/trustworthy/build_holdout_v5.py`，
新实体/数字/问法，204 题（6×34）。

## 相对 v4 的设计改动

1. **「两/没」单字针替换为 `structure_declared`**（本次核心）：
   - conflict 层：`marker_present: 未知` + `substance_min: 30` +
     `structure_declared {kind: conflicting_records, min_records: 2}`；
   - unknown 层：`marker_present: 未知` + `substance_min: 30` +
     `structure_declared {kind: missing_value}`。
   合同依据：`ATTEMPT_CONTRACT.md`（2026-09-13 修订版，七种 check）。
   保留「未知」字面针与 substance（用户可见底线），删去措辞类型针。
2. **sparse 双轮化推广**：约半数题两个 turn（两句连续改字段）——runner 已支持顺序多轮；
   oracle 对第二句点名字段同样出 `field_equals`。避免再引入「地点更换且无 Key」类
   与产品地理政策冲突的题（换地点题须在世界页文给出可核坐标，或不出）。
3. **persist 沿用 v4 双轮形态**（口述写入 → 重启 → 问还在），预设字段的保持检查
   继续用 `field_equals + equals_path` 双检查；生成器断言「口述子句数 == 期望字段数」。
4. **calculate 沿用 v4 分项设计**（页文不印合计、双路实算、v1.6/v1.7 已就绪）。
5. **时间锚**：`as_of` 晚于等于全部 `observed_at`（生成器断言，v4 已修）。

## 沿用不变

- 六层定义、`split=holdout`、合成实体、角色隔离字段零容忍、`evidence_spans` 规则；
- conflict 两记录同时刻、无自报词；unknown 页文无缺口提示语；
- calculate/persist 问法族混编（词表内/外各半，分布写进 README 供事后拆分）；
- 生成器内全部设计断言（双路算术、防答案句、针分布、时间锚）；
- 金标另开会话审；审前 `evaluation_kind=holdout_unreviewed`，分数只称 provisional。

## 出题会话纪律

同 `V4_AUTHORING_DRAFT.md`：只读四份合同文件与本草案 + 自建目录；用
`cli.py validate-dataset` 收口；不跑模型、不出分。完成后写 README 说明层规模、
问法族分布与任何显式裁决。
