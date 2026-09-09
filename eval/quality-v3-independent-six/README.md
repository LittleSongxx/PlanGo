# 六题独立新资料受控评测

2026-09-10。先冻结产品，再由新隔离上下文仅读执行环境契约编写六个新来源组，另一隔离上下文审gold后才运行；未读取产品、旧题或旧输出编题。产品为`53d4ef50eb5b1adf6ba1230931ac96d9f8c94450`，与[回归批次的冻结](../quality-v3-regression-six/product-freeze.json)相同，product SHA `221adb3d444868e655989d5b8da293df70814d5f4f0214857d4bc8f3c153bfbb`。

| 指标 | 最终结果 |
| --- | --- |
| TSR | **16.7%（1/6）**，仅FRESH-05保存恢复通过 |
| Groundedness | **82.08%**，6题宏平均；163/167条事实有支持，N/A=0 |
| Actor用量 | 21调用、22575 tokens；7次尝试=6有效+1无效，无缺usage |

163/167是事实总计数，不是主百分比算法。四题delivery、两题bounded_answer，六家族各一题；四题原始观测注入、一题真实Electron两轮编辑、一题真实Electron保存恢复。资料均为虚构受控来源，不是未见网站、真实商家完整准备/履约或第三方成绩。独立上下文AI审核，人工0、非模型身份盲审，也没有独立模型家族交叉验证。

FRESH-01错误进入起点澄清；FRESH-02/04/06虽然资料事实有支持，仍未完成要求的计算、比较或判断。FRESH-03第一轮4人/440元正确，第二轮4人/每人115元/90分钟写入，但最后停在澄清，没有最终草案。不能用保存题通过、原文事实支持或单项字段正确代替整体交付可靠性。六个新来源已揭示，后续只能用作回归。

保存恢复题首次采集漏执行“后端重开后再次打开桌面”步骤。独立[裁定](transport-adjudication.json)将唯一受影响的`FRESH-05:first`列为`runner_error invalid`；原资料/DB/profile/回执/零模型用量永久保留，其他五题有效失败未动。采集器`20dcd94`统一修复该流程后，仅替代一次；不是为产品失败挑最好成绩。

`FRESH-05:replacement-1`实际保存一次、关闭桌面与API，再重开API/DB及原profile桌面，读取同一保存任务；恢复阶段写入、消息、新任务与模型调用均为0，原run/版本/优惠/预算状态哈希一致。后端重启指LocalAPI服务生命周期关闭、新app及数据库连接重开，父Python进程没有退出；Electron为真实新进程，不把该检查说成WSL整机重启或长期稳定。

另有**已有证据补包**：同期API快照、原已采集完整UI文本补入`review/bundle.json`，绑定原hash和捕获时间，未重渲染。API状态不是独立SQL，后期证据不回证早输出。这与上述新的替代执行分开记录，原包见`review/initial-bundle.json`。最终独立审核覆盖全部36个标准输出面及14个公开UI/context面，含去重与初态旧回复排除记录，见`review/output-review.json`。

可直接离线复算：

```bash
quality_score_dir=$(mktemp -d output/quality-v3-score-XXXXXX)
/home/song/miniconda3/envs/plango/bin/python scripts/quality_ai_import.py \
  --bundle eval/quality-v3-independent-six/review/bundle.json \
  --gold-review eval/quality-v3-independent-six/review/gold-review.json \
  --output-review eval/quality-v3-independent-six/review/output-review.json \
  --output "$quality_score_dir/independent.json"
```

预期`complete`、`issues=0`。原件和全部尝试保留于`output/quality-v3-stage/independent-session/`。两批累计42调用/37018 tokens，共用同一预算账本；无额外模型重跑。详细工程、部署与统计限制见[总报告](../plango-quality-v3/README.md)。

独立终审另纠正了一条预算表示的事实错标：4人、每人115元与空的独立总预算字段仍表达本轮460元有效上限，不能据null认定预算丢失。原gold及输出未改，C3按冻结具体字段要求的原判保留并注明局限；缺最终草案仍独立导致整题失败。受影响原子事实与误报完成标签统一更正，其余五题不变，G由81.67%更正为82.08%，TSR不变。原v2审核/分数与[更正记录](independent-output-review-v3-change-log.json)、[语义说明](FRESH-03-budget-semantics-note.json)均保留。
