# 小悠 · YOYU

YOYU 是一个独立运行的 Electron 本地生活 Agent：保留聊天、真实浏览器、行程画布、地图、菜单、团购、分享和提醒，由内置的 Python Harness 统一管理模型调用、证据、审批、检查点及恢复。

**不需要 Planora 仓库、Planora 的 Python 环境或正在运行的服务。** 内置 Harness 源码固定在本仓库中，项目使用自己的依赖锁、配置、数据库和浏览器会话。

## 安装与启动

需要 Node.js 20.11+ 和 uv。下面的命令会为 YOYU 创建独立的 Python 3.12 环境：

```bash
npm ci
npm run setup:backend
cp .env.example .env
npm run dev
```

可以在设置里填写 OpenAI 兼容模型的地址、模型名和 Key，或使用本项目的 .env。首次启动自动启动本地后端，默认地址 http://127.0.0.1:8011；认证 token 自动生成并仅保存在本机。

- 地点、真实路线与天气需要高德 Web 服务 Key；浏览器读取当前页面不需要高德。
- 模型未配置时仍可观测页面、确定性解析受支持的菜单表格；模型规划和复杂抽取需要配置模型。
- 图片导入需要配置支持图像输入的模型；无法识别时请求用户补充，不伪造内容。
- 在应用内浏览器登录需要的站点。Cookie 保存在本应用分区，不发送给模型或后端。
- 在任务暂停时完成登录/验证码，再点继续；手动接管会取消尚未执行的自动操作。

最小演示：在内置浏览器打开真实菜单/团购页面，发送“读取当前页面的真实菜单”，查看价格、未知项和来源；配置高德与模型后，继续生成行程或修改需求。

## 运行方式

默认 desktop 模式使用本项目专属 SQLite、持久检查点和本地任务消费者，不需要 PostgreSQL/Redis。它使用真实浏览器 Provider，**不是 Sandbox 数据源**。

| 配置 | 作用 |
|---|---|
| OPENAI_API_KEY / OPENAI_BASE_URL / OPENAI_MODEL | 通用兼容模型配置，需同时提供有效 Key 与模型名 |
| LLM_PROVIDER / LONGCAT_* / MINIMAX_* | 保留已有 LongCat、MiniMax 配置方式 |
| AMAP_WEBSERVICE_KEY / AMAP_JS_KEY / AMAP_JS_SECURITY | 真实地点、路线、地图与定位 |
| YOYU_BACKEND_URL | 后端地址，默认 http://127.0.0.1:8011 |
| YOYU_BACKEND_AUTOSTART | 默认 true；连接独立启动的服务时设 false |
| YOYU_BACKEND_TOKEN | 可选固定认证 token；默认桌面生成，外部启动服务时必填 |
| YOYU_PYTHON | 可选 Python 可执行文件；默认只用 YOYU 的 .venv |
| YOYU_DATA_DIR | 可选数据目录；桌面默认 app userData/harness |
| YOYU_RUNTIME_PROFILE | desktop 或显式 service |

修改模型配置会重启桌面拥有的后端。它会等待当前浏览器命令收尾并保存回执，任务状态不会依赖进程内闭包。应用不会停止你另外启动的服务。

独立后端入口为 `npm run backend`，需要在环境中设置 YOYU_BACKEND_TOKEN；默认桌面启动流程不要求手动执行这个入口。

## 功能与真实边界

- 行程使用内置 Harness 的需求、候选、编译和验证流程。候选数量以实际有效结果为准；选方案必须提交确切 ID 和版本。
- 浏览器导航、读取、滚动、输入和点击实际操作 Electron 页面。页面脚本不能修改隔离执行环境中的审批快照。
- 原始网页文本、菜单和优惠均标记来源；缺失价格保持未知，套餐总价不当作人均。
- 审批绑定任务轮次、页面、快照和参数。改需求或人工改动页面后，旧审批不再有效。
- 点击完成后仍需另行读取结果。通用页面确认文案只作为观测证据，不能证明用户要求的商家、人数、金额或外部履约全部正确；未确认的业务结果保持 UNKNOWN，不能自动重复下单。用户在网站核对后可填写说明与编号明确确认，记录始终标为 user 来源。
- 分享保存服务端方案的固定版本，投票与意见本地持久化；显式创建的提醒支持重启后补发。
- 记忆、收藏与 Skill 通过统一后端处理。首启不生成虚构的偏好、足迹或随机关怀消息。
- 微信真实渠道、具体网站的交易适配与账号验收仍需按平台接入。正常运行不会用模拟二维码、排队号或订单成功补齐流程。

当前自动化验证使用明确标记的本地网页样本，不代表所有美团/点评页面、账号、地区或真实交易均已验收。支付、结算和外部对账不由通用页面文字识别宣称完成。

## 验证

```bash
npm run check
```

包含类型检查、界面状态/投影、命令与回执恢复、分享持久化、Python 后端回归及构建。测试在临时目录运行，不调用真实模型或交易接口。

Linux 无桌面环境可运行真实 Chromium/Electron 测试：

```bash
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:browser
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:desktop
env -u ELECTRON_RUN_AS_NODE xvfb-run -a npm run test:full-stack
```

- browser：页面、权限、快照、取消、重复命令与不同轮次隔离。
- desktop：真实界面、预加载与 IPC；协议后端是明确的离线测试样本。
- full-stack：YOYU 自有 Python 后端 + Electron + 实际本地网页，验证持久命令、菜单和后端重启恢复；不使用协议后端替身。

测试中的 `--no-sandbox` 仅用于隔离的 Linux CI fixture；应用正常启动没有禁用浏览器沙箱。

独立性复核：`npm run test:independent` 会把公开后端与依赖锁复制到临时目录，离线创建新的环境并启动，验证没有原仓库或上游环境依赖。

旧 `npm run eval` 只检查历史离线规划器，不是融合版本的成功率或真实浏览器验收。不要把其结果与真实运行混合。

独立问题基线、优化依据和真实模型试测方法见 [融合实现与质量验证](docs/融合实现与质量验证.md)。真实接口检查会使用本项目 `.env` 并产生少量调用费用；常规 `npm run check` 不调用这些接口。

## 可选 PostgreSQL / Redis 服务模式

Compose 使用 YOYU 专属项目名和数据卷，不挂载其他项目，也不连接 Planora 服务。API 默认仅开放本机 8011，PostgreSQL/Redis 不暴露宿主机端口。

```bash
export YOYU_BACKEND_TOKEN="$(openssl rand -hex 32)"
export YOYU_POSTGRES_PASSWORD="$(openssl rand -hex 24)"
# 按需在环境中设置 OPENAI_* 与 AMAP_WEBSERVICE_KEY

docker compose up --build -d
# 同一 shell 中启动桌面，复用上面的 token：
YOYU_BACKEND_AUTOSTART=false YOYU_BACKEND_URL=http://127.0.0.1:8011 npm run dev
```

停止服务使用 `docker compose down`，默认保留数据卷。首次迁移由 Compose migrate 服务完成，消费者使用本项目的 `yoyu.worker`。

本次验证覆盖 Compose 配置与临时数据库迁移链；尚未验证实际容器镜像构建和 PostgreSQL/Redis 容器运行，不据此宣称部署完成。

## 模块与上游维护

```text
src/renderer/            YOYU 产品界面与纯展示投影
src/main/harness*.ts     本地后端启动、认证、任务与回执传输
src/main/browser-bridge.ts
src/shared/browser.ts   浏览器命令契约与执行边界
backend/yoyu/            本项目的浏览器、业务、审批、记忆和提醒扩展
vendor/planora/          项目内固定的 Harness 源码与上游基线
pyproject.toml / uv.lock 本项目独立的 Python 环境
```

Planora 只作为源码来源。当前基线来自 v7 公开冻结归档，包含当时未提交的公开实现；不把预检结果当成正式质量验收。来源和逐文件哈希见 [SNAPSHOT.json](vendor/planora/SNAPSHOT.json)。运行时与构建时都不会读取原仓库。

上游发生修改时，仅在明确维护操作中查看差异：

```bash
npm run upstream:check -- --source /path/to/Planora
```

该命令只读报告上游变化、本地补丁和需要三方合并的文件，不会覆盖任何仓库。完整原始基线保存在 vendor/planora/upstream-base.tar.gz，可在审核新版本后进行三方合并；日常使用不需要该原仓库。
