# PlanGo

PlanGo 是独立运行的 Electron 本地生活 Agent：聊天、真实浏览器、行程画布、地图、菜单、团购、分享和提醒，由内置 Python Harness 管理模型调用、证据、审批、检查点与恢复。

运行和构建只使用本仓库源码、配置及依赖锁，不需要外部 Planora 仓库或服务。宿主机与容器内的 Python 均使用名为 `plango` 的 conda 环境；原 `planora` 环境保留，不作更名或修改。

本轮更名与后续 P0→P3 的实际进度见 [实施进度](docs/实施进度.md)。历史评测保留原名与原结论；当前命令使用 PlanGo。已有安装先参照 [迁移说明](docs/PlanGo迁移.md)，不要直接启动新空数据卷。

## 安装与启动

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

「重庆·附近发现」使用 `AMAP_WEBSERVICE_KEY` 调用高德 `/v5/place/text`，按城市与关键词搜索，并非 mock。目前没有传入当前位置或搜索半径，因此实际上是同城发现；同一桌面进程还会缓存相同查询，刷新不保证重新请求高德。该功能不依赖 JS Key，也不代表已核验商家的实时营业、库存或预约能力。

数据来源的实际调用核验见 [discovery_source_check.json](eval/discovery_source_check.json)，报告保留去掉 Key 的请求信息与少量公开 POI 样本。

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

不使用 Docker 时，可以让 Electron 启动 SQLite 后端与本地消费者。使用空闲端口，避免连接到仍在运行的 Docker API：

```bash
PLANGO_BACKEND_AUTOSTART=true PLANGO_RUNTIME_PROFILE=desktop PLANGO_BACKEND_URL=http://127.0.0.1:8012 npm run dev
```

两种模式都使用真实浏览器 Provider。它们的数据库独立，切换模式不会自动迁移历史。修改桌面模型配置会重启由桌面启动的后端；不会停止另外启动的服务。

手动启动本地后端可用 `npm run backend`，它读取本项目 `.env` 并使用 conda `plango`；默认监听 8011，需先释放端口。桌面连接时设置 `PLANGO_BACKEND_AUTOSTART=false` 并使用相同 token。

## 功能与真实边界

- 行程经过需求、候选、编译与验证；数量以有效结果为准，选方案需提交确切 ID 和版本。
- 浏览器实际导航、读取、滚动、输入和点击。审批绑定任务轮次、页面、快照与参数；改需求或页面后需重新核对。
- 网页、菜单和优惠标记来源；缺价保持未知，套餐总价不当人均，邻店报价不归给目标店。复杂饮食等未完整验证的条件可保持部分完成。
- 页面步骤完成后另行读取结果；通用“成功”文案不能证明商家、人数、金额和外部履约都正确。未确认结果保持 `UNKNOWN`，不会自动重复下单；用户核实后的说明与编号始终标为 `user` 来源。
- 分享固定方案版本，投票与意见在桌面本地持久化；手机需能访问电脑的局域网分享地址，电脑需保持运行。
- 记忆、收藏、Skill 与显式提醒经统一后端处理；提醒支持恢复后补发。首启不生成虚构偏好、足迹或随机关怀。
- 微信渠道、具体网站交易适配、账号流程、支付和外部对账仍需单独接入与验收；正常运行不会用模拟二维码、排队号或订单成功补齐流程。

Cookie 留在 Electron 持久会话分区，不直接发送给模型或后端。使用远程模型时，对话、相关记忆、网页片段和提交的截图可能发送到所配置的模型服务；高德也会接收查询与位置信息。默认本机 Docker 数据在本机数据卷，部署到其他机器时后端任务和记忆随部署端存储。分享链接会向持有链接且网络可达的人提供选定方案，不能概括为“所有数据绝不出电脑”。

## 检查与验收

本轮证据与未完成项见 [实施进度](docs/实施进度.md)。旧报告保留原结论与失败分母，不作为现行版本的通过凭据。

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
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:deployed
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:deployed -- --vision
```

`test:deployed` 调用已配置真实模型并重启本项目 API/worker，页面分别为受控菜单和 Canvas，保留任务与恢复证据；不进行真实商家交易。`scripts/check_vision_capability.py` 是单独的有界真实图像能力检查，输入为仓库中的受控浏览器截图。各阶段新证据写入 `eval/plango-r0/`、`eval/plango-p0/`、`eval/plango-p1/`，不覆盖历史。

测试中的 `--no-sandbox` 仅用于隔离 Linux 测试；正常应用保留浏览器沙箱。真实商家履约、任意网站表单和支付均不能由这些受控检查推断。

## 服务维护

```bash
docker compose ps
npm run services:down
```

`services:down` 停止服务并保留数据卷。Compose 使用 PlanGo 专属项目名与数据卷，API 默认只绑定本机；PostgreSQL/Redis 不暴露宿主机端口。首次及后续迁移由 `migrate` 服务执行，消费者为本项目 `plango.worker`。

容器基于 Miniforge，创建 `plango` conda 环境并安装本仓库锁定依赖；uv 用于导出锁和安装到该环境，不创建项目 `.venv`。旧 `.venv` 与缓存已清理，`node_modules` 和 `out` 保留用于当前桌面启动。

## 模块与上游维护

已确认路线见 [架构决策](docs/架构决策.md)；当前角色、流程断点与成熟实现替换机会见 [模块替换与 Agent 工作流审计](docs/模块替换与Agent工作流审计.md)。[迁移前工作流记录](figures/yoyu-current-workflow.md) 与[目标工作流建议](figures/yoyu-target-workflow.md) 分开记录，建议不代表已实现。

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
