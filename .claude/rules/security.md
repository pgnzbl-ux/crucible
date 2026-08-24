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
| 网络 | 默认可外联；容器注入公共 DNS（`223.5.5.5`/`8.8.8.8`/`1.1.1.1`）；专用网禁止 `internal` |
| 超时 | 必须有，到点强制回收 |
| tmpfs | `uid=1000,gid=1000,mode=0755` |

新增字段前先讨论是否触碰红线。

Runner 的 reproduce 节点通过 `host.docker.internal` 访问宿主映射的 Lab 端口，因此本阶段保留 host-gateway。通用 Agent 工具访问宿主端口属于已接受的剩余风险；彻底隔离需先改为 Runner 动态加入当前 Lab Compose 网络或引入出站代理，禁止只删别名造成生产复现链路失效。

## 2.1 Agent 工具权限（双层：工具白名单 + Bash 黑名单 hook）

运行在容器内 `runner/run_one.py`，`permission_mode="bypassPermissions"` + `allowed_tools` + `PreToolUse` hook：

1. **工具类型白名单**（`allowed_tools`，按节点裁剪）—— 白名单外的工具一律不可用
2. **Bash 命令黑名单**（`PreToolUse` hook，`matcher: "Bash"`）—— 拦截 `rm`/`mv`/`cp`/`chmod`/`chown`/`dd`/`mkfs`/`|bash`/`>/etc/`/`/proc/`/`/sys/` 等破坏性命令，其余放开（插件工作流需要 `docker compose`/`git`/`curl`/`python`）

**为什么用 hook 而非 `can_use_tool`**：SDK 的 `permission_mode="bypassPermissions"` 会 shadow `can_use_tool`（`CanUseToolShadowedWarning`，自动批准发生在回调之前）；而 `PreToolUse` hook 在**所有** permission_mode 下都执行，`permissionDecision: "deny"` 由 `_bundled/claude` CLI 原生消费，在 bypassPermissions 之前拦截。hooks 字段直接接受 async Python 回调（`HookCallback`），非 shell command。deny 时同步输出 `tool.call.denied` 审计事件。

## 2.2 LLM Base URL 与 Compose 准入

- **生产必须 `LLM_BASE_URL_RELAXED=false`**（`config` 启动校验强制）；本地开发可 `true` 以便指向本机/私网网关
- `false` 时：Provider Base URL 必须是 HTTPS 域名，不允许 IP 字面量、userinfo 或 fragment；DNS 结果须为公网或 TUN fake-ip `198.18.0.0/15`
- 仍拒绝 RFC1918、回环、链路本地、元数据地址（如 `169.254.169.254`）与 CGNAT `100.64.0.0/10`
- LLM 测试连接禁止跟随重定向；不使用域名白名单，以兼容未知公网 Provider
- AI 生成 Compose 在宿主执行前必须经过 `lab/compose_policy.py`；拒绝 privileged、host namespace、devices、cap_add、运行时 socket、越界 bind mount/build context
- 配方上传或 Lab ready 落库失败必须补偿 `compose down`；巡检兜底回收终态 Lab 运行时

## 3. 凭据存储(当前状态)

- **当前为明文存储**:`settings/service.py` 的 build_env_from_provider/create_provider/test_connection 全程明文存取,响应层 `mask_secret` 掩码
- `core/crypto.py` 的 `encrypt_secret/decrypt_secret` 仍在但**已不被 settings/credential 调用**,属遗留
- 列表接口只回显掩码(`***{last4}`)
- 待办:如需恢复加密,重新接入 `core/crypto.py`;当前 `SETTINGS_ENCRYPT_KEY` 配置未生效

## 4. 生产环境强校验（`core/config.py` validator）

当 `ENVIRONMENT=production`：

- `AUTH_SECRET` 必须为强随机（拒绝空串与示例值）
- **禁止** SQLite（必须 PostgreSQL）
- `LLM_BASE_URL_RELAXED=false`
- `CLAUDE_AGENT_SDK_ENABLED=true` 且 `LLM_GATEWAY_ENABLED=true`
- `METRICS_TOKEN` 非空（保护 `/metrics`）
- `CORS_ORIGINS` 精确域名白名单（禁止 `*`）

`SETTINGS_ENCRYPT_KEY` 因 Fernet 未接入而不生效（见 §3；明文 Key 暂保留）。dev 环境跳过这些校验。

SSE：前端先 `POST /tasks/{id}/events/ticket` 取短命票，EventSource 用 `?ticket=`；生产拒绝 `?token=` 传 access JWT。

## 5. Agent 零信任

- Agent（Claude Code CLI / 自研 Agent）输出视为**不可信**
- Agent 写出的报告、证据、命令结果都要走 schema 校验才能落库
- Agent 不能直接访问平台数据库 / Redis / MinIO——只能通过沙箱 + 受控 API
- Task、Report、Evidence 的详情和状态变更必须在 Repository 查询中绑定 owner；非 owner 与不存在统一 404
- 取消任务必须先提交 cancelled 再返回；`revoke(terminate=True)` + 后台拆 agent-runner/靶场；编排器不得把 cancelled 写回 failed(已落地,task/service.py + orchestrator.py)

## 6. 安全事件上报

- 沙箱逃逸迹象（异常 mount、`/proc`/`/sys` 访问）记 WARN 日志 + Prometheus 计数器
- Fernet 解密失败记 ERROR（可能是配置错误或主动攻击）
- 凭据相关日志一律**掩码**，不打印完整 key