---
name: run-project-env
description: 为漏洞验证场景搭建可复现靶场：从源码把 web 项目在本地容器中跑起来并返回访问地址，全程容器隔离不污染本地。当用户说"复现这个项目环境"、"搭漏洞验证靶场"、"把这个项目跑起来做验证"、"帮我起环境"时使用；与 vuln-verify 配合，产物按 `.vuln-env/` 约定放置。流程：先读 README 与关键文件建立项目全景 → 判定是否为 web / web api 类型（非 web 直接结束）→ 查是否有可直接 pull 的现成官方镜像 → 没有则自行编写 Dockerfile，多服务则编写 docker-compose → 启动环境，失败时回溯并轮询修正直到跑通 → 返回访问地址并沉淀 Docker 配置。
---

# 项目环境自动启动（run-project-env）

拿到任意源码（本地目录或 GitHub 仓库），把它的**运行环境在本地容器中跑起来**，返回可访问地址，并沉淀一份可复用的正确 Docker 配置。核心约束：**用容器隔离，尽量不污染本地环境**（不往宿主机装语言运行时/数据库/依赖）。

## 适用范围与自带参考资料

本 skill 的目标是**在本地把环境跑通**（复现、调试、验收），不是打包镜像推到仓库。选基础镜像、定默认端口、写 Dockerfile 时，直接读本 skill 自带的参考资料（已随 skill 一起安装，无需其它依赖）：

- 项目类型检测：`references/project-detection.md`
- Dockerfile 各语言模板：`references/dockerfile-patterns.md`

## 核心理念

1. **先理解，再动手**：不读懂项目就写 Dockerfile 大概率白费功夫。启动方式、端口、依赖的中间件（数据库/Redis/MQ）都要从代码里读出来。
2. **优先用现成的**：官方或社区维护的镜像通常比自己拼的更可靠。能 `docker pull` 就别自己写 Dockerfile。
3. **失败是常态，轮询修正**：环境跑不通很正常。系统化排障（看日志→定位→改配置→重试），不要瞎猜乱改。见 `references/startup-troubleshooting.md`。
4. **隔离优先**：所有东西装进容器，宿主机只留 Docker。见 `references/isolation.md`。

## 工作区目录约定（强制）

本工作区（即你为漏洞验证任务指定的根目录，下文统称 `<工作区>`）下，**每个顶层文件夹就是一个独立项目**。本项目所有验证产物必须放在**该项目自己的目录内**，便于项目单独打包、归档、清理，绝不污染工作区根目录或其它项目。

- **禁止**在 `<工作区>` 根目录、或在**别的项目**目录下创建兄弟目录来存放某个项目的 docker / 报告 / 状态文件。
  反例（曾经犯过）：把某项目的靶场建在 `<工作区>/.vuln-<项目名>` —— 它既不在该项目内，又挤在其它项目旁边。正确做法是放在 `<项目目录>/.vuln-env`。
- **所有验证操作都从 `<project_root>` 内部发起**：无论是 `git clone` 下来的，还是工作区里已存在的项目目录，**先 `cd` 进这个项目目录，再跑 docker compose / PoC 脚本 / 生成报告**。产物与命令目录一致，项目才能单独打包、归档、清理。
- `<project_root>` = 用户提供的「项目地址」目录。**即使源码在它的子目录里（如 `upload/`）**，产物也放 `<project_root>` 自身，不放源码子目录（除非用户给的「项目地址」就是那个子目录）。
- **多服务 compose 必须加 `name:`** 固定 compose 项目名，使容器名 / 卷名与目录位置解耦——这样搬运目录后 `docker compose down/up` 仍指向同一套资源，不会出现孤儿容器或端口冲突。

推荐布局：

```
<project_root>/                          # 用户提供的「项目地址」目录
├── .vuln-env/                           # 靶场 docker 构建与编排（分支 C 自建时）
│   ├── Dockerfile
│   ├── docker-compose.yml               # 含 name: <项目-slug>
│   ├── build/                           # 源码构建副本（按需改种子/配置，不碰用户原始工作区）
│   ├── *.sql                            # 初始化 / 种子 SQL
│   └── poc_browser.py 等辅助脚本
├── .vuln-env.json                       # 环境状态：地址 / 端口 / compose 路径 / 初始账号 / commit
└── VULN-<NNN>-<short-title>/            # 单漏洞报告（vuln-verify 产出）
    ├── report.md
    ├── VULN-<NNN>_Report.docx
    ├── RUN_ENV.md
    └── img/
```

> 第 4 步「产物统一放在项目目录内（或 `.vuln-env/` 子目录）」即指此约定；默认用 `.vuln-env/` 命名，与 `.vuln-env.json` 状态文件保持一致。

## 标准工作流程

按顺序执行。每一步的产出都为下一步服务，遇阻回溯。

### 第 1 步：建立项目全景

目标是能一句话回答"这是什么、怎么跑、依赖什么"。

- **读 README**（README.md / README_*.md / docs/），抓：项目定位、技术栈、启动命令、端口、依赖的中间件、已有的 Docker/compose 说明。
- **看依赖与入口文件**，按语言：`package.json`、`pom.xml`、`build.gradle`、`requirements.txt`/`pyproject.toml`、`go.mod`、`composer.json`、`Cargo.toml`。
- **找配置文件**：`application.yml`/`application*.properties`、`.env`/`.env.example`、`config/*`——从中读端口、数据库连接串、外部服务地址。
- **检查是否已有容器化产物**：`Dockerfile*`、`docker-compose*.yml`、`.devcontainer/`、`Makefile`、`bin/`启动脚本。**如果项目自带 compose，优先直接用它**（第 4 步）。

如果是 GitHub 地址而非本地目录，先 `git clone` 到工作目录再分析。

输出一段简短全景总结给用户：技术栈、启动方式、端口、依赖的中间件、是否自带 Docker 配置。

### 第 2 步：判定项目类型（web 门禁）

**只处理 web 与 web api 类型**，其它类型（CLI 工具、库、桌面应用、纯脚本、数据处理批任务等）**任务到此结束**，明确告知用户"非 web 项目，本 skill 不处理"。

判定依据见 `references/web-detection.md`。要点：有 HTTP 服务端框架（Spring MVC/Boot、Express/Nest/Koa、Flask/FastAPI/Django、Gin、Laravel 等）、监听端口对外提供 HTTP/REST/WebSocket，即为 web / web api。

### 第 3 步：查找可直接 pull 的现成镜像

自己写 Dockerfile 之前，先判断有没有现成的官方镜像可用。判断方法见 `references/existing-image.md`。要点：

- 项目 README / 官网 / Docker Hub 是否提供官方镜像（如 `nginx`、`gitea/gitea`、`nextcloud`、很多开源系统都有官方镜像）。
- 项目是否自带 `docker-compose.yml` 直接引用了现成镜像。
- 若有可信现成镜像 → 直接用它组 compose，跳到第 4 步的"用现成镜像"分支，**不写 Dockerfile**。
- 若没有（大多数需要从源码构建的项目）→ 进入自建 Dockerfile 分支。

### 第 4 步：编写启动配置

根据前面的判断，选一条分支：

**分支 A — 项目自带 compose**：优先复用。按需只改端口冲突、卷路径、把外部依赖（数据库等）补成 compose 内的 service，保证一键起。

**分支 B — 用现成镜像**：写一份 `docker-compose.yml`，`image:` 指向现成镜像，配好端口、卷、环境变量、依赖服务。

**分支 C — 自建 Dockerfile**：参照 `references/dockerfile-patterns.md` 按语言写多阶段构建 Dockerfile。若项目需要多个服务（后端 + 数据库 + Redis + 前端等），再写 `docker-compose.yml` 编排，用 `depends_on` + 健康检查控制启动顺序。

**多服务编排通用要点**（分支 A/B/C 皆适用）：

- 数据库/缓存等中间件用官方镜像作为独立 service，**不要装到宿主机**。
- 应用通过 service 名（如 `db`、`redis`）互连，不用 `localhost`。
- 数据持久化用**命名卷或项目内相对目录**，避免污染宿主机任意路径。
- **只把浏览器要访问的 Web 入口端口映射到宿主机**（`ports: ["3001:3000"]`）。postgres / redis / mysql / mq **不要**写 `ports` 到宿主，只留在 compose 内部网络。冲突时改宿主侧映射口，不要改容器内监听口。
- 所有 service 加 `restart: unless-stopped` 与健康检查。

产物统一放在**项目目录内**（或专门的 `.vuln-env/` 子目录），命名清晰，便于用户复用与清理。

### 第 5 步：启动环境

```bash
docker compose up -d --build       # 自建/编排
# 或
docker compose up -d               # 纯现成镜像
```

启动后：

- `docker compose ps` 确认所有 service 状态为 `running`/`healthy`。
- `docker compose logs -f <service>` 看应用日志确认无致命错误。
- 用 `curl` 或浏览器验证首页/健康端点可访问。

### 第 6 步：失败则回溯轮询修正

启动失败或健康检查不过时，**系统化排障**（不要瞎改），完整方法见 `references/startup-troubleshooting.md`。核心循环：

1. **读日志定位**：`docker compose logs` 找到真正的报错行（端口占用？缺环境变量？数据库连不上？依赖缺失？构建失败？）。
2. **定位根因层级**：是 Dockerfile 构建问题、compose 编排问题、还是应用配置问题。
3. **针对性改一处**，重试。一次只改一个变量，便于判断是否有效。
4. 循环直到所有 service `healthy` 且页面可访问。

设一个合理的尝试上限（如同一问题连续 5 次仍未解决），仍不行则把当前卡点、已试方案、日志摘要清晰汇报给用户，请求决策，不要无限空转。

### 第 7 步：返回访问地址并沉淀配置

环境跑通后：

- **返回访问地址**：在 Crucible 平台上不要猜宿主机 IP；最终对外地址由平台填成 `http://{宿主机IP}:{映射端口}`。独立使用时再写 `http://localhost:<port>`。如有后台/API 文档路径（如 `/swagger-ui`、`/admin`）一并给出；如有初始账号密码从代码/文档中找出并说明。
- **沉淀最终配置**：确认项目目录内留下的 `Dockerfile`、`docker-compose.yml` 是本次实际跑通的版本，清理调试过程中的废弃文件。
- **写一份简短启动说明**（可写入 `RUN_ENV.md`）：启动命令、访问地址、各 service 说明、数据目录位置、如何停止与清理（`docker compose down`，加 `-v` 清数据卷）。
- **写环境状态文件 `.vuln-env.json`**（位于 `<project_root>` 下）：记录访问地址、端口、`docker-compose.yml` 路径、初始账号/口令、实际 commit。该文件供跨会话复用——下次同项目可直接读取判定环境是否已启动，跳过重建。
- **提示清理方式**，让用户知道如何彻底移除环境、不留残留，践行"不污染本地"。

## 交付清单

环境跑通后项目内应包含：

```
<project>/
├── Dockerfile              # 分支 C 自建时；分支 A/B 可无
├── docker-compose.yml      # 多服务或用现成镜像时（本次实际跑通版本）
└── RUN_ENV.md              # 访问地址、账号、启停与清理说明
```

## 参考文件

| 文件 | 用途 |
|------|------|
| `references/web-detection.md` | 第 2 步：如何判定 web / web api，非 web 何时终止 |
| `references/existing-image.md` | 第 3 步：如何找可直接 pull 的现成镜像 |
| `references/startup-troubleshooting.md` | 第 6 步：启动失败的系统化排障与轮询修正 |
| `references/isolation.md` | 全程：不污染本地环境的具体做法与清理 |
| `references/project-detection.md` | 第 1 步：语言/框架/端口检测规则 |
| `references/dockerfile-patterns.md` | 第 4 步：各语言多阶段 Dockerfile 模板 |
