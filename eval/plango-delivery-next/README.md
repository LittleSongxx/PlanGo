# D1/D2 发送恢复与执行配置

2026-09-09；全部故障注入使用专属 HTTP、SQLite、PostgreSQL 或 Electron profile。没有调用真实商家、提交交易或运行质量测评。这些是可靠性与界面检查，不是用户数据事故、商家履约或成功率测量。

- `transport-before.json` 保存改动前三个失联窗口：请求未到达、接受后响应丢失、接受后快照失败。后两者服务已创建任务，但调用方没有 run ID；历史文字并未因此被证明从所有存储中消失。
- `transport-after.json` 保存修复后的受控传输检查。固定请求与内容指纹先落盘；创建/消息重试、同文新轮、图片/选店、原位置/Skills、重启和服务切换分别核对。直接或丢失的内容冲突响应不能确认成新输入已送达。
- `backend/tests/test_input_acceptance.py`、`test_input_acceptance_races.py` 覆盖事务回滚、旧数据库升级、并发接受、已接受命令/预算不被迟到修改覆盖、租约/取消/UNKNOWN保护；`postgres-check.json` 是双独立运行时的真实 PostgreSQL 并发和迁移结果。
- `scripts/check-delivery-ui.ts` 覆盖本地草稿、只读恢复、双击、跨会话迟到回包、原任务目标和存储失败；`scripts/check-delivery-storage.cjs` 使用两次独立 Electron 进程验证 8 MB 图片引用与内容完整恢复。没有把普通网页的配额推测当作本项目已发生的故障。
- `scripts/desktop-ui-smoke.cjs` 使用实际构建的 main/preload/renderer、可见原生浏览视图及受控服务器，检查一次接受响应丢失后取回原任务，以及服务/桌面模型配置、显式诊断、旧服务、缺密钥与断网的界面。

原始检查日志位于私有 `output/delivery-next/`、`output/d1-delivery-ui/` 和本轮 `output/desktop-ui-smoke/`；原备份/profile不公开。主服务更新的数据连续性单独记录在 `main-continuity.json`，不能用受控数据库测试替代主数据核验。

后续 D3–D7 尚需继续，首批不代表优惠决策、真实表单或独立用户验收已经完成。
