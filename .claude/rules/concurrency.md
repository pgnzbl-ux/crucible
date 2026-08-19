---
paths: ["backend/app/**/*.py"]
---

# Crucible 并发与异步规范

> 项目特点：Celery + agent-runner 容器 + Redis Pub/Sub 三方并发协作，参考 docs §2.5。

## 1. Celery 配置

- `task_acks_late=True`：任务执行完才 ack，崩溃可重派
- `task_reject_on_worker_lost=True`：worker 崩溃时任务重新入队
- `worker_prefetch_multiplier=1`：每个进程一次只预取一条长任务，避免饿死（Linux prefork 下仍可多进程并行）
- `task_soft_time_limit` + `task_time_limit`：软超时先发信号，硬超时强制 kill
- Windows：`--pool=solo`（实际并行=1）；Linux：`--pool=prefork --concurrency=$AGENT_RUNNER_CONCURRENCY_LIMIT`
- 任务并行软上限：表 `platform_settings.max_concurrent_tasks`（仅 Linux 设置页可改，默认 1；Windows solo 实际并行=1）。Worker **先抢 Redis 槽 `crucible:running_run_ids` 再 claim**；无槽则 `retry(countdown=15)`，保持 `queued`
- Redis 淘汰策略：`noeviction`（禁止 LRU 丢掉 Celery 未 ack 任务）
- `broker_transport_options.visibility_timeout` ≥ 任务硬超时 + 300s
- 取消：先提交 cancelled 并立刻返回 HTTP；后台 `schedule_teardown_task_runtime`（先拆 agent-runner 再 compose down）。删除 / 任务结束仍可同步 `teardown_task_runtime`。
- 孤儿巡检：Celery `worker_ready` 启动守护线程，每 5 分钟扫一次 Docker 容器；多进程用 Redis `crucible:runtime_sweeper` SET NX 互斥，并调和运行槽。活着且未超时的 running 任务不误杀。残留 host_workdir / compose.yml 不算运行时。年龄按 UTC 计算（`utc_unix`），避免 SQLite naive datetime 在东八区被 `.timestamp()` 算超龄。

## 2. asyncio 三件坑

| 坑 | 解决 |
|---|---|
| Celery worker 复用主进程 async engine 报"attached to a different loop" | worker 用独立 NullPool engine（`agent/tasks.py::_worker_engine`） |
| Celery 任务本体同步却要调 async 代码 | 用 `asyncio.run()` 临时 loop，不要 `loop.run_until_complete`（已废弃） |
| Redis asyncio 客户端绑死已关闭的 loop | 运行槽 `task_slots.py` 禁止进程级缓存客户端；每个 `asyncio.run` 新建并 `aclose` |
| `await` 跨进程 / 跨线程 | 不要 await 跨边界；Celery 任务内 async 代码必须包在 `asyncio.run` |
| 孤儿巡检 naive UTC 当成本地时 | `utc_unix()` 再算 age；否则东八区会把刚启动的 running 判超龄并拆掉 |
| agent-runner 流式回调要在同步线程内落 DB | 独立 session + `run_coroutine_threadsafe`；已在事件循环线程则 `create_task`，禁止 `.result()` 死锁 |

## 3. 资源锁

- 同一用户高频创建任务 → `asyncio.Semaphore`（API 层）或 Redis 分布式锁
- LLM Provider 激活切换（"激活唯一性"）→ DB 事务 + `SELECT ... FOR UPDATE`（PostgreSQL）
- agent-runner / 任务并行硬顶：`AGENT_RUNNER_CONCURRENCY_LIMIT` 默认 4、合法 1–8（设置项上限 + Linux worker 进程数）

## 4. Redis Pub/Sub

- 一个进程一个长连接
- 订阅端用 `get_message(timeout=1.0)` 轮询，**不要** `listen()` 阻塞（Celery worker 不能阻塞）
- 异常断连自动重连（`pubsub.connection` 监听）

## 5. SSE（待 P0-1）

- 每客户端一个独立 Redis 订阅
- 客户端断开立即清理订阅（防 Redis 连接泄漏）
- 心跳 15s 防代理超时

## 6. 沙箱超时

- 软超时（`task_soft_time_limit`）：优雅收尾（关闭 Agent）
- 硬超时（`task_time_limit`）：强制 `revoke(terminate=True)` + `container.stop()`
- 容器层 OOM：Docker 自动杀，worker 看 `State.OOMKilled` 标 failed