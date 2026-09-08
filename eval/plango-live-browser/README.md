# 真实浏览器与图片用户流程验收

本轮用真实 Electron + WebContentsView、独立 SQLite 后端与本项目既有真实模型配置，未使用商家 mock，也未运行质量指标测评。隔离数据保存在 `output/live-browser-e2e/session-7sbvyC/`。本轮独占随机端口 55877；验收结束已关闭自己的后端和 Electron，未操作主用户 profile、主控 18011 服务或兄弟项目。

## 通过的用户路径

- **真实外部网页读取**：通过地址栏打开 [重庆市政府三峡博物馆介绍](https://www.cq.gov.cn/zjcq/cycq/jplyxl/dsy/dsjp/202608/t20260826_15982331.html)，用输入框提交读取要求。修后 run `6903a5625c244b82983989f5f268635e` 为 `SUCCEEDED`，保留 `source=browser`、真实 URL、观测时间及开放时间/地址原文，结果范围是 `read_only`，`business_completed=false`。界面显示“资料读取已完成”。证据：`external-read-success.json`、`05-external-read-success.png`。
- **用户文件选择上传图片**：将上述真实网页中开放时间和地址的实际截图保存为 `official-visit-guide-upload.png`，先输入读取要求，再点击“上传图片”，通过 Playwright `filechooser` 选择该文件。修后 run `7aca5ec4a79a44648a54a8681d3e081e` 为 `SUCCEEDED`，持久成果 `source=user`、`scope=image_text`，不存在来自浏览器的成果。界面显示图片识别完成，并说明图片里的商家信息和价格尚未实时核验。证据：`image-upload-success.json`、`06-image-upload-success.png`、`09-image-final-ui.png`。
- **浏览器普通控制**：真实地址栏导航、后退、前进、重载、放大至 110% 后重置为 100%、新标签、两个标签切换与关闭第二个标签均通过。导航跨两篇政府网页及博物馆官网，未修改页面数据或执行账号操作。
- **停止与人工操作**：从界面停止任务，后端持久状态变为 `CANCELLED`；随后通过地址栏继续人工浏览成功。短暂的“停止任务并接管”浮条未在本次运行中完成单独点击，不能将它记为通过；本轮实际验证的是常驻“停止任务”按钮及停止后的人工浏览操作。
- **错误恢复与布局**：输入 `javascript:` 地址被拒绝，改回实际 HTTPS 页面后恢复正常。UI 已去掉 Electron IPC 错误包装，保留具体原因。1180×740 最小窗口中，浏览器、对话、侧栏入口仍可用，键盘分隔条调整和 Home 重置通过。证据：`07-minimum-window-browser.png`。
- **恢复**：重启独立后端保留原数据库后，5 条任务仍可从历史恢复；图片结果在历史恢复及 renderer 重载后仍保留来源与范围说明。证据：`08-image-history-restored.png`、`actions.json`。

`10-external-native-final.png` 使用独立 Xvfb 中的系统级截图，包含真实原生浏览器视图；普通 host Playwright 截图不包含 WCV，不能用其空白区域判断网页是否显示。

## 本轮发现与修复

1. “不要规划或提交任何操作”被写操作词匹配：主控修复路由判断中的否定条款，同时保留用户原文；以上真实网页原措辞已复验通过。
2. “图片”未进入读取任务，误问出行日期：主控补齐图片/图像/照片识别入口；同一 filechooser 路径和原措辞已复验通过。
3. 图片上传忽略输入框已写要求，强制使用攻略规划文案：UI 改为优先提交用户已输入的要求，默认进行图片读取。规划要求仍可由用户明确输入。
4. 任务进度暴露原始事件名称和阶段码：UI 改为用户能理解的中文进度，已观测及图片提取事件保持完成状态，不改变审批或 UNKNOWN 语义。
5. 重置缩放按钮补充可访问名称，网址错误去掉 IPC 包装。

最初 3 个探路任务加主控授权的 2 个修后复验任务，共 5 个真实模型任务；未继续扩大任务数量。`command-ledger.json` 来自此隔离 SQLite 的实际记录，7 个命令全部为 `extract/observed`，没有 `click`、`type` 或业务写回执。本轮辅助检查为类型检查、桌面正确性回归及构建；完整产品的其他流程由主控单独验收。
