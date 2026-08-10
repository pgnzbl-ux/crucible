# Crucible — AI 漏洞自动验证平台

> 坩埚：代码与漏洞投入烈火，真伪自现。

## 项目概述

Crucible 是一个 AI 驱动的漏洞自动验证平台。安全研究员提交项目地址和漏洞描述，平台通过 Claude Code Agent（可对接 DeepSeek 等第三方 LLM）在隔离环境中自动分析源码、搭建复现环境、验证漏洞、生成报告。

## 技术栈

- **后端**: Python 3.11+ / FastAPI / SQLAlchemy 2.0 Async / Celery / Redis
- **前端**: React 19 / TypeScript / Vite 6 / Ant Design 5 / Zustand / TanStack Query
- **数据库**: PostgreSQL 16 (生产) / SQLite (开发)
- **对象存储**: MinIO (S3 兼容)
- **Agent**: Claude Code CLI（Anthropic 兼容端点，支持 DeepSeek / 腾讯云）
- **沙箱**: Docker（开发直连宿主机 / 生产嵌套 DinD）

## 快速启动（开发模式 · Windows 宿主机直连）

> 当前开发机为 Windows，后端直接跑在宿主机上，SandboxManager 直连宿主 Docker daemon，
> **不启用**嵌套 DinD 与 mTLS（生产部署才需要，见下文「生产部署」）。

```bash
# 1. 基础设施（端口避开宿主机默认 5432/6379）
cd infrastructure && docker compose up -d

# 2. 构建沙箱基础镜像（首次必需，内置 git + node + claude CLI）
docker build -f sandbox/Dockerfile -t crucible-sandbox:base .

# 3. 后端（配置在 backend/.env）
cd backend
pip install -e ".[dev]"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010

# 4. Celery worker（独立终端）
python run_worker.py

# 5. 前端
cd frontend && npm install && npm run dev
```

访问地址：
- 前端 → http://localhost:5173
- API 文档 → http://localhost:8010/docs
- MinIO 控制台 → http://localhost:9001

## 运行时架构（两种模式）

### 开发模式（默认 · Windows 宿主机）

```
宿主机 (Windows)
├── API + Celery Worker    # 直接跑在宿主机
├── postgres/redis/minio   # Docker 容器 (crucible-net)
└── SandboxManager ──直连──▶ 宿主 Docker daemon（docker.sock）
     └─ Agent 容器（内置 claude CLI，LLM 凭据环境变量注入）
          └─ 沙箱内 git clone → 分析 → 复现
```

- `SandboxManager` 通过 `docker.from_env()` 直连宿主 daemon
- 沙箱容器接 `crucible-sandbox-net`（与平台网络隔离）
- **无需** socket-proxy / dind / mTLS —— 全部在宿主机权限模型内完成

### 生产模式（Docker 化部署 · 嵌套 DinD + mTLS）

见下文「生产部署」完整设计。

## 生产部署（全 Docker 化）

### 架构总览

安全模型的核心转变：**安全边界从「限制 Docker API 操作类型」改为「限制 daemon 实例」**。
Agent 与复现环境**不接触宿主 daemon**，只连接每个任务独立的嵌套 Docker daemon（DinD），
业务在嵌套 daemon 内拥有完整 Docker 能力（build / pull / compose / 卷 / 网络 / exec / cp），
但破坏半径被限制在任务自己的 namespace 内，平台容器零暴露。

```
宿主 Docker daemon
├── 平台容器：API + Worker / postgres / redis / minio
├── 镜像缓存卷（crucible-dind-cache，各任务 dind 共享，加速 pull/build）
└── 每任务动态创建：
     └── dind 容器（docker:28-dind，--privileged，--tlsverify 监听 2376）
          └── Agent 容器（内置 claude CLI，DOCKER_HOST=tcp://dind-<task>:2376）
               └── 嵌套 daemon 内：docker pull / build / compose up / exec / cp
任务结束 → compose down -v + 销毁 dind（label 回收，缓存卷保留）
```

### 为什么不用「宿主 daemon + socket-proxy ACL」

漏洞验证业务的靶场搭建流程（`run-project-env` / `vuln-verify`）需要近乎完整的 Docker 能力：

| 业务动作 | 说明 |
|---------|------|
| `docker pull` | 拉取官方/数据库/Redis 等现成镜像 |
| `docker build` | 自建 Dockerfile 多阶段构建 |
| `docker compose up -d --build` | 多服务编排 |
| `docker compose logs/ps/down` | 排障与清理 |
| 命名卷 / 自动建网络 | 数据持久化与服务互连 |
| `docker exec` / `docker cp` | 修改配置、拷入文件、发请求验证 |

在宿主 daemon 上做细粒度 ACL（禁 build/pull/compose/卷/网络）会**直接杀死靶场业务**；
而全开放又会让 Agent 触碰平台容器。嵌套 DinD 通过 **daemon 实例隔离** 同时满足两者：
业务全能力 + 平台零暴露。这是 GitLab Runner DinD / Jenkins DinD 的业界标准方案。

### mTLS 双向认证（2376 端口）

嵌套 daemon 的 TCP API **绝不免密**（2375 明文端口禁用），启用 Docker 官方标准
mTLS（`--tlsverify`），客户端必须持有平台 CA 签发的证书才能连接：

```
平台 CA（crucible-docker-ca 卷，Worker 侧保管，首启幂等生成）
  ├─ 签 dind 服务端证书（CN=dind-<task>，SAN=容器名，serverAuth EKU）
  └─ 签 Agent 客户端证书（CN=agent-<task>，clientAuth EKU，有效期 24h）
```

```bash
# dind 启动（dockerd 带 TLS）
docker run -d --privileged \
  -v crucible-docker-ca:/certs:ro \
  -v crucible-dind-cache:/var/lib/docker \
  --name dind-<task> \
  docker:28-dind dockerd \
    --tlsverify \
    --tlscacert=/certs/ca.pem \
    --tlscert=/certs/server-cert.pem \
    --tlskey=/certs/server-key.pem \
    -H tcp://0.0.0.0:2376 \
    --label managed_by=crucible --label task_id=<task>
```

```bash
# Agent 容器侧（claude 用 docker CLI 驱动嵌套 daemon）
DOCKER_HOST=tcp://dind-<task>:2376
DOCKER_TLS_VERIFY=1
DOCKER_CERT_PATH=/certs        # ca.pem + cert.pem + key.pem（clientAuth）
```

**证书生命周期**：

| 环节 | 做法 |
|------|------|
| CA 初始化 | 平台首启生成（幂等 `ensure_ca()`），CA 私钥仅存 Worker 挂载卷，不落任何容器 |
| 每任务签发 | Worker 用 Python `cryptography` 库签发 serverAuth + clientAuth 两张证书（24h） |
| 挂载方式 | 证书目录只读卷挂入 dind / Agent 容器（`/certs`），不写进镜像层 |
| 随任务销毁 | dind + Agent 容器移除，证书临时卷删除；CA 私钥保留平台侧 |
| 凭据零落盘 | 证书运行时挂载，非镜像内容 |

**安全收益**：
1. 无客户端证书的主机扫描到 2376 也无法连接
2. 证书任务级隔离：A 任务证书连不上 B 任务 dind（CN 不同）
3. Agent 被攻破也只能用自己任务的证书打自己的嵌套 daemon —— 破坏半径仍受限
4. 短期证书（24h）压缩泄漏利用窗口

### 网络与资源约束

- **双网络**：平台 `crucible-net`（postgres/redis/minio/API），任务 `crucible-sandbox-net`（dind + Agent + 复现环境），互不连通
- **出口管控**：任务网络可外联（pull 镜像、应用外联需要），平台内网不可达
- **资源配额**：dind 容器设 CPU / 内存 / 磁盘限制（`--privileged` 是唯一特权点）
- **标签回收**：所有 dind / 复现容器带 `managed_by=crucible` + `task_id` 标签，Worker 定期 `cleanup_stale()` 回收孤儿

### 生产模式启动

```bash
# infrastructure/docker-compose.yml 中：
# - 声明 cruicible-docker-ca / cruicible-dind-cache 卷
# - API/Worker 容器挂载宿主 docker.sock（只读）—— 仅用于创建/销毁 dind，代码层强制
# backend/.env：
SANDBOX_RUNTIME=dind
DIND_IMAGE=docker:28-dind
DIND_CACHE_VOLUME=crucible-dind-cache
DIND_CERT_TTL_HOURS=24
```

## 对接第三方 LLM（DeepSeek）

Claude Code 通过 Anthropic 兼容端点对接 DeepSeek，凭据以环境变量注入沙箱（零落盘）：

```bash
# backend/.env
CLAUDE_CODE_ENABLED=true
LLM_BASE_URL=https://api.deepseek.com/anthropic    # DeepSeek 官方端点
LLM_API_KEY=sk-xxx                                  # 你的 DeepSeek API Key
LLM_MODEL=deepseek-v4-flash                         # v4-flash 非思考 / v4-pro 思考
```

- 腾讯云端点：`https://api.lkeap.cloud.tencent.com/anthropic`（模型 `deepseek-v3.2`）
- 旧模型名 `deepseek-chat`/`deepseek-reasoner` 已于 2026-07-24 弃用
- LLM 配置可在**平台后台管理**（侧边栏「设置」→ LLM Provider）：支持多 Provider、
  API Key Fernet 加密落库、测试连接、默认切换，无需改文件重启
- 沙箱镜像已内置 Claude Code CLI（`claude 2.1.226`）

## 项目结构

```
Crucible/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI 入口
│   │   ├── core/                # 配置、数据库、安全、沙箱、加密
│   │   ├── contexts/            # Bounded Contexts
│   │   │   ├── task/            # 任务管理（CRUD + 事件流）
│   │   │   ├── agent/           # Agent 执行平台（claude 适配 + Celery 工作流）
│   │   │   ├── report/          # 报告与证据（MinIO 归档）
│   │   │   ├── identity/        # 认证与用户管理
│   │   │   └── settings/        # LLM Provider 后台配置
│   │   └── shared/              # 共享基类、事件总线
│   ├── run_worker.py            # Celery worker 入口
│   └── tests/                   # 冒烟测试
├── frontend/                    # React 前端
└── infrastructure/              # Docker Compose + 沙箱镜像
```

## 开发规范

- 模块化单体：Bounded Context 组织代码，跨 Context 只用 ForeignKey + ID，不建 ORM 关系
- 事件驱动：Context 间通过 Redis Pub/Sub 异步通信（`app/shared/events.py`）
- Security by Default：沙箱真隔离、凭据零落盘（环境变量/卷注入）、Agent 零信任
- Celery worker 用 `run_worker.py`（solo pool），不要用裸 `celery -A app.core.celery_app worker`

## 环境变量（backend/.env 关键项）

| 变量 | 说明 | 默认 |
|------|------|------|
| `SANDBOX_RUNTIME` | `host`（开发直连）/ `dind`（生产嵌套） | `host` |
| `CLAUDE_CODE_ENABLED` | 是否启用真实 Agent（否则 Mock） | `false` |
| `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | 第三方 LLM 接入 | — |
| `SETTINGS_ENCRYPT_KEY` | Fernet 密钥（生产必配；开发从 AUTH_SECRET 派生） | — |
| `DATABASE_URL` | 开发 SQLite / 生产 PostgreSQL | SQLite |
| `REDIS_URL` / `CELERY_BROKER_URL` | Redis 与 Celery（端口 6380） | — |
