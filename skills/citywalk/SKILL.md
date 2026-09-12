---
name: citywalk小吃
description: 城市漫步+特色小吃：串起地标与老字号，动线顺、好出片、边走边吃。
operations:
  - snapshot
  - extract
  - navigate
  - scroll
  - click
  - type
  - finish
---

# citywalk 小吃程序

有界程序：不授权新工具。已加载后只能使用 frontmatter 列出的浏览器操作。`read_skill` 只用于加载或切换程序，不是本程序的业务步骤。准备态禁提交由执行层强制。

1. 观测：对当前页 `snapshot` 或 `extract`；内容未读完时 `scroll` 后再观测。
2. 可选导航：仅当用户已给出 http(s) URL 时 `navigate`；不要编造地址或搜索引擎查询。
3. 核对：用当前快照原文核实地标、店名、菜单、营业与收费；身份与路线耗时需要来源。未知标未知，不编造履约结果。
4. 需审批的写入：每个 `click`/`type` 单独提出，只使用当前快照中存在的 idx。
5. 未知或受阻：登录、验证码或能力不足时暂停并交给用户；不要宣称业务已成功。
