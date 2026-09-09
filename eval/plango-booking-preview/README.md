# 悦廊参数预览：禁止锁位与预约请求

2026-09-09。用户已明确允许悦廊2人、2026-09-11 15:00的准备验证，条件是不提交预约、不产生真实业务影响。本轮完成的是**受保护的网页参数预览及同任务恢复**，没有查询空位或完成预约。

## 实际发现的副作用边界

TableCheck[官方 Booking v1](https://tablecheck.atlassian.net/wiki/spaces/API/pages/160334455/Booking+v1)说明锁位早于最终预约提交；[Availability v1](https://tablecheck.atlassian.net/wiki/spaces/API/pages/46301274/Availability+v1)虽有只读契约，但现场网页使用不同的私有`production-booking.szuo.com/v2`接口。

本轮读取现场已加载的公开客户端脚本，确认landing挂载调用`POST /v2/booking/cart/init`，Find availability默认路径调用创建/更新cart，日期面板调用`POST /v2/booking/calendar`。前端代码无法证明这些私有接口不锁位、续期或改变服务端业务状态，因此没有放行。不是只跳过最后的“提交”按钮。

受保护会话从文档导航前开始拦截，仅允许精确商家页面及已核对的静态资源GET/HEAD；cart、空位、checkout、遥测、未知XHR/后台请求均拒绝。标签跳转不会释放保护，标签关闭后的无归属请求继续拦截，普通其他标签仍可读取。未扩展自动click/type白名单，未把自定义控件伪造成native form。

## 真实参数与原任务接续

使用[官方支持的预填参数](https://tablecheck.atlassian.net/wiki/spaces/API/pages/48595292/Web+Booking+v1)，将`pax=2`、`start_date=2026-09-11`、`start_time=15:00`带入已经验证的悦廊入口；页面显示[2 Guests / Fri Sep 11 / 3:00 pm](page-parameters.png)。年份来自预填URL，网页控件没有独立显示年份；结果明确保留此来源差异。

真实run `82c0f6fbcb394a8eaba38f3c8d6990ea` 在独立`output/d7-next/tealounge-brha972n/`完成。仍是第1轮、原`initial`预算，累计2240 tokens/3 tools；两次真实模型抽取的用量保留。4条持久浏览器命令包含1条标签已关闭失败，没有删除失败或用新任务补成功。原顺风123行程、登录态与主库任务未替换。

网站须知暂停后实际关闭桌面/SQLite后端，再用原任务继续；旧标签失败沿现有只读重绑完成。源码随后将须知继续明确绑定当前可见标签，结果仍须严格核对目标URL、参数与当前快照。新增受控重启回归覆盖一次继续即可读新标签。保存后的真实任务又实际重启，原结果和累计用量保持。

[结果摘要](result.json)为`SUCCEEDED`、`data.scope=booking_parameters`、`business_completed=false`、`availability_checked=false`。这是参数核对完成，不能算预约完成。[结果卡](final-preview-card.png)展示本次人数、日期、时间和未查询状态，不把无菜品/无价格的参数页当套餐比较。

[网络记录](network.json)显示本次记录阶段没有业务API请求通过发送前检查，cart/init被`ERR_BLOCKED_BY_CLIENT`阻断。早期D7仅做UI调查时未采集网络日志，不能据早期“未点提交”记录追溯断言网站从未初始化匿名cart；本轮才查明并实施这层保护。

## 验证与保留

[工程检查](checks.json)：完整npm check在须知接续补修前386 pytest+43 subtests及TS/构建通过；最终定向24项和契约8项通过，Ruff/mypy71文件、TS/UI投影/构建通过。真实WCV/IPC/Playwright浏览器88项断言包含网络请求在连接本地受控端点之前被阻断、迟到请求、其他标签和自动写入权限保持。首次采用自定义HTTPS协议fixture时该协议绕过正常webRequest路径，测试失败原样保留；最终改用真实HTTPS请求指向本地受控监听器，未向真实商家制造测试请求。

私有现场与失败日志：`output/playwright/d7-next/live-1788942063023/`（脚本/网络只读调查）、`live-1788943739408/`（真实任务/重启）、`output/d7-next/preview-*.log`。窗口与独立后端已关闭，数据库/profile保留。完整质量sweep和长时压力仍暂停，独立试用者尚未完成验收。

主API/worker已更新，[原17业务表1413行和主Cookie/身份/回执保持](main-continuity.json)，[运行代码与健康检查](deployment.json)通过，迁移仍0013。私有备份`output/d7-preview-final/`。本地试用包另行从提交后的源码生成，旧包不覆盖。
