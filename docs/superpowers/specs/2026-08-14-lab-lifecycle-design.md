# 靶场生命周期与管理设计

> 版本: v1.0 · 2026-08-14
> 状态: 已评审
> 定位: 把靶场从「每个验证任务一套、任务结束即拆」改成「同一项目同一 commit 共用一套、TTL 静默销毁、管理页按项目分组操作容器」。
> 覆盖并修正：`2026-08-12-platform-node-orchestration-design.md` 中「完成即 `cleanup_lab_containers` / `crucible-lab-{task_id}` / 取消与删除都 `compose down`」的靶场销毁约定。Agent-runner 容器仍按任务拆，不变。
> 阅读对象: 编排 / Lab 上下文 / 前端靶场管理页。

---

## 0. 已确认决策

| 项 | 决定 |
|---|---|
| 复用键 | 同一用户 + 同一 `project_id` + 同一 `commit_sha` 共用一套靶场 |
| 并发 | 第一个任务创建中，后来的任务只查状态、等待或复用，不另起一套 |
| TTL | 默认 1 小时；任务使用（env_ready / 复现打靶）或管理页查看/操作都刷新 `last_seen_at` |
| 到期动作 | 完全静默满 TTL **且** 无 queued/running 任务 → `compose down -v`，记录留 `expired`，可重建 |
| 任务结束 | **不**拆靶场；取消/删除任务只拆该任务的 agent-runner |
| 占用中操作 | 有 queued/running 任务使用该 lab 时，停止/启动/重建/销毁（含容器级）一律拒绝 |
| 落地 | 新 Bounded Context `lab` + `labs` 表；compose 项目名 `crucible-lab-{lab_id}` |
| UI | 独立「靶场管理」`/labs`，按源码项目分组，组内是 commit 靶场，再下是该 compose 的容器 |
| 操作 | 靶场级：停 / 起 / 重建 / 销毁；容器级：停 / 起 / 重启 / 删除。容器不单独 rebuild |

明确不做：跨用户共用、容器级 rebuild、TTL 设置页（先写死 3600 秒）。

---

## 1. 身份与数据

### 1.1 一套靶场

`Lab = (owner_id, project_id, commit_sha)`。唯一约束同这三项。

- `commit_sha` 来自节点 0 `source` 产出，节点 2 才能 acquire。
- 任务已有 `project_id`。若为空，Lab 服务按任务的 `project_address` 解析/确保 Project 后再 acquire，**禁止**退化成 `crucible-lab-{task_id}`。
- Docker Compose 项目名：`crucible-lab-{lab_id}`（小写）。标签继续能扫到：`com.docker.compose.project`。
- 配方与 compose 文件最终落在 **lab 目录** `{agent_runner_workdir_base}/labs/{lab_id}/`，不绑某个任务的 `host_workdir`。创建者仍把 `.vuln-env/` 写到 `{source_path}/` 下，平台复制到 lab 目录后再 `compose up`。

### 1.2 表 `labs`

| 列 | 说明 |
|---|---|
| id | UUID |
| owner_id | 用户 |
| project_id | 源码项目 |
| commit_sha | 40 位 SHA |
| status | 见 §1.3 |
| compose_project | `crucible-lab-{id}` |
| workdir | lab 目录绝对路径 |
| target_url | 就绪后的 `http://{宿主机IP}:{映射端口}` |
| compose_path | 相对 workdir，如 `.vuln-env/docker-compose.yml` |
| transport_shape / initial_creds | JSON，复现节点要吃 |
| last_seen_at | TTL 时钟 |
| ttl_seconds | 默认 3600 |
| creator_task_id | 当前/最近一次创建者 |
| error_message | `failed` 时原因 |
| created_at / updated_at | 常规 |

`tasks.lab_id` 可空 FK。`acquire` 无论创建、等待还是复用，**立刻**写入，等待者也算 live 占用，避免 TTL 在排队时误拆。

跨 Context：task / agent 只调 `LabService`，禁止直连 `labs` repository。

### 1.3 状态机

```
(无行 / failed / expired / destroyed)
        │ acquire 抢创建
        ▼
    creating ──成功──► ready
        │
        └──失败 / 创建者取消──► failed

ready ──管理页停止──► stopped ── start 或任务复用 start──► ready
ready / stopped ── TTL 到期且无 live 任务──► expired（down -v）
ready / stopped / failed / expired ──管理页销毁──► destroyed（down -v）
expired / destroyed / failed ── rebuild 或再次 acquire──► creating
```

`creating` 时其他任务只轮询。`stopped` 时后来的任务不跑 AI，只 `compose start`，起来后当 `ready`。

---

## 2. 节点 2：创建 / 等待 / 复用

6 个可视化节点不变。`env_ready` 仍会落一条 NodeRun。

进入节点 2（且 `is_web is True`）后：

1. `LabService.acquire(owner, project_id, commit_sha, task_id)`  
   - 无行或状态 ∈ {failed, expired, destroyed}：CAS 成 `creating`，本任务是创建者。  
   - 已是 `creating` 且创建者不是自己：进入等待。  
   - 已是 `ready`：复用。  
   - 已是 `stopped`：本任务负责 `compose start`，成功则 `ready` 并复用。

2. **创建者**  
   先 `download_recipe`。命中则落到 lab 目录，平台改宿主口（若占用）+ **一次** `compose up`；成功则跳过 AI，`upload_recipe`。未命中或该次 up/探活失败再进入现有 AI 配方 + 端口占用检查 + `compose up` 循环（max 5）；构建/探活失败立即回喂，不二次同一稿。compose `-p crucible-lab-{lab_id}`，文件在 lab workdir。成功：`status=ready`，写入 `target_url` / creds / compose_path，`touch()`，`upload_recipe`。失败：`status=failed`，`error_message` 记下。NodeRun 产出与现在相同。

3. **等待者**  
   轮询 lab 状态（建议 2s）。`ready` → 把库里的 `target_url` 等写入本节点产出并 `touch()`，**不**调用 AI、**不** `compose up`。`failed` 或创建者取消导致 `failed` → 等待者再 `acquire` 抢创建（同一轮 env_ready 内允许一次转创建，避免死等）。等待上限 = `agent_runner_timeout_seconds`（当前 1800s）；超时则本节点 failed（分支出口 C，可 retry）。

4. **复用产出**  
   NodeRun.output 含 `target_url`、`compose_path`、`initial_creds`、`transport_shape`，并加 `reused: true`（创建者为 false/缺省）。前端进度仍显示「靶场就绪」。

5. **创建者被取消**  
   只拆该任务 agent-runner，**不** `compose down` 整套 lab。将 lab 标 `failed`（若仍是 `creating` 且 `creator_task_id` 匹配）。等待者按上条转创建。若 `compose up` 仍在跑，创建失败路径由新创建者 `down` 后重来。

6. **任务成功 / 失败 / 删除**  
   不拆 lab。删除任务若它是唯一 live 占用者，只清 `tasks.lab_id`，lab 继续走 TTL。

复现节点每次开始打 `target_url` 时 `LabService.touch(lab_id)`，并 `align_runtime_status`：若容器已在跑而库仍是 `expired`/`stopped`，回写 `ready`。

---

## 3. TTL 与巡检

**刷新 `last_seen_at`（touch）**

- acquire 成功（创建开始、复用、stopped→start）
- 复现节点开始执行
- `GET /api/v1/labs/{id}`
- 任何靶场/容器管理写操作

**销毁条件（须同时满足）**

- `now - last_seen_at >= ttl_seconds`
- 没有 status ∈ {pending, queued, running} 且 `lab_id` 指向该 lab 的任务
- lab.status ∈ {ready, stopped}

动作：`docker compose -p crucible-lab-{id} down -v`，status=`expired`。

终态补偿巡检 `cleanup_terminal_runtimes`：`expired` 但 compose 仍有 running 容器时，回写 `ready` 并刷新 TTL，**不** `down`（AI 复现已把靶场再次拉起）。`failed`/`destroyed` 仍 best-effort `down`。

**僵死 creating**

巡检发现 `creating` 且没有 live 任务占用该 lab（创建者已死/取消未写成 failed）：标 `failed`，best-effort `compose down`。

**相对现状的巡检变化**

现有 5 分钟巡检「非 live 任务的 `crucible-lab-{task_id}` 一律拆」**废止**。改为：

- agent-runner：仍按 `crucible.task_id` 拆已结束任务的 runner。
- 靶场：只拆 TTL 到期或 `expired`/`destroyed`/`failed` 僵死的 `crucible-lab-{lab_id}`。
- 历史残留 `crucible-lab-{task_id}`（无对应 labs 行）：仍当孤儿拆掉，避免升级后垃圾堆积。

取消 API：提交 cancelled 后立刻返回；后台只拆 agent-runner，不再 `compose down` 靶场。

---

## 4. 管理 API

前缀 `/api/v1`，需登录，只返回 `owner_id = 当前用户` 的 lab。

占用拒绝：该 lab 存在 live 任务时，下列写操作 **409**，body 为 `detail: {code, message, task_ids}`，其中 `code=LAB_IN_USE`。

| 方法 | 路径 | 行为 |
|---|---|---|
| GET | `/labs` | 按 `project_id` 分组。每组含项目名、labs[]（status、sha 短号、target_url、ttl 剩余秒、容器摘要）。**status 按 compose 实际容器校正并回写**：`expired`/`stopped` 且有 running 容器 → `ready`；无 live 任务时 `ready` 全停 → `stopped`、无容器 → `expired`。`creating`/`failed`/`destroyed` 不校正。 |
| GET | `/labs/{id}` | 详情 + `docker ps` 过滤该 compose 项目的容器列表（name、status、ports、image）。与列表相同，按容器实际状态校正并回写 `status`。 |
| POST | `/labs/{id}/actions/stop` | `compose stop` → `stopped` |
| POST | `/labs/{id}/actions/start` | `compose start` → `ready`，touch |
| POST | `/labs/{id}/actions/rebuild` | `creating` → `compose up -d --build` → `ready` 或 `failed` |
| DELETE | `/labs/{id}` | `down -v` → `destroyed` |
| POST | `/labs/{id}/containers/{name}/actions/stop` | `docker stop` |
| POST | `/labs/{id}/containers/{name}/actions/start` | `docker start` |
| POST | `/labs/{id}/containers/{name}/actions/restart` | `docker restart` |
| DELETE | `/labs/{id}/containers/{name}` | `docker rm -f` |

`{name}` 必须属于该 lab 的 compose 项目，否则 404。GET 详情即 touch。

列表分组 JSON 形状：

```json
{
  "items": [
    {
      "project_id": "...",
      "project_name": "claudecodeui",
      "labs": [
        {
          "id": "...",
          "commit_sha": "abc...",
          "status": "ready",
          "target_url": "http://10.0.0.8:3001",
          "ttl_remaining_seconds": 2400,
          "live_task_count": 0,
          "containers": [{ "name": "...", "status": "running", "ports": "3001->3000" }]
        }
      ]
    }
  ]
}
```

---

## 5. 前端

- 路由 `/labs`，侧栏「靶场管理」，与任务/源码/报告并列。
- 交互对标 Docker Desktop：项目为可展开组 → 该项目下每个 commit 一套靶场 → 展开容器。
- 靶场行：状态、短 SHA、可点击的 `target_url`、TTL 倒计时、停/起/重建/销毁。
- 容器行：停/起/重启/删除。
- live 占用时按钮 disable，tooltip 说明先取消任务。
- 任务详情仍展示节点 2 的 `target_url`，不在任务页做容器操作。

---

## 6. 失败与边界

| 情况 | 行为 |
|---|---|
| 有 live 任务时管理操作 | 409，不改 Docker |
| 等待创建超时 | 本任务 env_ready failed，lab 不由等待者标 failed |
| 库 `ready` 但 docker 已无该项目 | acquire/start/rebuild 视为过期：标 `expired` 后允许重新创建 |
| 库 `expired`/`stopped` 但容器已在跑 | 列表/详情/复现 `align_runtime_status` 回写 `ready`，管理页不再卡在 expired |
| lab 目录或 compose 文件缺失 | rebuild 先从 MinIO 拉配方再 `compose up`；仍无则 400。start 不能只靠缺失文件硬起。创建者路径仍先 MinIO 再决定是否 AI |
| 端口占用 | 仍只发生在**创建者**写配方时；复用不改端口 |
| 无 project_id | 按 git_url 确保 Project，失败则 env_ready fail-fast |
| Mock（SDK 关闭） | 不写 `labs` 行、不碰 Docker；管理页不出现 mock 靶场。节点 2 仍返回占位 `target_url`（现有行为） |

Fail-fast：禁止吞掉 docker 错误；禁止两个 `creating` 并行 `compose up` 同一复用键（唯一约束 + CAS）。

---

## 7. 测试（先失败再实现）

1. 同 owner+project+SHA，第二任务 env_ready **不**调用 `docker_compose_up`，产出 `reused: true` 且 target_url 与第一套相同。
2. 第一任务 `creating` 时第二任务只轮询；第一套变 `ready` 后第二套复用。
3. 任务 completed **不**调用 lab `compose down`。
4. `last_seen_at` 超 TTL 且无 live 任务 → 巡检 `down -v`，status=`expired`。
5. 有 running 任务时 DELETE lab / stop → 409。
6. 创建者 cancel → lab 从 `creating` 变 `failed`；等待者随后可抢创建。
7. `GET /labs` 按 project 分组，容器挂在对应 lab 下。

---

## 8. 对既有文档的修正点（实施时同步）

- 编排 spec §3.1 末尾 `cleanup_lab_containers()`：成功/失败只拆 agent-runner，靶场改走 TTL。
- 编排 spec 取消路径：去掉 `compose down` 靶场。
- `.claude/api-contract.md`：取消/删除不再拆靶场；新增 Lab API。
- `runtime_cleanup` 巡检：靶场改认 `crucible-lab-{lab_id}` + TTL，而不是「任务非 live 就拆」。
- `env_ready` 的 `-p` 从 `task_id` 改为 `lab_id`。
- `env-builder.md`：创建者仍把配方写到 `{source_path}/.vuln-env/`，平台复制到 lab workdir；等待/复用轮次不会进 AI。
