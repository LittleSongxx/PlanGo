# 小悠 · YOYU

YOYU 是独立运行的 Electron 本地生活 Agent：聊天、真实浏览器、行程画布、地图、菜单、团购、分享和提醒，由内置 Python Harness 管理模型调用、证据、审批、检查点与恢复。

运行和构建只使用本仓库源码、配置及依赖锁，不需要外部 Planora 仓库或服务。宿主机与容器内的 Python 均使用名为 `planora` 的 conda 环境；环境名称不代表连接原 Planora 项目。

## 安装与启动

需要 Node.js 20.11+、conda、uv、Docker Engine 与 Compose，以及能运行 Electron 的桌面环境。先安装依赖并初始化本项目配置：

Linux/WSL 图形桌面可直接使用一键脚本，脚本会自动定位本仓库：

```bash
./start.sh  # 准备依赖和 planora 环境，等待 YOYU Docker 就绪，后台启动桌面
./stop.sh   # 停止本仓库桌面和 YOYU 容器，保留数据库等数据卷
```

重复启动会复用已运行的本仓库桌面；更改 `.env` 后先停止再启动。脚本校验进程归属、PID 启动时间和容器目录标签，不按通用进程名停止其他项目。启动日志在 `output/lifecycle/setup.log` 和 `output/lifecycle/desktop.log`；并发启停会被拒绝。纯服务器没有图形显示时，请仅运行下方 Docker 服务命令。

也可手动分步操作：

```bash
npm ci
npm run setup:backend
```

安装脚本创建或复用 Python 3.12 的 `planora` 环境，按本仓库 `uv.lock` 安装运行依赖，保留环境中的其他包。它保留已有 `.env`，不存在时从模板创建；自动生成缺失的后端 token、数据库密码，并写入 `YOYU_PYTHON`。`.env` 被 Git 忽略，权限设为 0600，不要再用模板覆盖它。

在本项目 `.env` 填写模型与高德配置。默认模板使用 `YOYU_BACKEND_AUTOSTART=false` 连接 Docker 后端；已有配置不会被自动改成这个模式。

```dotenv
OPENAI_API_KEY=填写模型服务Key
OPENAI_BASE_URL=填写兼容接口地址
OPENAI_MODEL=填写模型名
AMAP_WEBSERVICE_KEY=填写高德Web服务Key
AMAP_JS_KEY=填写高德JavaScript Key
AMAP_JS_SECURITY=填写高德JavaScript安全码
```

高德 JS 配置获取：登录[高德控制台](https://console.amap.com/)，在「应用管理 → 我的应用」创建或选择应用，再添加服务平台为 **Web端（JS API）** 的 Key。把该 Key 填入 `AMAP_JS_KEY`，对应安全密钥 `securityJsCode` 填入 `AMAP_JS_SECURITY`；它们和 Web 服务 Key 是不同的平台凭证。参见[官方申请步骤](https://lbs.amap.com/api/javascript-api-v2/prerequisites)。个人认证开发者可用于个人研究学习；获取这两个值不要求先升级企业认证。商业用途的技术服务许可和配额应另按[官方规则](https://lbs.amap.com/faq/advisory/authorization/43168)确认。

「上海·附近发现」使用 `AMAP_WEBSERVICE_KEY` 调用高德 `/v5/place/text`，按城市与关键词搜索，并非 mock。目前没有传入当前位置或搜索半径，因此实际上是同城发现；同一桌面进程还会缓存相同查询，刷新不保证重新请求高德。该功能不依赖 JS Key，也不代表已核验商家的实时营业、库存或预约能力。

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
| `LLM_PROVIDER` / `LONGCAT_*` / `MINIMAX_*` | 桌面本地模式的兼容配置；Docker 统一填写 `OPENAI_*` |
| `AMAP_WEBSERVICE_KEY` / `AMAP_JS_KEY` / `AMAP_JS_SECURITY` | 地点、路线、天气、地图与定位 |
| `YOYU_BACKEND_URL` | 桌面连接地址，默认 `http://127.0.0.1:8011` |
| `YOYU_BACKEND_AUTOSTART` | 模板为 `false`；设 `true` 可在无服务时启动本地后端 |
| `YOYU_BACKEND_TOKEN` | 桌面与后端共享的认证 token，由安装脚本补全 |
| `YOYU_POSTGRES_PASSWORD` | YOYU Docker 数据库密码，由安装脚本补全 |
| `YOYU_SERVICE_PORT` | Docker 宿主机端口，默认 8011；改动后同步 `YOYU_BACKEND_URL` |
| `YOYU_PYTHON` | `planora` 环境解释器绝对路径，由安装脚本写入 |
| `YOYU_DATA_DIR` | 本地模式数据目录；桌面默认 `app userData/harness`，容器为 `/data` |
| `YOYU_RUNTIME_PROFILE` | 本地为 `desktop`；Compose 固定为 `service` |

不使用 Docker 时，可以让 Electron 启动 SQLite 后端与本地消费者。使用空闲端口，避免连接到仍在运行的 Docker API：

```bash
YOYU_BACKEND_AUTOSTART=true YOYU_RUNTIME_PROFILE=desktop YOYU_BACKEND_URL=http://127.0.0.1:8012 npm run dev
```

两种模式都使用真实浏览器 Provider。它们的数据库独立，切换模式不会自动迁移历史。修改桌面模型配置会重启由桌面启动的后端；不会停止另外启动的服务。

手动启动本地后端可用 `npm run backend`，它读取本项目 `.env` 并使用 conda `planora`；默认监听 8011，需先释放端口。桌面连接时设置 `YOYU_BACKEND_AUTOSTART=false` 并使用相同 token。

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

安装测试工具后运行常规检查：

```bash
npm run setup:backend -- --dev
npm run check
```

包含类型检查、界面投影、模型连接错误处理、命令与回执恢复、分享持久化、Python 回归及构建；使用隔离测试数据，不调用真实模型或交易接口。本次清理与修复后的 `npm run check` 已通过，Python 回归为 47 个测试函数，另有 35 个 subtests；Python 类型检查覆盖 63 个文件并通过。

Linux 无桌面显示时可用 Xvfb 运行真实 Chromium/Electron 检查：

```bash
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:browser
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:desktop
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:full-stack
npm run test:independent
python3 scripts/check_lifecycle.py
```

- `browser`：真实页面操作、权限、快照、取消、重复命令和轮次隔离。
- `desktop`：真实界面、预加载与 IPC，使用明确的离线协议后端样本。
- `full-stack`：真实 YOYU Python 后端与 Electron 本地网页样本，验证菜单、持久命令和后端重启恢复，不调用真实模型。
- `independent`：复制公开源码与锁文件到临时目录，使用调用方的 `planora` 环境启动，验证源码、配置和数据不依赖外部仓库；从锁文件安装全新环境由 Docker 构建验证。
- `check_lifecycle.py`：在临时目录使用替身 Docker/npm 与真实测试子进程验证启停归属、重复执行和异常清理，不停止正在运行的 YOYU 或其他服务。

Docker 构建、迁移和健康检查已实测通过。已部署 API 的 23 项检查通过，覆盖认证、Skill、记忆和提醒及 PostgreSQL 提交结果，见 [部署 API 检查](eval/deployment_api_checks.json)。可复现：

```bash
conda run --no-capture-output -n planora python scripts/check_deployment.py
```

[本次交付检查](eval/project_delivery_checks.json) 记录了运行中的服务及解释器，并确认容器内 73 个源码/Skill 文件与工作区一致、未打包 `.env`。正常桌面启动也已验证，浏览器沙箱保持启用。

该脚本检查默认 8011 服务，创建并清理独立验收用户和提醒，不调用模型。需要运行服务中的 Electron 验收时：

```bash
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:deployed
```

`test:deployed` 使用 `.env` 中的已部署后端和实际配置模型，会产生模型调用、创建验收任务，并重启 YOYU 的 API/worker 检查恢复；请在没有其他进行中任务时执行。页面仍是本地受控菜单样本。

包含新 Skill 的镜像已通过 [部署桌面检查](eval/deployed_desktop_checks.json)：实际模型调用、真实 Electron 菜单读取、价格 128 元与未知价保留、API/worker 重启和历史界面恢复均通过，见 [实测桌面截图](docs/assets/deployed-desktop.png)。验收中修复了完成后未展示结果、同页重复卡片，以及模型漏提取覆盖 DOM 表格价格的问题。

容器内高德 Web 地理编码实测通过，见 [高德检查](eval/deployed_amap_check.json)。当前仍缺 JavaScript Key 与安全码，**地图 JavaScript 渲染尚未验收**；该地理编码结果也不等于路线、天气或真实商家流程已全部验证。

测试中的 `--no-sandbox` 仅用于隔离 Linux 测试；应用正常启动没有禁用浏览器沙箱。以上结果不代表任意商家站点或真实交易均已验收。历史模型两批试测各为 3/4 场景达标，失败记录和具体限制见 [融合实现与质量验证](docs/融合实现与质量验证.md)。

## 服务维护

```bash
docker compose ps
npm run services:down
```

`services:down` 停止服务并保留数据卷。Compose 使用 YOYU 专属项目名与数据卷，API 默认只绑定本机；PostgreSQL/Redis 不暴露宿主机端口。首次及后续迁移由 `migrate` 服务执行，消费者为本项目 `yoyu.worker`。

容器基于 Miniforge，创建 `planora` conda 环境并安装本仓库锁定依赖；uv 用于导出锁和安装到该环境，不创建项目 `.venv`。旧 `.venv` 与缓存已清理，`node_modules` 和 `out` 保留用于当前桌面启动。

## 模块与上游维护

```text
src/renderer/            产品界面与展示投影
src/main/harness*.ts     后端启动、认证、任务与回执传输
src/main/browser-bridge.ts
src/shared/browser.ts   浏览器命令契约与执行边界
backend/yoyu/            浏览器、业务、审批、记忆和提醒扩展
vendor/planora/          固定 Harness 源码与上游基线
pyproject.toml / uv.lock 本项目 Python 依赖声明与锁
```

本次清理移除了不可达的旧 TypeScript Agent/规划器、模拟供给与交易链、旧 CLI/eval 入口，以及过时设计 PDF/LaTeX 和专用截图。10 个场景 Skill 保留需求要点，执行指导已同步到当前操作与审批。历史 `eval/*.json` 保留作证据，不能作为现行运行说明。当前 [设计文档](docs/设计文档_小悠.md)、[Demo](docs/Demo脚本_3分钟.md) 与 [导师咨询提纲](docs/导师咨询_30问.md) 已同步到实现边界。

Planora 基线来自 v7 公开冻结归档，包含当时未提交的公开实现。来源和逐文件哈希见 [SNAPSHOT.json](vendor/planora/SNAPSHOT.json)，完整基线在 `vendor/planora/upstream-base.tar.gz`。日常构建与运行都不读取外部仓库；维护时可显式检查上游：

```bash
npm run upstream:check -- --source /path/to/Planora
```

该命令只读报告上游变化、本地补丁和需要三方合并的文件，不会覆盖任何仓库。
