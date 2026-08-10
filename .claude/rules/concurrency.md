---
paths: ["backend/app/**/*.py"]
---

# Crucible 并发与异步规范

> 项目特点：Celery + agent-runner 容器 + Redis Pub/Sub 三方并发协作，参考 docs §2.5。

## 1. Celery 配置

- `task_acks_late=True`：任务执行完才 ack，崩溃可重派
- `task_reject_on_worker_lost=True`：worker 崩溃时任务重新入队
- `worker_prefetch_multiplier=1`：单 worker 单任务，避免饿死
- `task_soft_time_limit` + `task_time_limit`：软超时先发信号，硬超时强制 kill
- Windows：`--pool=solo`（`run_worker.py` 已固定）
- 取消任务：`celery_app.control.revoke(task_id, terminate=True)` + agent-runner 容器销毁（`tasks.py::_on_sigterm` 钩子）

## 2. asyncio 三件坑

| 坑 | 解决 |
|---|---|
| Celery worker 复用主进程 async engine 报"attached to a different loop" | worker 用独立 NullPool engine（`agent/tasks.py::_worker_engine`） |
| Celery 任务本体同步却要调 async 代码 | 用 `asyncio.run()` 临时 loop，不要 `loop.run_until_complete`（已废弃） |
| `await` 跨进程 / 跨线程 | 不要 await 跨边界；Celery 任务内 async 代码必须包在 `asyncio.run` |
| agent-runner 流式回调要在同步线程内落 DB | 用 `asyncio.run_coroutine_threadsafe(_persist_event(), loop).result(timeout=5)` 跨入主 loop |

## 3. 资源锁

- 同一用户高频创建任务 → `asyncio.Semaphore`（API 层）或 Redis 分布式锁
- LLM Provider 激活切换（"激活唯一性"）→ DB 事务 + `SELECT ... FOR UPDATE`（PostgreSQL）
- agent-runner 容器并发上限：默认 4（防宿主 OOM，`settings.agent_runner_concurrency_limit`）

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