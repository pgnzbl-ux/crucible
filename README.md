

# 🔥 Crucible

**AI 驱动的漏洞自动验证平台**

代码与漏洞投入烈火，真伪自现。

[Python](https://www.python.org/)
[FastAPI](https://fastapi.tiangolo.com/)
[React](https://react.dev/)
[PostgreSQL](https://www.postgresql.org/)
[Docker](https://docs.docker.com/compose/)



---

## 目录

- [这是什么](#这是什么)
- [解决什么问题](#解决什么问题)
- [平台特色](#平台特色)
- [技术方案与架构](#技术方案与架构)
- [安装部署](#安装部署)
- [配置 LLM](#配置-llm)
- [开始使用](#开始使用)
- [注意事项](#注意事项)
- [项目结构](#项目结构)
- [测试](#测试)
- [尚未完成](#尚未完成)
- [文档](#文档)

---

## 这是什么

Crucible 是面向安全研究员的 **漏洞自动验证平台**。

你提交一个项目地址和一段漏洞描述，平台在隔离环境里自动完成：读源码、搭建靶场、白盒推演利用链、在靶场上确认危害、出具中文报告。

它验证的是「这个洞是不是真的、能不能打、证据是什么」，而不是再扫一遍代码找新洞。


|         |                                           |
| ------- | ----------------------------------------- |
| **你提供** | Git 仓库地址 + 漏洞描述（可附带已有 PoC / 分析过程）         |
| **你得到** | 结构化中文报告：结论、证据链、复现路径、修复建议；确认的漏洞附带可复现 PoC   |
| **判定**  | 已确认 / 部分确认 / 代码可达 / CODE SMELL / 误报 / 未复现 |


结论由**最强可观察证据**决定：诊断信号（HTTP 500、日志、sink 命中）只说明可达，不能当成「已确认」。

---

## 解决什么问题

扫描器（SAST / DAST）的输出通常是噪声大于信号：真实可利用漏洞、误报、代码异味混在一起。人工逐条复核贵，直接信扫描器又会漏掉真正能打的洞。

安全研究员真正要的是 **验证**，不是再「发现」一遍：

1. 这个告警是不是误报？
2. 攻击者能不能实际打出可观察危害？
3. 复现路径和证据是什么？
4. 该怎么修？

Crucible 按研究员的方法论自动走完这条链路：

> **白盒优先** —— 先用源码走通调用链、读全每一层防御，把 payload 在纸上推演到「理论可通」，再用一次请求在靶场确认。推演不通即判误报，不在靶场上黑盒盲试。

---

## 平台特色

- **白盒优先的验证，而不是扫描** —— 源码是主战场，靶场只做最后确认。
- **自动搭靶场并复用** —— 为 Web 项目拉起隔离运行环境；同一项目可复用已就绪靶场。
- **Agent 在沙箱里跑** —— 分析过程在独立 Docker 容器中执行，与平台进程隔离。
- **进度看得见** —— 任务详情实时展示节点进度和 Agent 过程（思考、工具调用、结论）。
- **报告与证据可归档** —— 中文结构化报告，证据进对象存储，可导出 JSON / Markdown。
- **自己选模型** —— 通过 Anthropic 兼容端点接入 DeepSeek 等；后台配置，设为默认即启用。
- **任务可取消、可从失败处续跑** —— 取消会停 Worker 并回收容器；重试复用已完成步骤。
- **登录隔离** —— JWT 认证，数据按用户隔离。

---

## 技术方案与架构

平台是 **Web 控制台 + 后台 Worker + 隔离 Agent 容器**：

```
浏览器（React）
    │  HTTP + SSE
    ▼
FastAPI  API  ── PostgreSQL
    │           Redis（任务队列 + 实时事件）
    │           MinIO（源码 / 报告 / 证据 / 靶场配方 / 失败节点包）
    ▼
Celery Worker
    ├─ 拉取 / 缓存源码
    ├─ 按 6 个节点编排验证
    ├─ 每个 AI 节点拉起一只 agent-runner 容器
    └─ 靶场用 Docker Compose 在隔离网络中启动
```

一次任务按固定流水线推进（非 Web 项目会在画像后结束，不搭靶场、不下漏洞结论）：

```
拉取源码 → 项目画像 → 靶场就绪 → 白盒审计 → 靶场复现 → 生成报告
```


| 节点  | 做什么                  |
| --- | -------------------- |
| 源码  | 拿到可分析的代码快照           |
| 画像  | 判断技术栈、是否 Web 应用      |
| 靶场  | 编写或复用运行环境，探活，给出访问方式  |
| 审计  | 只读源码，推演利用链；过不了门禁则判误报 |
| 复现  | 对靶场做有限次确认，给出六档判定     |
| 报告  | 唯一写文档的步骤，落库并归档       |


三层隔离：

1. **平台** —— API、Worker、数据库、Redis、MinIO
2. **Agent** —— 每个分析步骤一只容器；LLM 凭据只进容器环境变量，容器销毁即消失
3. **审计** —— Agent 输出不可信，校验通过才写入平台


| 层     | 技术                                               |
| ----- | ------------------------------------------------ |
| 后端    | Python 3.11+ · FastAPI · SQLAlchemy 2.0 · Celery |
| 前端    | React 19 · TypeScript · Vite 6 · Ant Design      |
| 数据    | PostgreSQL 16 · Redis 7 · MinIO                    |
| Agent | Claude Agent SDK，镜像 `crucible-agent-runner:base` |
| 运行    | Docker Compose 起基础设施；Worker 用 Docker 拉起分析容器与靶场   |


---

## 安装部署

开发环境是「基础设施容器 + 本机三个进程」。数据库是 Compose 里的 **PostgreSQL 16**（宿主机 `localhost:5433`）。

### 前置

- Docker 20+ 与 Docker Compose v2+
- Python 3.11+
- Node.js 18+
- 本机可访问 Docker（真实验证模式要拉容器）
- 可选：Anthropic 兼容 LLM 凭据（如 [DeepSeek](https://platform.deepseek.com/)）。没有凭据也能先用 Mock 跑通界面

### 启动

```bash
git clone <repo-url> Crucible && cd Crucible

# 1. Redis + MinIO（以及可选的 PostgreSQL）
cd infrastructure && docker compose up -d && cd ..

# 2. 构建 Agent 镜像（首次，或改了 agent-runner / 节点 skill 之后）
#    必须在仓库根目录构建
docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .

# 3. 后端 API（终端 1）
cd backend
cp .env.example .env
pip install -e ".[dev]"
python -m uvicorn app.main:app --host 0.0.0.0 --port 8010

# 4. Celery Worker（终端 2，必须用这个入口）
python run_worker.py

# 5. 前端（终端 3）
cd ../frontend
npm install
npm run dev
```

本地开发连 Docker 里的 PostgreSQL（`localhost:5433`）。空库在 API 启动时按当前模型建表；也可用：

```bash
cd backend
alembic upgrade head
```

### 访问


| 服务            | 地址                                                       | 说明                          |
| ------------- | -------------------------------------------------------- | --------------------------- |
| 控制台           | [http://localhost:5173](http://localhost:5173)           | 日常使用入口                      |
| API / Swagger | [http://localhost:8010/docs](http://localhost:8010/docs) | 接口文档                        |
| MinIO 控制台     | [http://localhost:9001](http://localhost:9001)           | `minioadmin` / `minioadmin` |
| PostgreSQL    | localhost:5433                                           | 仅在改用 PG 时需要                 |
| Redis         | localhost:6380                                           | Worker 与实时进度依赖它             |


打开 [http://localhost:5173](http://localhost:5173) 。库中还没有账号时，登录页会让你创建第一个账号；之后用该账号登录。

```bash
curl http://localhost:8010/health
# {"status":"ok","version":"0.3.0"}
```

---

## 配置 LLM

LLM 凭据 **只** 在控制台「设置 → LLM Provider」里配置，不要写进 `.env`。

`.env` 只负责打开真实 Agent：

```bash
# backend/.env
CLAUDE_AGENT_SDK_ENABLED=true
```

然后：登录 → 设置 → LLM Provider → 新增 → **设为默认**。列表里「默认」才是当前启用的接入点，备用配置不会被任务使用。


| 服务       | Base URL                             | 模型示例                                    |
| -------- | ------------------------------------ | --------------------------------------- |
| DeepSeek | `https://api.deepseek.com/anthropic` | `deepseek-v4-flash` / `deepseek-v4-pro` |


保存前可点「测试连接」。改默认后立即生效，不用重启进程。

---

## 开始使用

**Mock（默认）**：`CLAUDE_AGENT_SDK_ENABLED=false`。不调真实模型，用来确认「创建任务 → 进度 → 报告」整条链路能转。

1. 打开 [http://localhost:5173，按页面提示创建账号或登录](http://localhost:5173，按页面提示创建账号或登录)
2. 新建任务，填写 Git 地址和漏洞描述
3. 打开任务详情看节点进度
4. 结束后查看报告；需要时上传补充证据

**真实验证**：打开 `CLAUDE_AGENT_SDK_ENABLED=true`，并在设置里配置**默认** LLM Provider。Worker 会按节点拉起隔离容器做白盒分析与靶场复现。

接口文档见 [http://localhost:8010/docs（需先登录拿](http://localhost:8010/docs（需先登录拿) Token）。

---

## 注意事项

**进程与端口**

- 必须同时跑 **API + Worker + 前端**。只开 API 时任务会停在队列里。
- Worker 请用 `python run_worker.py`，不要自己拼 Celery 命令（Windows 需要 solo 进程池）。
- 端口刻意避开本机常见占用：API `8010`、前端 `5173`、Postgres `5433`、Redis `6380`、MinIO `9000/9001`。
- `python app/main.py` 默认不是 8010，请用上面的 uvicorn 命令。

**数据库**

- 开发连 Docker PostgreSQL（`DATABASE_URL` 指向 `localhost:5433`）。空库在 API 启动时建表。
- 生产同样是 PostgreSQL，并设置足够长的 `AUTH_SECRET`。

**真实 Agent**

- 需要本机 Docker，且已构建 `crucible-agent-runner:base`。
- 构建必须在**仓库根目录**执行，否则镜像里会缺节点 skill。
- 改过 `infrastructure/agent-runner/` 下的 skill 或 Dockerfile 后要重新 build。
- 任务会访问公网（拉代码、拉镜像、调 LLM）；请在可接受该行为的环境运行。

**凭据与安全**

- LLM API Key 存在平台数据库中（列表只显示掩码）。不要把这个开发实例直接暴露到公网。
- 账号存在数据库。只有库中还没有任何用户时，登录页才允许创建第一个账号；之后不能公开自助注册。
- 不要把 Key 写进 Dockerfile、compose 或提交到 Git。
- Agent 默认非 root、只读根文件系统、去掉全部 Linux capability，并有 CPU / 内存 / 超时上限。
- Agent 不能直连平台数据库；其输出校验后才落库。

**能力边界**

- 验证对象是「给定描述的那一个漏洞」，不是全量扫描。
- 非 Web 项目不会搭靶场，也不会给出漏洞结论。
- 靶场起不来（多轮失败）时任务失败，可重试。
- Mock 模式的报告不能当作真实审计结果。

---

## 项目结构

```
Crucible/
├── backend/                 # FastAPI + Celery
│   ├── app/contexts/        # 任务 / Agent / 靶场 / 项目 / 报告 / 认证 / 设置
│   ├── alembic/             # 唯一基线迁移（与当前模型一致）
│   └── .env.example
├── frontend/                # React 控制台
├── infrastructure/
│   ├── docker-compose.yml   # Postgres / Redis / MinIO
│   └── agent-runner/        # Agent 镜像与各节点 skill
├── plugins/vuln-verify-expert/   # 桌面方法论母本（不进入运行镜像）
├── docs/                    # 架构与节点设计
└── README.md
```

---

## 测试

```bash
cd backend
pytest tests -k "not smoke"
python tests/smoke_agent_runner.py   # 需要 Docker
python tests/smoke_sse.py

cd ../frontend
npm run typecheck
```

---

## 尚未完成

完整说明见 `[docs/development-guide.md](docs/development-guide.md)`。当前未做：

- 平台级 API Key / Service Account、OIDC、RBAC、操作审计日志
- 生产级部署（嵌套 Docker、mTLS）与 PostgreSQL 生产验证
- CI/CD、OpenTelemetry / Sentry
- OpenAPI 生成前端类型
- 任务 / 报告详情页的独立路由体验

---

## 文档


| 文档                                                     | 内容           |
| ------------------------------------------------------ | ------------ |
| [docs/development-guide.md](docs/development-guide.md) | 架构决策与后续工作    |
| [docs/agent-workflow.md](docs/agent-workflow.md)       | 六节点如何协作      |
| [docs/governance/](docs/governance/)                   | 开发约定         |
| [CLAUDE.md](CLAUDE.md)                                 | 给协作 AI 的项目入口 |
| [.claude/api-contract.md](.claude/api-contract.md)     | HTTP API 契约  |


---



让 AI 像安全研究员一样验证漏洞，而不是像扫描器一样盲射。

