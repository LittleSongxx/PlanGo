# PlanGo

PlanGo 是独立运行的 Electron 本地生活 Agent：聊天、真实浏览器、行程画布、地图、菜单、团购、分享和提醒，由内置 Python Harness 管理模型调用、证据、审批、检查点与恢复。

运行和构建只使用本仓库源码、配置及依赖锁，不需要外部 Planora 仓库或服务。宿主机与容器内的 Python 均使用名为 `plango` 的 conda 环境；原 `planora` 环境保留，不作更名或修改。

R0→P3 本轮核心实施已完成，实际验收见 [实施进度](docs/实施进度.md)末尾。新对话从 [当前交接](docs/CODEX_HANDOFF.md) 和 [接手提示词](docs/CODEX_接手提示词.md) 继续。历史评测保留原名与原结论；当前命令使用 PlanGo。未迁移的旧安装先参照 [迁移说明](docs/PlanGo迁移.md)，不要直接启动新空数据卷。

默认窗口为1800×1120，并限制在屏幕工作区内；门店资料按已读/待核对范围分层展示，原始网页与证据可展开查看。

功能暂不可用时，界面会说明当前情况和下一步，不直接展示内部异常。优惠读取失败可重新打开来源页核对；消息显示“送达待核实”或“已接收待取回”时，使用“核对送达并取回”继续原请求。不要用新建任务代替核对。当前修复及桌面截图见[提示说明](eval/plango-friendly-errors/README.md)。

作品集质量材料见[30例受控评测](eval/quality-v2-30/README.md)：TSR与Groundedness同时报告，Token另记，明确AI独立上下文审核及受控资料范围，不表示真实预约成功率或全网泛化能力。

## 安装与启动

Linux/WSLg 试用包的独立安装、非开发启动、配置诊断、升级与冷备份恢复见 [试用安装](docs/试用安装.md)。下面是源码开发环境的启动方式；试用包不需要 Node/npm、Vite 或主机 conda。

需要 Node.js 20.11+、conda、uv、Docker Engine 与 Compose，以及能运行 Electron 的桌面环境。先安装依赖并初始化本项目配置：

Linux/WSL 图形桌面可直接使用一键脚本，脚本会自动定位本仓库：

```bash
./start.sh  # 准备依赖和 plango 环境，等待 PlanGo Docker 就绪，后台启动桌面
./stop.sh   # 停止本仓库桌面和 PlanGo 容器，保留数据库等数据卷
```

重复启动会复用已运行的本仓库桌面；更改 `.env` 后先停止再启动。脚本校验进程归属、PID 启动时间和容器目录标签，不按通用进程名停止其他项目。启动日志在 `output/lifecycle/setup.log` 和 `output/lifecycle/desktop.log`；并发启停会被拒绝。纯服务器没有图形显示时，请仅运行下方 Docker 服务命令。

也可手动分步操作：

```bash
npm ci
npm run setup:backend
```

安装脚本创建或复用 Python 3.12 的 `plango` 环境，按本仓库 `uv.lock` 安装运行依赖，环境独立于兄弟项目。它保留已有 `.env`，不存在时从模板创建；自动生成缺失的后端 token、数据库密码，并写入 `PLANGO_PYTHON`。`.env` 被 Git 忽略，权限设为 0600，不要再用模板覆盖它。

在本项目 `.env` 填写模型与高德配置。默认模板使用 `PLANGO_BACKEND_AUTOSTART=false` 连接 Docker 后端；已有配置不会被自动改成这个模式。

```dotenv
OPENAI_API_KEY=填写模型服务Key
OPENAI_BASE_URL=填写兼容接口地址
OPENAI_MODEL=填写模型名
AMAP_WEBSERVICE_KEY=填写高德Web服务Key
AMAP_JS_KEY=填写高德JavaScript Key
AMAP_JS_SECURITY=填写高德JavaScript安全码
```

高德 JS 配置获取：登录[高德控制台](https://console.amap.com/)，在「应用管理 → 我的应用」创建或选择应用，再添加服务平台为 **Web端（JS API）** 的 Key。把该 Key 填入 `AMAP_JS_KEY`，对应安全密钥 `securityJsCode` 填入 `AMAP_JS_SECURITY`；它们和 Web 服务 Key 是不同的平台凭证。参见[官方申请步骤](https://lbs.amap.com/api/javascript-api-v2/prerequisites)。个人认证开发者可用于个人研究学习；获取这两个值不要求先升级企业认证。商业用途的技术服务许可和配额应另按[官方规则](https://lbs.amap.com/faq/advisory/authorization/43168)确认。

「附近发现」统一请求本项目后端高德服务。明确地址/设备坐标使用 `/v5/place/around` 和 5 公里半径；城市、区级或 IP 参考位置使用 `/v5/place/text` 并标为同城发现。界面显示来源时间，缓存保留原时间，刷新明确绕过缓存。选店时后端按 POI ID 重新核对详情，保留用户起点。这不依赖 JS Key，也不证明实时库存、排队或可预约。

本轮真实界面验收见 [实施进度](docs/实施进度.md) 与 `eval/plango-live-ui/`；旧 `discovery_source_check.json` 仅保留当时版本的来源证据。

然后启动服务与桌面：

```bash
npm run services:up
npm run dev
```

默认后端地址为 `http://127.0.0.1:8011`。Docker 中包含 PostgreSQL、Redis、迁移、API 和 worker；浏览器由桌面 Electron 提供，API 容器本身没有浏览器。修改 Docker 模型或高德 Web 配置后，重新执行 `npm run services:up` 使配置生效；桌面设置不会改写已部署后端的环境。

- 模型未配置时可观测页面、确定性解析受支持的菜单表格；规划和复杂抽取需要可用模型。图片导入还需要模型支持图像输入。
- 地点、路线和天气需要高德 Web Key；地图展示需要 JavaScript Key 与安全码。浏览器读取当前页面不依赖高德。
- 在应用内浏览器完成需要的账号登录。任务暂停时处理登录或验证码，再继续；手动接管会取消尚未执行的自动操作。

最小演示：打开可读取的菜单页面，发送“读取当前页面的真实菜单”，核对名称、价格、未知项和来源。完整讲解见 [3 分钟 Demo](docs/Demo脚本_3分钟.md)。

发送中断时，输入区会保留文字、图片和已选门店，并显示“未送达”“送达待核实”或“已接收·待取回任务”。点击“核对送达并取回”只查询原请求；需要重试时使用“继续发送原请求”或“按原请求重试”。不要复制成新任务来恢复同一次发送。只有确定未送达的请求可以取消并返回草稿；已经接收的请求只取回原任务。重启后先核对送达，保留同一任务及累计预算。

“设置”或“连接与能力”中的“执行服务与能力”显示连接的服务、服务配置模型、最近任务记录的模型及能力范围。“测试服务模型”发起一次有界文本诊断；“测试桌面模型”使用桌面配置，两者不同。独立 worker 的当前配置尚未单独核对时会明确注明，历史调用或健康状态不能证明现在的模型和工具可用。新版可恢复发送需要支持该协议的后端；连接旧服务时会保留草稿并提示升级。

读取同店优惠后，可先在比较卡修改人数、日期和预算，查看“不适用”原因或缺失规则。面值、售价和原价分别显示；98元双人餐不代表足够3人，代金券标价不代表全部餐费。选择“用于本次行程”后，核对网页与高德候选分店的名称、地址，再明确确认；原任务会保留选中的优惠来源，规则未齐或来源过期仍提示待核对，不自动抵扣优惠。

聊天按接受顺序保留每轮输入和已记录的回复，关闭再打开也能回看；旧任务仅恢复真实保存过的澄清内容，不补写缺失回答。主界面采用明黄色、暖白底和深色文字，向上阅读时保持滚动位置。

进入行程后，在需求卡修改条件会重新规划，保存后可从历史继续同一任务。搜索半径围绕搜索中心，单段路程上限核对实际路线，两者独立。公交仅覆盖能核实两端城市并完整解析的同城公交/步行组合；缺线路时路线未知，缺票价时费用未知。预算显示餐饮、活动和去程交通的已知估算小计；返程、额外消费、驾驶油费和停车费等仍需核对。操作截图、真实只读结果与尚未覆盖范围见 [产品接续证据](eval/plango-product-next/README.md)。

也可以直接发送“人数改为3人，其他不变”或“日期改成2026年9月12日，取消单段路程上限”。修改保留同一任务和未提及条件；角色人数与总人数分开，日期不会作为目标地区查询。

打开包含明确报价、规则或路线的资料页后，可以发送“只按这份条款判断适用性并给出总价”或“根据给定路线能确认不超预算吗”。当前支持来源唯一、计价单位明确的套餐条件核算，以及已知完整餐费加单程标准票价的预算分析。成人资格、额外费用、日期/时段等必须按原文核对；多套餐歧义、缺票价或缺规则会保留未知，不自动购买或预约。实现与失败保留记录见[评测驱动修复](eval/plango-quality-fixes/README.md)。

悦廊的网页预约条件可通过官方预填链接核对，当前仅支持参数预览：购物车、空位查询与预约接口保持阻断，不能据此判断有位。结果会保留实际网页标签和核对时间，关闭后可继续原任务；见[本次验证](eval/plango-booking-preview/README.md)。

## 配置与运行方式

| 配置 | 作用 |
|---|---|
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` / `OPENAI_MODEL` | Docker 与后端使用的 OpenAI 兼容模型配置 |
| `PLANGO_LLM_PROVIDER` / `LONGCAT_*` / `MINIMAX_*` | 桌面本地模式的兼容配置；Docker 统一填写 `OPENAI_*` |
| `AMAP_WEBSERVICE_KEY` / `AMAP_JS_KEY` / `AMAP_JS_SECURITY` | 地点、路线、天气、地图与定位 |
| `PLANGO_BACKEND_URL` | 桌面连接地址，默认 `http://127.0.0.1:8011` |
| `PLANGO_BACKEND_AUTOSTART` | 模板为 `false`；设 `true` 可在无服务时启动本地后端 |
| `PLANGO_BACKEND_TOKEN` | 桌面与后端共享的认证 token，由安装脚本补全 |
| `PLANGO_POSTGRES_PASSWORD` | PlanGo Docker 数据库密码，由安装脚本补全 |
| `PLANGO_SERVICE_PORT` | Docker 宿主机端口，默认 8011；改动后同步 `PLANGO_BACKEND_URL` |
| `PLANGO_PYTHON` | `plango` 环境解释器绝对路径，由安装脚本写入 |
| `PLANGO_DATA_DIR` | 本地模式数据目录；桌面默认 `app userData/harness`，容器为 `/data` |
| `PLANGO_RUNTIME_PROFILE` | 本地为 `desktop`；Compose 固定为 `service` |
| `PLANGO_AGENT_MODE` | `multi`/`single` 视角策略，默认 multi；不改变审批权限 |
| `PLANGO_MAX_MODEL_TOKENS` / `PLANGO_MAX_TOOL_CALLS` / `PLANGO_MAX_RUN_SECONDS` | 每次明确用户输入的有界预算，默认 12000 / 48 / 300；累计用量保留，人工等待另存剩余时间 |
| `PLANGO_EMBEDDING_API_KEY` / `PLANGO_EMBEDDING_BASE_URL` / `PLANGO_EMBEDDING_MODEL` | 可选独立向量接口；不配置独立 Key 时保持关闭，不借用聊天 Key |
| `PLANGO_BROWSER_VISION_ENABLED` | 显式启用按需只读截图理解，示例默认 false；需已验证模型读图能力 |

不使用 Docker 时，可以让 Electron 启动 SQLite 后端与本地消费者。使用空闲端口，避免连接到仍在运行的 Docker API：

```bash
PLANGO_BACKEND_AUTOSTART=true PLANGO_RUNTIME_PROFILE=desktop PLANGO_BACKEND_URL=http://127.0.0.1:8012 npm run dev
```

两种模式都使用真实浏览器 Provider。它们的数据库独立，切换模式不会自动迁移历史。修改桌面模型配置会重启由桌面启动的后端；不会停止另外启动的服务。

手动启动本地后端可用 `npm run backend`，它读取本项目 `.env` 并使用 conda `plango`；默认监听 8011，需先释放端口。桌面连接时设置 `PLANGO_BACKEND_AUTOSTART=false` 并使用相同 token。

## 功能与真实边界

- 行程经过需求、候选、编译与验证；数量以有效结果为准，选方案需提交确切 ID 和版本。
- 成果页的“行程需求”卡可直接修改起点、搜索中心/范围、日期/时间、人数、预算及交通方式，并锁定/解锁当前方案的一站。保存走同一任务的稀疏规范补丁；空预算即取消，旧方案审批失效，原记录保留。范围同时约束搜索与路线，直接编辑上限50公里；锁定不冻结营业/价格证据。
- 登录或验证码页会暂停到人工接管；已实测的大众点评扫码路由也在模型读取前暂停。首站已由用户完成登录，真实门店地址/推荐菜/优惠预览有只读证据；完整菜单、使用细则和真实表单仍未覆盖。登录页本身不算商家读取成功。
- 浏览器实际导航、读取、滚动、输入和点击。审批绑定任务轮次、页面、快照与参数；改需求或页面后需重新核对。
- 网页、菜单和优惠标记来源；缺价保持未知，套餐总价不当人均，邻店报价不归给目标店。复杂饮食等未完整验证的条件可保持部分完成。
- 页面步骤完成后另行读取结果；通用“成功”文案不能证明商家、人数、金额和外部履约都正确。未确认结果保持 `UNKNOWN`，不会自动重复下单；用户核实后的说明与编号始终标为 `user` 来源。
- 分享固定方案版本，投票与意见在桌面本地持久化；手机需能访问电脑的局域网分享地址，电脑需保持运行。
- 明确偏好、收藏、任务经历和显式反馈保存在运行服务，可查看与删除；提醒支持恢复后补发。Skills 是本地任务指导，启用列表在新任务创建时固定，开关跨桌面重启保存。首启不生成虚构偏好、足迹或随机关怀。
- 微信渠道、具体网站交易适配、账号流程、支付和外部对账仍需单独接入与验收；正常运行不会用模拟二维码、排队号或订单成功补齐流程。

Cookie 留在 Electron 持久会话分区，不直接发送给模型或后端。使用远程模型时，对话、相关记忆、网页片段和提交的截图可能发送到所配置的模型服务；高德也会接收查询与位置信息。默认本机 Docker 数据在本机数据卷，部署到其他机器时后端任务和记忆随部署端存储。分享链接会向持有链接且网络可达的人提供选定方案，不能概括为“所有数据绝不出电脑”。

## 检查与验收

本轮证据与未完成项见 [实施进度](docs/实施进度.md)。旧报告保留原结论与失败分母，不作为现行版本的通过凭据。

接续的结构化卡、真实扫码暂停及安装验证见 [N1→N4 记录](eval/plango-next/README.md)；小型归档回归使用 `python scripts/inspect_trace.py --check`，不会自动联网或写库。

```bash
npm run setup:backend -- --dev
npm run check
python3 scripts/check_lifecycle.py
python3 scripts/migrate_config.py
conda run --no-capture-output -n plango python backend/plango/migrations/check.py
npm run test:independent
```

`check` 包含类型、状态/定位/迁移、传输、模型错误处理、分享、Python 回归及构建；这些回归使用隔离测试数据。真实 Chromium/Electron 受控页面另行检查：

```bash
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:browser
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:browser-view
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:desktop
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:full-stack
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:map-title
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:desktop-storage-browser
```

浏览器已采用主进程 WebContentsView、经实测的 Playwright/CDP 与原生截图。renderer 只发送固定用户意图与布局，不持有任意脚本或 CDP 能力。用户、模型与执行器操作同一浏览会话；原生弹窗也保持该会话。兼容性边界和失败记录见 [浏览器适配验证](docs/浏览器适配验证.md)。截图理解每轮最多一次，须显式启用 `PLANGO_BROWSER_VISION_ENABLED`，目前仅用于只读理解/核验，不能代替提交审批或证明业务完成。

部署 API 与实际模型检查须在本项目无进行中操作时运行：

```bash
conda run --no-capture-output -n plango python scripts/check_deployment.py
PLANGO_TEST_BACKEND_URL=http://127.0.0.1:18011 PLANGO_TEST_COMPOSE_PROJECT=plango-e2e env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:deployed
PLANGO_TEST_BACKEND_URL=http://127.0.0.1:18011 PLANGO_TEST_COMPOSE_PROJECT=plango-e2e env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:deployed -- --vision
```

`test:deployed` 必须连接独立 `plango-e2e` 服务（先用 `PLANGO_SERVICE_PORT=18011 docker compose -p plango-e2e up --build -d --wait` 启动），脚本核对容器归属和端口，拒绝主用户服务。它调用真实模型并仅重启测试 API/worker，页面分别为受控菜单和 Canvas，保留任务与恢复证据；不进行真实商家交易。`scripts/check_vision_capability.py` 是单独的有界真实图像能力检查，输入为仓库中的受控浏览器截图。各阶段已审查证据保存在 `eval/plango-r0/`、`eval/plango-p0/`、`eval/plango-p1/` 和 `eval/plango-live-*/`；今后的完整链路脚本默认写入带时间目录的 `output/full-stack-smoke/`，不覆盖历史。

测试中的 `--no-sandbox` 仅用于隔离 Linux 测试；正常应用保留浏览器沙箱。真实商家履约、任意网站表单和支付均不能由这些受控检查推断。

## 服务维护

```bash
docker compose ps
npm run services:down
```

`services:down` 停止服务并保留数据卷。Compose 使用 PlanGo 专属项目名与数据卷，API 默认只绑定本机；PostgreSQL/Redis 不暴露宿主机端口。首次及后续迁移由 `migrate` 服务执行，消费者为本项目 `plango.worker`。

容器基于 Miniforge，创建 `plango` conda 环境并安装本仓库锁定依赖；uv 用于导出锁和安装到该环境，不创建项目 `.venv`。旧 `.venv` 与缓存已清理，`node_modules` 和 `out` 保留用于当前桌面启动。

## 模块与上游维护

当前实现见 [Agent架构与选型](docs/Agent架构与选型.md)、[架构图](figures/plango-agent-architecture.md) 与 [任务闭环图](figures/plango-task-lifecycle.md)。外层是集中式 Plan-and-Execute 工作流，页面内是受控 ReAct 循环；确定性协调器、LLM专业节点和领域服务职责分开。single/multi 表示是否启用额外视角，并非两套运行架构。产品当前不集成 MCP，Skills 不授予工具权限。

固定决策见 [架构决策](docs/架构决策.md)。[迁移前审计](docs/模块替换与Agent工作流审计.md)、[旧工作流](figures/yoyu-current-workflow.md) 和 [历史目标建议](figures/yoyu-target-workflow.md) 保留作历史证据，不代表当前缺口或已实现功能。

```text
src/renderer/            产品界面与展示投影
src/main/harness*.ts     后端启动、认证、任务与回执传输
src/main/browser-bridge.ts
src/shared/browser.ts   浏览器命令契约与执行边界
backend/plango/            浏览器、业务、审批、记忆和提醒扩展
vendor/plango_harness/          固定 Harness 源码与上游基线
pyproject.toml / uv.lock 本项目 Python 依赖声明与锁
```

本次清理移除了不可达的旧 TypeScript Agent/规划器、模拟供给与交易链、旧 CLI/eval 入口，以及过时设计 PDF/LaTeX 和专用截图。10 个场景 Skill 保留需求要点，执行指导已同步到当前操作与审批。历史 `eval/*.json` 保留作证据，不能作为现行运行说明。当前 [设计文档](docs/设计文档_PlanGo.md)、[Demo](docs/Demo脚本_3分钟.md) 与 [导师咨询提纲](docs/导师咨询_30问.md) 已同步到实现边界。

Planora 基线来自 v7 公开冻结归档，包含当时未提交的公开实现。来源和逐文件哈希见 [SNAPSHOT.json](vendor/plango_harness/SNAPSHOT.json)，完整基线在 `vendor/plango_harness/upstream-base.tar.gz`。日常构建与运行都不读取外部仓库；维护时可显式检查上游：

```bash
npm run upstream:check -- --source /path/to/Planora
```

该命令只读报告上游变化、本地补丁和需要三方合并的文件，不会覆盖任何仓库。
