# 任务并行上限（设置项 + 运行槽）

> 版本: v1.0 · 2026-08-18
> 状态: 待确认
> 定位: 让平台同时跑多个验证任务，并在设置页配置「同时运行几个」。不拆 Celery 队列、不上 Redis 集群。
> 阅读对象: settings / agent worker / 前端设置页 / 部署。

---

## 0. 已确认决策

| 项 | 决定 |
|---|---|
| 并行语义 | **平台全局**同时 `running` 的验证任务数，不是每用户配额 |
| 可配范围 | `1 .. AGENT_RUNNER_CONCURRENCY_LIMIT`（环境硬顶，默认 **4**，允许 1–8） |
| 默认值 | **1**（与当前 Windows solo 实际行为一致，避免开发机突然双开靶场） |
| 热更新 | 改设置立即影响**新认领**；已 `running` 的不杀、不等到降到新上限才继续 |
| 准入 | Redis SET + Lua 原子抢槽；**先抢槽再 `claim_task_run`** |
| 无槽 | Celery `retry(countdown=15)`，任务保持 `queued`，不占 worker |
| Worker 池 | Linux：`--pool=prefork --concurrency=$AGENT_RUNNER_CONCURRENCY_LIMIT`；Windows：继续 `--pool=solo`（实际并行=1） |
| 队列 | **保持默认单队列**，不拆分析/清理队列 |
| Redis 拓扑 | 仍 db0 事件 / db1 broker / db2 result；**不**上集群 |
| Redis 淘汰 | `maxmemory-policy` 改为 **`noeviction`**（禁止 LRU 丢掉 Celery 未 ack 任务） |
| 权限 | 与 LLM Provider 相同：RBAC 落地前**任意已登录用户**可读写此全局项 |
| 靶场 | 不改 Lab 复用：同用户+项目+commit 仍一套；不同项目可并行多套 |
| 未执行配置 | `AGENT_RUNNER_CONCURRENCY_LIMIT` 从「写了没用的容器上限」改为本功能硬顶 |

明确不做：按用户隔离队列、K8s HPA、Redis Cluster、`--pool=threads`、无限并行、任务执行中途因降上限而被取消、本轮关闭 Celery result backend。

---

## 1. 问题

今天创建多个任务会全部进 Redis 排队，但 Celery worker 被 `run_worker.py` 固定为 `--pool=solo`，同一时刻只执行一条 `agent.run_analysis`。`--concurrency=2` 在 solo 下无效。

业务层**没有**全局互斥：`claim_task_run` 只禁止同一任务的第二条 run。Lab 已按「后来者等待/复用同一套靶场」设计。`AGENT_RUNNER_CONCURRENCY_LIMIT=4` 写在 `.env` / `config.py`，代码从未读取。

用户要的是：设置页能配「同时允许多少个任务」，改完不必重启就能收紧/放宽新任务准入。

---

## 2. 两层上限

```
环境硬顶 AGENT_RUNNER_CONCURRENCY_LIMIT   （默认 4，改它要重启 worker）
        │
        ▼
设置项 max_concurrent_tasks               （1..硬顶，默认 1，热更新）
        │
        ▼
有效并行 = min(max_concurrent_tasks, 实际 worker 进程数)
```

| 层 | 存哪 | 谁读 | 改了怎样 |
|---|---|---|---|
| 硬顶 + Linux 进程数 | `.env` `AGENT_RUNNER_CONCURRENCY_LIMIT` | API 校验上限；Linux `run_worker.py` 的 `--concurrency` | 必须重启 worker |
| 软上限 | 表 `platform_settings.max_concurrent_tasks` | worker 抢槽前每次读取 | 立刻作用于下一次抢槽 |
| Windows 进程数 | 代码写死 solo | 实际永远 1 | 设置页禁用修改并 Alert；不假装已并行 |

6 节点在**单个任务内串行**，因此 N 个并行任务 ≈ N 个 agent-runner 容器；不同项目还会各有一套 compose 靶场。硬顶默认 4 是防宿主机 OOM，不是性能调优。

---

## 3. 业务规则

编号可测。Worker / API / 前端必须服从。

**R1.** 平台同时处于 `running` 且已持有运行槽的验证任务数，不得超过当前 `max_concurrent_tasks`。

**R2.** `max_concurrent_tasks` 必须是整数，范围 `[1, AGENT_RUNNER_CONCURRENCY_LIMIT]`。越界 PUT → 422 `VALIDATION_FAILED`。

**R3.** 库中无行时，第一次 GET 插入默认 `max_concurrent_tasks=1`（get-or-create）。不得出现 0 行导致 worker 拒绝所有任务。

**R4.** 抢槽成功之前，不得把任务/run 从 `queued`/`pending` 改为 `running`。无槽时保持 `queued`，Celery 任务 `retry(countdown=15)`，`max_retries` 不设上限（直到用户取消、删除或任务被标失败）。

**R5.** 同一 `run_id` 重复抢槽（Celery 重投）必须幂等：SET 里已有该 `run_id` 视为已持有，不额外占一个名额。

**R6.** 分析正常结束、失败、取消、认领被拒，均须释放该 `run_id` 的槽。进程被杀未走 finally 时，巡检按 DB 调和：DB 不是 `running` 的 `run_id` 从 SET 删掉；DB 是 `running` 但不在 SET 的补回去。

**R7.** 调低上限时：已 `running` 的任务继续跑完；只拒绝新抢槽，直到当前持槽数 `<` 新上限。

**R8.** 调高上限时：正在 `retry` 等待的 `queued` 任务在下一次抢槽即可进入，不必重启 worker。

**R9.** 用户取消/删除仍按现契约：先提交 `cancelled`/`archived`，再 revoke + 拆该任务 agent-runner。等待槽位中的 Celery retry 醒来后发现不可认领 → 释放槽（若有）并忽略，不得改回 `running`。

**R10.** 不因并行改变 Lab 复用键、TTL、CAS「禁止两套 creating」。同项目同 commit 第二任务仍等待/复用；不同项目允许同时各起靶场。

**R11.** 单队列、单任务名 `agent.run_analysis` 不变。禁止为本功能新增 Celery 队列或路由。

**R12.** Redis `maxmemory-policy` 必须是 `noeviction`。内存满时拒绝写入并告警，不得 LRU 淘汰 broker key。

---

## 4. 数据

### 4.1 表 `platform_settings`

单行表（settings Context）。用 `singleton_key='default'` 唯一约束防止插出第二行。

| 列 | 类型 | 约束 | 说明 |
|---|---|---|---|
| id | UUID | PK | 沿用 `BaseModel` |
| singleton_key | `String(20)` | UNIQUE NOT NULL，默认 `default` | 单例键 |
| max_concurrent_tasks | `Integer` | NOT NULL，默认 1 | 软上限 |
| created_at / updated_at | timestamptz | 常规 | |

无其它平台运行时列。禁止做成通用 KV「以备将来」。

开发：`init_db()` 的 `create_all` 会建表。已有库：增量 Alembic revision（`down_revision=b7e4c2a19f08`）。Worker 的 `tasks.py` 必须 import 该模型以注册 metadata。

### 4.2 Redis

| Key | 类型 | 用途 |
|---|---|---|
| `crucible:running_run_ids` | SET | 成员 = 当前持槽 `run_id` |
| `crucible:runtime_sweeper` | STRING | 巡检互斥，`SET NX EX 240` |

抢槽 Lua（原子）：

1. 若 `SISMEMBER key run_id` → 返回 1（重投幂等）
2. 若 `SCARD key >= max` → 返回 0
3. `SADD key run_id` → 返回 1

释放：`SREM`。`max` 每次从 DB 读取后传入 Lua，不把上限缓存在 Redis（避免设置页改了 worker 仍读旧值）。

频道 / db 编号不变：事件仍 db0，broker db1，result db2。

### 4.3 环境变量

`AGENT_RUNNER_CONCURRENCY_LIMIT`：整数，代码默认 4；合法 1–8。大于 8 的 `.env` 值启动时校验失败（Fail-Fast）。含义改为：

- PUT 设置项的上限
- Linux worker `--concurrency`

不新增 `WORKER_CONCURRENCY`。Windows 忽略该值作为进程数（solo 永远 1），但仍用它作为设置项可配上限（方便同一套 `.env` 在 Linux 上生效）。

Celery：`broker_transport_options.visibility_timeout` 显式设为 `max(3600, agent_runner_timeout_seconds + 300)`，避免长任务被 broker 当成丢失再投。

---

## 5. API

前缀 `/api/v1`，需 Bearer JWT。错误信封与现网一致。

### GET `/api/v1/settings/runtime`

无行则插入默认 1 再返回。

```json
{
  "max_concurrent_tasks": 1,
  "max_allowed": 4,
  "worker_pool": "solo"
}
```

| 字段 | 来源 |
|---|---|
| `max_concurrent_tasks` | DB |
| `max_allowed` | `settings.agent_runner_concurrency_limit` |
| `worker_pool` | API 进程按 `sys.platform`：`win32` → `"solo"`，否则 `"prefork"`。这是**部署约定的只读提示**，不是探测正在跑的 worker |

`worker_pool` 不保证与运维手工启动的 celery 命令一致；UI 用它做文案，不当成强校验。

### PUT `/api/v1/settings/runtime`

```json
{ "max_concurrent_tasks": 2 }
```

校验：整数、`1 <= n <= max_allowed`。成功返回与 GET 相同形状。不接受其它字段（`extra=forbid`）。

错误：

| HTTP | code | 何时 |
|---|---|---|
| 401 | `UNAUTHENTICATED` | 未登录 |
| 422 | `VALIDATION_FAILED` | 缺字段 / 非整数 / 越界 |
| 401/403 | 与现网设置接口相同 | 本轮不单开 admin |

---

## 6. Worker

`run_analysis` 顺序：

1. 若任务不可认领（已取消/归档/终态）→ 直接返回，不抢槽
2. 读 `max_concurrent_tasks`；Lua 抢槽
3. 失败 → `self.retry(countdown=15)`（Retry 异常必须在未 claim 时抛出）
4. 成功 → `claim_task_run`；认领失败 → 释放槽并返回
5. 跑 6 节点编排（现逻辑不变）
6. `finally`：若本执行持有槽则释放

禁止在任务函数里 `sleep` 等槽：那会占死 Celery 进程。只能 retry 把进程让出去。

Linux `run_worker.py`：`--pool=prefork --concurrency={agent_runner_concurrency_limit}`，去掉无效的 solo+concurrency=2 组合。Windows：`--pool=solo`，不要传大于 1 的 concurrency 假装能并行。

`worker_ready` 巡检：多 prefork 子进程可能都连信号。抢 `crucible:runtime_sweeper` 成功的那一方才扫；顺带调和运行槽（R6）。未抢到锁则跳过本轮。

取消：现有 SIGTERM 钩子在拆 runner 之外，必须释放槽。API 侧 teardown 后由巡检兜底即可，但 worker 正常 finally 是主路径。

---

## 7. 前端

设置页 `SettingsPage` 增加第三个 Tab：**运行**。

- 控件：`InputNumber`，min=1，max=`max_allowed`，绑定 `max_concurrent_tasks`；仅 `worker_pool === 'prefork'`（Linux）可编辑
- 保存：PUT，成功 toast；失败用现有 `useErrorToast`
- `worker_pool === 'solo'` 时禁用输入与保存，并用页面内 `Alert`（可持续阅读，不是 toast）说明：同时运行任务数仅 Linux 可配置；Windows 同一时刻只跑 1 个任务，把数字调到 2 也不会并行
- 副文案：Linux 写清改数字立即作用于排队中的任务、不中断已在跑的任务；Windows 写清本项仅 Linux 可配置
- PageHeader subtitle 改为同时覆盖「模型 / 凭据 / 并行」

类型与请求加在 `frontend/src/shared/lib/api.ts`。面板放到 `frontend/src/features/settings/RuntimePanel.tsx`（页面已偏长，新 UI 不继续堆进 `SettingsPage.tsx` 函数体）。

不在任务列表上画「槽位 x/y」仪表（本轮不做）。

---

## 8. 资源与失败

并行的真实瓶颈是 Docker 与 LLM，不是 Redis。

| 资源 | 行为 |
|---|---|
| 每 runner | 仍 1 CPU / 1GB（现 `AgentRunnerSpec`） |
| 靶场 | 不同项目各一套 compose；可能再占数 GB |
| LLM | 仍共用默认 Provider；429 按现节点失败/重试，本轮不加全局 LLM 限流 |
| Redis 满 | `noeviction` 下写入失败 → Fail-Fast，任务按现基础设施错误处理（503 / failed），禁止静默丢任务 |
| 抢槽 Redis 失败 | Fail-Fast：本轮 Celery 执行失败并 retry；不得假装获得槽 |

---

## 9. 兼容

- 已有 `queued` 任务：worker 升级后按新逻辑抢槽，无需数据迁移
- 已有 `running`：不在 SET 里 → 巡检第一次会 `SADD` 补上，避免超卖
- 设置表空：GET 或 worker 首次读都会 get-or-create 为 1
- 前端旧 Tab 不变

回滚：代码回退 + Alembic downgrade 删表；Redis SET 可 DEL；compose 策略改回不影响功能，只恢复误杀风险。

---

## 10. 验收

| # | 场景 | 期望 |
|---|---|---|
| A1 | 默认新库 | GET 返回 `max_concurrent_tasks=1`，`max_allowed=4` |
| A2 | PUT 2 后连续创建 3 个不同项目任务（Linux prefork≥2） | 2 个 `running`，1 个保持 `queued` 直至有空槽 |
| A3 | 同项目同 commit 第二任务 | 仍复用 Lab，不另起 creating（现 Lab 契约） |
| A4 | 运行中把上限 2→1 | 两个 running 都不被取消；第三个继续 queued |
| A5 | 运行中把上限 1→2 | queued 在 15s 内可被抢槽进入 running（worker 进程数≥2） |
| A6 | 取消一个 queued（正在 retry 等槽） | 保持 cancelled，醒来不改 running |
| A7 | PUT 0 / 9 / `"x"` / 超过 `max_allowed` | 422 |
| A8 | Windows solo | 设置页禁用修改并 Alert；即便库中为 2，同时只有 1 个 running |
| A9 | `docker-compose.yml` Redis | 命令含 `noeviction`，不含 `allkeys-lru` |
| A10 | 单测 | Lua 满员拒绝、同 run_id 重投幂等、get-or-create 默认 1、claim 前无槽不改 running |

冒烟（P0 行为）：Linux 或「两个 solo 进程抢同一队列」下，两个任务可同时 `running`。Windows 单 solo 不作为并行冒烟环境。

---

## 11. 文档同步（实现时）

- `.claude/api-contract.md`：Settings 增加 runtime GET/PUT
- `.claude/rules/concurrency.md`：solo 仅 Windows；Linux prefork；抢槽；Redis `noeviction`；执行 `AGENT_RUNNER_CONCURRENCY_LIMIT`
- `docs/development-guide.md`：worker 启动与并行说明
- `backend/.env.example`：注释改为硬顶含义
- 本文件状态改为「已确认」仅在实现合并后

---

## 12. 非目标

- 按用户 / 按项目的并发配额
- 设置页配置 Celery 队列名、prefetch、超时
- 动态改 Linux `--concurrency` 而不重启
- 多 LLM Provider 并行分流
- 任务列表实时「槽位占用」看板
- 修改 6 节点编排顺序或 Lab 状态机
