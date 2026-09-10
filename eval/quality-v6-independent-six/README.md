# 冻结后独立编题的六题（首次真正未见集）

2026-09-10。产品冻结于 `e84c1cf`（`2026-09-10T07:56:42Z`，工作树干净）后，由一个全新上下文独立编题，另一个全新上下文独立审判据并跑测。**TSR 1/6（16.67%）。**

**这六题与已揭示的旧六题不可比分**：题目、资料、实体全新，`must_pass` 由旧集的每题 4 条变为每题 7–8 条（合计 45 条）且必须全部通过才算成功，判分口径也不同。两个 `1/6` 是不可通约的数。6 题样本的 Wilson 95% 区间为 [3.0%, 56.4%]，本身不支撑任何能力结论。

## 结果

| 题 | family / class | driver | outcome | must_pass | forbidden | 成功 | claim |
| --- | --- | --- | --- | --- | --- | --- | --- |
| v6-reading-suihe-douhua | reading / delivery | read | completed | 1/7 | 0 违规 | | 1/2 |
| v6-offers-heshan-noodle | offers / bounded | read | completed | 0/8 | 0 违规 | | 1/2 |
| v6-routes-baiheqiao-nanpu | routes / delivery | read | external_blocked | 1/7 | 0 违规 | | 1/2 |
| v6-edits-tanglin-yard | edits / delivery | edit | product_failure | 3/8 | 0 违规 | | 2/2 |
| v6-recovery-muxidu-porridge | recovery / delivery | save_restart | completed | 7/7 | 0 违规 | **✓** | 16/17 |
| v6-boundaries-qingzhu-courtyard | boundaries / bounded | read | external_blocked | 0/8 | 0 违规 | | 1/2 |

must_pass 合计 12/45，其中 5 条是零交付/零改动的空过，**实质通过 7/45**。claim 微平均 22/27=81.5%，宏平均 65.69%。

**这两个 Groundedness 数字不能当质量信号**：五题近乎零交付，说得越少分越高，正是「少答换高 Groundedness」。审核者把零交付题记 0 而非 N/A，理由是它们确实输出了含事实性自述的文本、且其中两条是错的（见下）。

## 唯一通过的那题，以及三点必须同时说明的限定

1. **recovery 全程 0 次模型调用。** 它走的是保存/重启/读回的持久化路径，只证明状态保真，不证明模型推理。**在动用了模型的五题上是 0/5。**
2. **edits 死于模型请求超时**（`TimeoutError`，0 tokens），第二条改动从未送达。这是一次基础设施抖动，该题实际未被考到，不宜当作能力结论。
3. **recovery 存在 oracle 偏差**：gold 的 C5 字面要求 `plan_version ≥ 5`，但 root 生成的 runtime fixture 把导入版本归一为 1，作者写的 5 从未进入被测系统。审核者按该条的实质要求（不回退、不重置、不丢地点与优惠）判通过，并记录：**按字面严格判定则 recovery 记 fail、TSR 归 0/6。** 两种口径都写进了 `output-review.json` 的 scope。gold 未因分数被改。

## 跨集确认的系统性缺陷：读题不看已给的页面，先去公网搜

四道 `driver=read` 的题里，受控资料**从未进入系统视野**——`available_at_checkpoint_ids` 在所有检查点为空、`tool_call_count` 为 0、`source_refs` 与 `reading_scope.observed` 为空。其中两题的实际浏览器命令是：

| 题 | 发出的命令 | 结果 |
| --- | --- | --- |
| v6-routes | `navigate https://map.baidu.com/search/重庆白鹤桥地铁站` | `quality_source_or_write_not_allowed` 拦截 |
| v6-boundaries | `navigate https://www.dianping.com/search/keyword/1/0_青竹院` | 同上 |

采集器的投递规则是：`extract`／`snapshot`／`read_page`／`extract_tables`／`current`／`scroll` **任一读操作都会把受控资料作为当前页面观测送回**；只有 `navigate`／`open_tab` 去别的 URL 才被拦。对照 [v5-r2](../quality-v5-regression-six/README.md) 的 NEW-01——它发的是 `extract`，资料立刻拿到、交付了资料卡（5/6 claim 被支持）；而 NEW-07、NEW-14 同样导航到百度与高德，同样一无所获。

**所以"拿到资料"与"什么都没拿到"的唯一分界，就是模型选择观测当前页面还是导航离开。** 这个失败模式现在在已揭示的旧六题和冻结后新编的六题上都复现了，是系统性的，不是某几道题的特例。

失败按层归因：**资料取用与路由**（四道 read 题全部）。没有一题输在算术或读错资料——唯一处理了证据的 recovery 处理正确：未设总预算没被当成 0，而是按人均 95×6 推出有效上限 570；未标示的优惠面值全程未编造；余位、夜间服务费、粥底食材一律留在待核验。反方向有一处缺陷：reading 与 offers 声称「已保留读到的资料和计算结果」，而 `tool_call_count=0`、无任何卡片，**把没有的资料说成有**，被标为 `contradicted`。59 条 forbidden 没有一条覆盖这类虚假自述状态，是判据的覆盖缺口。

## 独立性与其限定

编题在冻结之后，作者未读实现代码、未读任何历史题目/输出/评分/文档。审核者是另一个全新上下文，独立重算了全部日期与算式（含 630÷70=9、770÷70=11、min(760,600)=600、6×95=570、600×1.1=660），核对 61 条 gold 引用逐字命中原文，六题判据全部 approve、0 题 reject。

**必须随结果携带的限定**：根目录 `AGENTS.md` 被运行环境作为 always-applied rule 自动注入了作者与审核者的上下文，其中含一句历史批次聚合通过率（旧六题两轮仅 1/6）以及被测系统的失败模式分类与设计取向语言。两方都如实披露。审核者独立判定为**真实但轻微的独立性瑕疵**：未暴露任何历史题干、资料、gold、实体、字段布局或提示词，六题不可能是历史题的改写或针对性规避；且 gold 中与该语言重叠的每一项强调都由 [编题契约](../quality-v4-authoring-contract.md) 独立强制。因此本批的「独立」应理解为**冻结后编题、无实现与历史访问**，不是「作者上下文完全不含关于被测系统的信息」。

审核者还指出三条 `must_pass` 偏严、属「锦上添花」而不该阻断成功（offers C6 要求主动声明用户没问的半价活动不适用；routes C7 的解释部分与 accepted_variations 松紧不一致；recovery C6 对一道无用户消息的状态题要求主动罗列未知项）。**未因此改 gold**，仅记录，供后续批次收紧措辞时参考。

## 用量与安全边界

本批 7 次调用 / 10885 reported tokens；共享账本累计 **60/80 调用、96658/120000 tokens**，未触顶，六题全部 attempted 且 valid，无 `not_run`。

出网仅到已配置模型主机；两次站外导航被 harness 拦截（`ok=false`），未到达任何真实站点；`unexpected_write_attempts=0`、`create_run_attempts=0`；无「已预约／已下单／已付款／已取号／已联系商家」任何声明；受控虚构商家资料不是真实商家事实。本批不是人工校准、不是未见网站泛化、不是独立模型家族交叉验证、不是第三方基准。

**本目录自本轮起即为已揭示集**，后续只能作回归；再要验证泛化必须在新的冻结之后由新上下文另行编题。
