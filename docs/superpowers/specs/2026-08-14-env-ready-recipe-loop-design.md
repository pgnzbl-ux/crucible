# 靶场配方闭环与 MinIO 复用设计

> 版本: v1.0 · 2026-08-14
> 状态: 已评审
> 定位: 纠正 env_ready「AI 在 runner 里装依赖 / 试跑」的偏离，把节点 2 收成「只读分析 → 写配方 → 宿主机 compose → 失败改配方」；已跑通的配方按复用键存 MinIO，下次同 SHA 不再分析。
> 覆盖并修正：`2026-08-14-lab-lifecycle-design.md` §2 创建者「每次 creating 都进 AI」；`2026-08-12-platform-node-orchestration-design.md` §3.3 循环入口（增加配方命中短路）；插件 `run-project-env` 在平台上的职责切分。
> 阅读对象: 编排 / Lab / agent-runner 工具策略 / env-builder。

---

## 0. 已确认决策

| 项 | 决定 |
|---|---|
| AI 职责 | 只读源码与画像，写出/修改 Dockerfile 与 compose；**禁止**在 agent-runner 内安装依赖或启动靶场 |
| 依赖安装 | 只写进 Dockerfile / compose 服务镜像（`RUN npm ci`、`pip install`、中间件用官方 image） |
| 启动与探活 | 宿主机 worker：`docker compose -p crucible-lab-{lab_id} up -d --build` + Web 口探活 |
| 失败 | `compose up` 或探活失败 → **立即**把日志回喂 AI 改配方，不拿同一份配方再 up 一轮 |
| 持久化 | MinIO **只存配方文件**（`.vuln-env/` + 元数据 JSON），不存构建镜像 tar |
| 复用键 | 与 Lab 相同：`owner_id + project_id + commit_sha`；不跨用户 |
| 配方命中 | 创建者先拉 MinIO → 落到 lab 目录 → compose up；成功则跳过 AI |
| 端口占用 | 配方命中后若宿主口被占，**平台改 host 侧映射**，不调 AI；改不了或 up/探活仍失败才进 AI |
| Mock | SDK 关闭时仍不写 labs、不碰 Docker、不传 MinIO 配方 |

明确不做：跨用户共用配方、把镜像 layer 打 tar 进 MinIO、TTL 设置页、给 AI 挂 docker.sock、新的 Lab HTTP API。

---

## 1. 问题与目标

### 1.1 现状偏离

平台设计早已是「AI 出配方、worker 执行 compose」。实际创建者仍经常在 `crucible-agent-runner` 里 `npm install` / 装运行时，因为：

- 插件 skill `run-project-env` 按本地 Claude 写：同一个 agent 分析、写配方、**自己** `compose up`。
- `isolation.md` 说「依赖装在容器内」——AI 已经在容器里，会把 runner 当成靶场。
- runner 镜像带了 Node/npm；Bash 黑名单不拦 `npm`/`pip`/`apt`。
- Lab TTL 到期后再次 acquire 走 `creating`，**总是重新跑 AI**。MinIO 只缓存源码，不缓存已验证配方。

### 1.2 目标闭环

```
创建者 acquire → creating
    │
    ├─ MinIO 命中配方 ──► 落到 lab 目录 ──►（必要时改宿主口）──► compose up + 探活
    │                         │ 成功 → ready，结束（不调 AI）
    │                         └ 失败 → 日志作为 previous_error，进入 AI 循环 attempt=1
    │
    └─ 未命中 ──► AI 循环 attempt=1
                    只读分析 → 写 {source_path}/.vuln-env/ → submit_result
                    → 平台拷到 lab 目录 → compose up + 探活
                    失败 → down + 日志回喂，最多 5 轮
                    成功 → ready + 上传 MinIO（覆盖同 key）
```

live `ready` 复用、`stopped` 的 `compose start`、等待者轮询：**不变**，不读 MinIO。

---

## 2. MinIO 配方对象

### 2.1 存储

| 项 | 值 |
|---|---|
| Bucket | `crucible-lab-recipe`（不存在则创建，与 `crucible-source` 并列） |
| Object key | `recipe/{owner_id}/{project_id}/{commit_sha}.tar.gz` |
| 内容 | 目录 `.vuln-env/`（Dockerfile、compose、`.dockerignore`、init SQL、辅助脚本等）+ 根级 `recipe-meta.json` |
| 元数据 JSON | `compose_path`、`transport_shape`、`initial_creds`、`started_containers`。**不含** `target_url`（宿主 IP / 映射口每次重算） |

不新增业务表。key 由复用键推导，不写 `labs` 新列。

Lab Context 自管存取（`lab/recipe_store.py`，模式对齐 `project/source_cache.py`）。task / agent 只经 `LabService`，禁止直连 MinIO 客户端。

### 2.2 上传

时机：创建者路径 **探活成功、即将 `mark_ready` 之前**（含：配方命中后一次 up 成功；AI 循环某轮成功）。同 key 覆盖。

失败：节点仍标 `ready`（靶场已可用）。打 error 日志，不阻塞任务。下次 creating 若拉不到则再走 AI。

打包源：lab workdir 里实际跑通的 `.vuln-env/`，不是任务 workspace 里可能过期的副本。

### 2.3 下载

时机：创建者 `role==create` 且即将决定是否调 AI 之前。

| 结果 | 行为 |
|---|---|
| 对象不存在 | 视为未命中，进 AI 循环 |
| 下载/解压异常 | 视为未命中，打 warning，进 AI 循环（不因 MinIO 抖动把节点判失败） |
| 解压后无 compose 文件 | 视为未命中 |
| `recipe-meta.json` 缺失或非法 | 仍算命中；`compose_path` 默认 `.vuln-env/docker-compose.yml`，`transport_shape`/`initial_creds`/`started_containers` 用空值 |
| 成功 | 写入该 lab 的 `workdir`，使 `{workdir}/.vuln-env/` 可用 |

解压不得写到任务 `host_workdir` 之外的其它 lab。

---

## 3. 创建者执行顺序

`EnvReadyNode` 在 `acquire` 得到 `create` 之后：

1. `LabService.download_recipe(owner_id, project_id, commit_sha, dest_workdir=lab.workdir)`  
2. **命中**  
   1. 读 compose，解析 Web 宿主口；与 `occupied_host_ports` 求交。  
   2. 有冲突：平台只改 compose **host 侧**端口（不改容器内监听口、不调 AI）。无法改写则把冲突列表当作 `previous_error`，转步骤 3。  
   3. `compose up --build` + 探活。  
   4. 成功：用 `recipe-meta.json` 的 creds/shape + 平台重算的 `target_url` → `mark_ready` → `upload_recipe`（端口若被平台改过，以磁盘最终稿为准）→ 节点结束，`reused` 语义见 §5。  
   5. 失败：`compose down`（仅该 `lab_id`），`previous_error` = up/探活日志，**立即**进入步骤 3 的 AI 循环，`attempt` 从 1 计。不拿同一份未改配方再 up。  
3. **未命中，或命中后失败/无法改端口**  
   现有 max-5 AI 循环（写配方 → 端口占用检查 → 拷到 lab → up → 探活）。成功则 `mark_ready` + `upload_recipe`。5 轮全失败则 lab `failed`，分支出口 C。

AI 循环里「配方宿主口已被占用 → 不起 compose、回喂 AI」保持不变（AI 自己写的稿由 AI 改口）。仅 **MinIO 命中稿** 走平台改口。

---

## 4. AI 与 runner 约束

### 4.1 职责（env-builder）

- 做：读 README / 依赖清单 / 路由与鉴权配置 / 已有 Dockerfile，先判断项目是否存在登录功能，再按语言把 **Node/JDK/Python/中间件** 写进配方。无登录功能（如公开 dashboard）明确交 `{auth_required:false,note}`；有登录功能则优先复用已有预设账号。若没有预设账号，但项目已有环境变量、seed/init 脚本等安全初始化机制，可仅修改 `.vuln-env` 来创建靶场专用账号并交回实际账密；无法安全初始化则用非空 `note` 说明需自行注册、API Key 或其他前置条件。  
- `submit_result` 交 `compose_path`、占位 `target_url`，以及 **必填** `initial_creds`（三态之一：实际可用账密 / 确认无登录功能的 `auth_required=false` / 非空 `note` 说明无法自动提供凭据）。  
- 不做：`npm`/`pip`/`apt`/`docker`；宣称已启动；web 门禁（节点 1）；git clone（节点 0）；修改项目业务源码；编造未被配方真正初始化的账号；交空对象 `{}` 冒充「无需登录」。  
- `credential_lookup_only=true` 时靶场已运行，只允许只读判断和查找，不得为补凭据修改配方或重启靶场。  
- `attempt>1`：只根据 `previous_error` 改一处。

插件 `run-project-env/SKILL.md` 增加「Crucible 平台」专章：在 env-builder 下 **第 5–7 步（启动、排障循环驱动、对人写 RUN_ENV）由平台执行**；agent 只执行分析与写/改配方。本地 Claude 单独使用 skill 时仍可走全文。`isolation.md` 在平台语境下明确：**agent-runner ≠ 靶场容器**，往 runner 里装依赖视为违规。

### 4.2 Bash 策略（仅 `NODE_KEY=env_ready`）

在现有破坏性命令黑名单之上，再拒绝（匹配命令词，不区分路径包装）：

`npm` `npx` `yarn` `pnpm` `pip` `pip3` `apt` `apt-get` `apk` `yum` `dnf` `docker`

仍允许：`node -e` / `python -c` 只读探测；`Read`/`Grep`/`Glob`/`Write`/`Edit`。

其它节点（audit/reproduce）策略不变。profile 继续无 Bash。

agent-runner 镜像可继续带 Node 供 `node -e`；**不**再把「镜像里有 npm」当成可以 `npm install` 的许可。

---

## 5. 与 Lab 生命周期的衔接

| 场景 | 配方 / AI |
|---|---|
| live `ready` 第二任务 | 不调 MinIO、不调 AI（现有 reuse） |
| `stopped` → start | 不调 MinIO；无容器则 expire 后走创建者（既有缺口） |
| `expired`/`failed`/`destroyed` 再 acquire | 创建者：先 MinIO 再决定是否 AI |
| 管理页 rebuild | 本地 compose 在则直接 up；**缺失则先 MinIO 拉回**；仍无则 400「缺少配方，请从验证任务重新创建」 |
| 任务结束 / TTL down | 不删 MinIO 对象 |
| 用户销毁 lab | 不删 MinIO（同 SHA 下次还能用）；不做配方 DELETE API |

创建者因 MinIO 命中而跳过 AI：NodeRun 仍在，产出含 `target_url` 等；`reused` 为 `true`（与 live 复用一样表示「没跑创建者 AI」）。前端步骤条仍显示「靶场就绪」。

---

## 6. 失败与边界

| 情况 | 行为 |
|---|---|
| 配方构建/应用失败（compose 日志、探活非 2xx） | 立即 AI 修，不重试同一稿 |
| docker 守护进程不可用、命令无法执行 | 节点失败，**不**进 AI（与「配方未命中」区分） |
| MinIO 下载失败 | 当未命中，走 AI |
| MinIO 上传失败 | lab 已 ready，只记日志 |
| 配方命中但宿主口占用 | 平台改 host 口；改不了再 AI |
| 无 project_id / 无 commit_sha | 与现 spec 相同，fail-fast |
| Mock | 不写 labs、不碰 MinIO 配方 |

Fail-fast：docker 守护进程错误仍抛，不装成「配方未命中」。

---

## 7. 测试（先失败再实现）

1. 创建者 MinIO 命中且 up 成功：`run_ai_turn` **不**调用；`docker_compose_up` 调用一次；产出有 `target_url`，`reused is True`。  
2. 创建者 MinIO 命中但 up 失败：不第二次 up 同一稿；随后 `run_ai_turn(attempt=1)` 且 `previous_error` 含失败日志。  
3. 创建者未命中：与现网相同，第一轮就 `run_ai_turn`。  
4. 探活成功后调用 `upload_recipe`，key=`recipe/{owner}/{project}/{sha}.tar.gz`，包内有 `.vuln-env/` 与 `recipe-meta.json`。  
5. `env_ready` 下 `npm install` / `pip install` / `docker compose` 被 Bash 钩子 deny；`node -e` 允许。  
6. rebuild 本地缺 compose、MinIO 有对象：先下载再 `compose_up_build`，不 400。  
7. Mock：`upload_recipe`/`download_recipe` 都不调用。

---

## 8. 对既有文档的修正点（实施时同步）

- 本文件为业务行为 SSOT；lab-lifecycle §2 创建者改为「先 MinIO 再 AI」。  
- 编排 spec §3.3 循环前增加配方命中短路；失败策略改为「立即回喂，不重复 up」。  
- `env-builder.md` + `run-project-env/SKILL.md` 平台专章。  
- `.claude/api-contract.md`：rebuild 缺本地文件时可从 MinIO 恢复；无新路由。  
- `docs/development-guide.md` 已完成清单：配方 MinIO 复用。
