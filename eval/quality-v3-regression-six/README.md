# 六题已揭示资料回归

2026-09-10。产品冻结 `53d4ef50eb5b1adf6ba1230931ac96d9f8c94450`，product SHA `221adb3d444868e655989d5b8da293df70814d5f4f0214857d4bc8f3c153bfbb`。原30题中预先选定NEW-01/07/14/18/21/29，每家族一题，输入、来源和gold原样保留；这是回归集，不是未见测试。

| 指标 | 完整UI补审后的最终结果 |
| --- | --- |
| TSR | **0.0%（0/6）**，delivery 0/5、bounded_answer 0/1 |
| Groundedness | **91.18%**，6题宏平均；105/116条事实有支持，N/A=0 |
| Actor用量 | 21调用、14443 tokens；6有效尝试、0无效、无缺usage |

高事实支持主要来自已保留的原文摘录，不能替代所需计算、比较、判断和草案交付。同六题旧版也没有整题通过，本次未证明TSR改善；不能把这6题的G与原30题54.3%直接相减当成总体提升。

四题是原始观测注入；NEW-18为真实Electron接受响应丢失/查询取回，字段19:20/120分钟及运输条件通过，但未达到原`draft_review`停点；NEW-29为真实Electron编辑，预算澄清保留，但明确人数仍为2而非4。所有失败保留，没有有效题重跑、改gold或临时追加回答。

独立上下文AI审核，人工0；不是模型身份盲审、独立模型家族交叉验证、真实商家履约或第三方成绩。Token仅计Actor，不含Codex编题/审核。

首次评分包遗漏两题原已采集的完整UI正文和同期API附件，初审G=94.33%保留在`scores-initial.json`及`review/initial-*`。最终仅追加原始UI文本、原API状态、时间/hash与绑定，不重渲染、不调用Actor；API附件明确不是独立SQL，后来的状态不能证明早输出。独立补审重新去重并将遗漏的当前UI事实纳入，最终以`review/bundle.json`及配套审核为准。原30题包和成绩未改、未重评。附录一致性检查见[本轮证据说明](../plango-quality-v3/evidence-amendment.json)。

可直接离线复算，无需主服务、浏览器或模型：

```bash
quality_score_dir=$(mktemp -d output/quality-v3-score-XXXXXX)
/home/song/miniconda3/envs/plango/bin/python scripts/quality_ai_import.py \
  --bundle eval/quality-v3-regression-six/review/bundle.json \
  --gold-review eval/quality-v3-regression-six/review/gold-review.json \
  --output-review eval/quality-v3-regression-six/review/output-review.json \
  --output "$quality_score_dir/regression.json"
```

预期`complete`、`issues=0`。数学/格式校验不证明AI语义判断绝对正确。

实际采集入口已接通：`quality_acceptance.py freeze/prepare/run/bundle`支持显式`--dataset/--freeze/--work`；本批参数分别为当前目录、`product-freeze.json`、`output/quality-v3-stage/regression-session`。原session及有效attempt禁止复用覆盖。下一版本先另建明确protocol与新freeze/work，再独立审gold，不删旧registry或改旧冻结来重跑。

私有原件、SQLite/profile、原模型日志、原包与两版审核保留于上述session。与[六题独立新集](../quality-v3-independent-six/README.md)共用80调用/120000已报告tokens停止阈值和同一追加预算账本；没有分批续额度。[总报告](../plango-quality-v3/README.md)区分工程修复、质量结果、部署与未完成范围。
