# PlanGo 迁移与回滚

本轮状态见 [实施进度](实施进度.md)。运行名为 `plango`，展示名为 PlanGo；上游来源、原始 Alembic revision、历史报告与作者 Xiaonian 原文保留。

## 配置与环境

`python3 scripts/setup_backend.py --dev` 创建独立 conda `plango`，按本仓库锁安装。原 `planora` 不修改。

安装脚本先执行 `migrate_config.py`：YOYU_* / XIAONIAN_* → PLANGO_*，LLM_PROVIDER / DATA_SOURCE → PLANGO_LLM_PROVIDER / PLANGO_DATA_SOURCE。相同的新键优先；新旧值冲突时停止，禁止静默改 token。原 `.env` 备份为 `.env.before-plango`（忽略提交），保留供应商标准键和值、重庆地区与权限，解释器更新为新环境路径。不要以模板覆盖真实配置。

## 已有 Docker 数据

不能直接对旧安装执行新 Compose 并当成完成迁移。先核对旧项目标签、working_dir、配置路径与进行中任务；保存原 Compose、环境和 Git 基线。停旧 API/worker 后导出 PG，Redis SAVE，再停旧 PG/Redis，复制完整旧卷到新卷：

| 原卷 | 目标卷 |
| --- | --- |
| yoyu_postgres-data | plango_postgres-data |
| yoyu_redis-data | plango_redis-data |
| yoyu_runtime-data | plango_runtime-data |

本机私有冷备份在 `output/r0-backup`；原卷始终保留。复制后按每个文件核对内容哈希、权限与属主；目录 mtime 因解包会改变，不作为文件内容校验。原数据目录权限不能改给宿主账号。

新 PG 启动后，使用维护连接把旧数据库 `yoyu` 改为 `plango`、旧 role 同样改名；不能改 POSTGRES_DB/USER 后假定已有 PG 自动迁移。先确认密码是 SCRAM：本机 SCRAM 凭据保持原值；若 MD5，role 改名会清除密码，必须使用原秘密值重新设置，不能生成新密码。临时维护 role 完成后删除。

Redis 切换必须在旧生产者/消费者已停、新 worker 尚未启动时执行 `scripts/migrate_runtime.py`；键与消费组、PEL/投递次数/最后消费位置一起保留，禁止从头创建组。数据库新增 Alembic 迁移原地改表名，原 revision ID 不变。checkpoint 保留原字节，使用明确 contracts 类型转换恢复，不扩大反序列化权限；审批、回执和命令摘要不作字符串替换。

新 migrate/API/worker 就绪前逐表核对迁移前后数据摘要；新 run 为测试专用时单独记录，不能掩盖原任务消失。保持旧容器停止，避免双消费者。

## 桌面

Electron ready 前完整迁移旧 userData 至 `plango`，包括 Local State、49 条本机 Cookie、localStorage、harness 身份与回执；`Partitions/xiaonian` → `Partitions/plango`，配置文件与 `xy_*` 状态键按一次性兼容迁移。发现两份不同状态或活跃旧桌面即停止，禁止选空状态覆盖。迁移前保存完整 profile 副本。

本机现存 Cookie 未使用 encrypted_value；如遇依赖旧应用名的 OS keyring 加密 profile，需单独迁移密钥后再打开，当前转换器会拒绝自动打开，避免静默失去登录。

CLI 旧默认 `data/yoyu` 存在且未显式指定路径时，后端拒绝创建新空库。先停止本项目本地消费者、备份该完整目录，在目标 `data/plango` 不存在的前提下整目录改名，再运行迁移。显式数据库地址不猜改。

Electron 自身可能提前建立 `~/.config/PlanGo/Crashpad`；只有顶层仅包含真实 Crashpad 目录时识别为启动产物，保留原位，用户数据仍迁往 `plango`。任何其他内容都按状态冲突处理。

## 回滚边界

本轮验证前不删除旧卷、旧镜像与私有备份。回滚时先停新 API/worker；如切换后产生新的用户状态，应先导出新库并迁回，不能直接覆盖。仅在确认没有切换后新写入时，恢复原源码/配置、从原 Compose 启动旧卷并恢复原 profile；不要同时启动新旧消费者。已迁移源码的目录不能直接挂给旧运行时。

## 旧名保留分类

- `scripts/sync_planora.py`、SNAPSHOT 和 upstream-base：真实上游维护与来源，不是本项目运行包。
- 旧 Alembic revision/迁移：已应用的历史数据库契约。
- 迁移器中的旧前缀、表名、profile 与 contracts 路径：仅升级兼容；移除须等所有保留的安装/备份与checkpoint均转换或失效。
- `eval/` 既有 JSON、截图、原审计文档与 figures/yoyu-*：原始证据。本轮新结果写 `eval/plango-r0/`，不覆盖历史分母。
- `docs/YOYU_Planora_融合方案.md`：用户未跟踪原文，禁止覆盖、删除或顺手提交。

实现依据：[PostgreSQL ALTER ROLE](https://www.postgresql.org/docs/16/sql-alterrole.html)、[Redis XCLAIM](https://redis.io/docs/latest/commands/xclaim/)、[Electron app/sessionData](https://www.electronjs.org/docs/latest/api/app)。
