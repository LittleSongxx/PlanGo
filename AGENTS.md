# YOYU 项目约定

已确认的项目决策见 [docs/架构决策.md](docs/架构决策.md)。开始设计或改动前先读该文件；其中的目标架构不代表已经实现。

- 项目独立运行，不依赖兄弟 Planora 仓库、环境配置或服务；上游仅通过本仓库固定源码与显式维护操作参考。
- 后端 Python 使用名为 `planora` 的 conda 环境；该名称不代表依赖 Planora 项目。
- 保留 Electron 桌面与真实可见浏览器。浏览器演进方向已确认：WebContentsView、经适配验证的 Playwright/CDP、DOM-first 与按需 Vision，共用同一会话及 Harness 授权/回执边界。
- 不用 mock 补齐正常运行的商家事实或业务结果。测试样本与真实业务验收必须明确区分。
- 本项目 `.env` 中的密钥不输出、不提交；不要修改兄弟 Planora 正在进行的工作。
