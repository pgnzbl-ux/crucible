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

## 2.1 LLM Base URL 与 Compose 准入

- Provider Base URL 必须是 HTTPS 域名，不允许 IP 字面量、userinfo 或 fragment
- create/update/test 和注入 Runner 前均解析 DNS；结果须为公网地址，或 TUN fake-ip 网段 `198.18.0.0/15`（Clash/Surge 等，不路由到真实内网）
- 仍拒绝 RFC1918、回环、链路本地、元数据地址（如 `169.254.169.254`）与 CGNAT `100.64.0.0/10`
- LLM 测试连接禁止跟随重定向；不使用域名白名单，以兼容未知公网 Provider
- AI 生成 Compose 在宿主执行前必须经过 `lab/compose_policy.py`；拒绝 privileged、host namespace、devices、cap_add、运行时 socket、越界 bind mount/build context
- 配方上传或 Lab ready 落库失败必须补偿 `compose down`；巡检兜底回收终态 Lab 运行时

## 3. 凭据存储(当前状态)

- **当前为明文存储**:`settings/service.py` 的 build_env_from_provider/create_provider/test_connection 全程明文存取,响应层 `mask_secret` 掩码
- `core/crypto.py` 的 `encrypt_secret/decrypt_secret` 仍在但**已不被 settings/credential 调用**,属遗留
- 列表接口只回显掩码(`***{last4}`)
- 待办:如需恢复加密,重新接入 `core/crypto.py`;当前 `SETTINGS_ENCRYPT_KEY` 配置未生效

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
- Task、Report、Evidence 的详情和状态变更必须在 Repository 查询中绑定 owner；非 owner 与不存在统一 404
- 取消任务必须先提交 cancelled 再返回；`revoke(terminate=True)` + 后台拆 agent-runner/靶场；编排器不得把 cancelled 写回 failed(已落地,task/service.py + orchestrator.py)

## 7. 安全事件上报

- 沙箱逃逸迹象（异常 mount、`/proc`/`/sys` 访问）记 WARN 日志 + Prometheus 计数器
- Fernet 解密失败记 ERROR（可能是配置错误或主动攻击）
- 凭据相关日志一律**掩码**，不打印完整 key