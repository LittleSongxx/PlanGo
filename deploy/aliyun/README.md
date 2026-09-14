# 阿里云部署（求职作品集演示）

本目录把 PlanGo 部署为一台阿里云 ECS 上的演示站。**这不是生产形态**：没有 TLS、多副本与限流；目标是面试官打开一个网址就能看到并操控完整的 PlanGo 桌面应用。

PlanGo 的产品形态是 Electron 桌面应用（前端与 Electron IPC 深度耦合，无纯网页模式），因此云端演示采用两层：

- **服务栈**：仓库主 `docker-compose.yml`（PostgreSQL + Redis + 迁移 + API + worker），`compose.demo.yaml` 通过 `include` 原样复用，不改任何主服务配置。
- **桌面演示容器**：Electron 桌面跑在容器内 Xvfb 虚拟显示上，x11vnc + websockify 以 noVNC 暴露画面——浏览器打开 `http://<ECS公网IP>:6080/vnc.html` 即遥控真实桌面，内嵌真实浏览器、地图与画布都可用。密钥全部经环境变量注入，不写入镜像（`.env` 被排除在镜像构建上下文之外）。

```
浏览器 ── http :6080 ──> desktop-demo 容器（Xvfb + x11vnc + websockify/noVNC + Electron PlanGo）
                              │ compose 内网 http://api:8011（Bearer token）
                              └──> postgres / redis / migrate / api / worker（主 compose 原样）
```

## 前置条件

- 阿里云账号 + 一台 ECS：推荐 2 vCPU / 8 GiB（4 GiB 是下限，Electron+Chromium 约 2 GiB）、Ubuntu 24.04、按使用流量计费公网带宽、有公网 IP。
- 模型 Key：现用 DashScope（`OPENAI_BASE_URL` 指向 `dashscope.aliyuncs.com/compatible-mode/v1`，qwen 系列）——与 ECS 同在阿里云，无需跨境。高德 Web 服务 Key + JS Key/安全码各一枚。
- `.env`：在本机 `.env.example` 基础上填写真实 Key，并生成两个随机值（安装脚本 `npm run setup:backend` 也会自动生成，服务器上手工生成即可）：

```bash
printf 'PLANGO_BACKEND_TOKEN=%s\nPLANGO_POSTGRES_PASSWORD=%s\n' \
  "$(openssl rand -hex 32)" "$(openssl rand -hex 24)" >> .env
```

`.env` 不入库（已 gitignore），也不进镜像。

## 部署步骤

1. **安全组**：入方向放行 22（SSH，限自己 IP）、6080（noVNC 演示，建议限自己 IP，见下方安全说明）；可选 8011（叠加 `compose.expose-api.yaml` 时）。
2. **装 Docker**：`curl -fsSL https://get.docker.com | sh`（阿里云 ECS 上该脚本会自动选用内网镜像源）。
3. **传代码**：`git clone` 本仓库到 ECS（私有仓用 SSH Key），或本机 `tar --exclude=node_modules --exclude=.git --exclude=output --exclude=release -czf plango-src.tar.gz .` 后 `scp` 上去解压。求职笔记等未跟踪文件不会被 git 带上服务器。
4. **写 `.env`**（同上一步格式，放进仓库根目录）。
5. **一条命令起栈**（注意 `--env-file .env` 必须显式传：compose 的项目目录默认取 compose 文件所在目录，读不到仓库根的 `.env`）：

```bash
docker compose -p plango --env-file .env -f deploy/aliyun/compose.demo.yaml up -d --build
```

6. 浏览器打开 `http://<ECS公网IP>:6080/vnc.html`，点 Connect，即可看到 1800×1120 的 PlanGo 桌面。中文输入用 noVNC 左侧剪贴板面板粘贴（容器内无输入法）。
7. （可选）公开 API 供展示或桌面试用包直连：再叠加 `-f deploy/aliyun/compose.expose-api.yaml` 后 `up -d`；API 全程要求 Bearer `PLANGO_BACKEND_TOKEN`。

## 安全与成本（务必读）

- 6080 端口等于**把一个真实浏览器和你的模型配额交给持有网址的人**。默认只对自己的 IP 放行；面试演示前临时放开、演示完收回，或直接提前录屏。应用内每次任务受 `PLANGO_MAX_MODEL_TOKENS/TOOL_CALLS/RUN_SECONDS` 预算约束，但不构成访问控制。
- 不叠加 expose-api 时，API 只绑容器网络，公网摸不到。
- 机器不用时 `docker compose -p plango down`（保留数据卷）或直接停机，按量计费不烧钱。

## 本地先跑通（与 ECS 同一套命令）

```bash
# 18011 避开本机主栈的 8011；构建需拉 npm 与 Electron 二进制（已配 npmmirror 镜像）
PLANGO_SERVICE_PORT=18011 docker compose -p plango-demo --env-file .env \
  -f deploy/aliyun/compose.demo.yaml up -d --build
# 打开 http://127.0.0.1:6080/vnc.html
docker compose -p plango-demo --env-file .env -f deploy/aliyun/compose.demo.yaml down
```

## 与试用包的关系

`release/plango-0.1.0-linux-x64-*.tar.gz` 是 Linux/WSLg 桌面试用包（见 `docs/试用安装.md`），可上传阿里云 OSS 生成下载链接作为作品集的"可安装"形态；noVNC 演示站则是"零安装即可看"的形态，两者互补。

## 文件清单

- `compose.demo.yaml`：演示栈入口（include 主 compose + desktop-demo 服务）。
- `compose.expose-api.yaml`：可选叠加，把 API 发布到公网（token 门禁，无 TLS）。
- `desktop-demo/Dockerfile` + `Dockerfile.dockerignore`：桌面演示镜像（node:22 + Electron + Xvfb/x11vnc/noVNC + Noto CJK 字体）；专属 dockerignore 是因为仓库根 `.dockerignore` 为后端白名单式、不含前端路径。
- `desktop-demo/entrypoint.sh`：起 Xvfb/VNC 通道后前台运行 Electron。
