---
paths: ["backend/app/**/*.py", "backend/tests/**/*.py"]
---

# Crucible 安全规范

> **凭据零落盘、沙箱真隔离、Agent 零信任**。三件底线破坏任一即不可上线。

## 1. 凭据零落盘

- LLM API Key / 证书 / Token **只**走环境变量或运行时卷注入（`docker run --env` 注入 agent-runner 容器，容器销毁 env 消失）
- 绝不写进 `Dockerfile`、`docker-compose.yml`、`alembic/` 种子、`configs/` 文件
- agent-runner 内通过 `ClaudeSdkAdapter.build_runner_env()` 注入 `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL` 等 8 个变量
- 容器销毁时容器层全部清理；不要在容器文件系统留临时凭据文件
- 提交前 `git grep -nE "sk-[A-Za-z0-9_-]{20,}|ghp_[A-Za-z0-9]{30,}"` 必须零命中

## 2. Agent Runner 安全基线（`core/agent_runner.py` `AgentRunnerSpec` 默认值即红线）

| 项 | 要求 |
|---|---|
| 用户 | 非 root（user=1000） |
| rootfs | 只读 |
| capabilities | `cap_drop: ["ALL"]` |
| 内存/CPU/PIDs | 必须限 |
| 网络 | 默认 `False`（可外联 git clone），专用网络只隔离平台服务 |
| 超时 | 必须有，到点强制回收 |
| tmpfs | `uid=1000,gid=1000,mode=0755` |

新增字段前先讨论是否触碰红线。

## 3. Fernet 加密

- `SETTINGS_ENCRYPT_KEY`（base64 32 字节）生产必配；开发从 `AUTH_SECRET` 派生但**必须**文档标注
- 列表接口只回显掩码（`***{last4}`），永不返回明文
- 加密函数与设置 Context 解耦：`core/crypto.py` 只做加密，Context 只做"何时加密"

## 5. 生产环境强校验（`core/config.py` validator）

当 `ENVIRONMENT=production`：

- `AUTH_SECRET` 必须配置（≥32 字节）
- **禁止** SQLite（必须 PostgreSQL）
- `SETTINGS_ENCRYPT_KEY` 必须显式配置（不接受派生）
- CORS 白名单必须显式（不接受 `*`）

dev 环境跳过这些校验，但提醒日志要打。

## 6. Agent 零信任

- Agent（Claude Code CLI / 自研 Agent）输出视为**不可信**
- Agent 写出的报告、证据、命令结果都要走 schema 校验才能落库
- Agent 不能直接访问平台数据库 / Redis / MinIO——只能通过沙箱 + 受控 API
- 取消任务必须 `celery_app.control.revoke(task_id, terminate=True)` + 沙箱销毁 + run 标记 cancelled（**待补**，见 docs §4 P0-2）

## 7. 安全事件上报

- 沙箱逃逸迹象（异常 mount、`/proc`/`/sys` 访问）记 WARN 日志 + Prometheus 计数器
- Fernet 解密失败记 ERROR（可能是配置错误或主动攻击）
- 凭据相关日志一律**掩码**，不打印完整 key