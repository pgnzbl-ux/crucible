---
paths: ["backend/app/**/*.py"]
---

# Crucible 性能规范

> 性能基线围绕**沙箱编排**与**Agent 流式响应**，参考 docs §2.5（host / dind 两种运行时）。

## 1. 异步优先

- 所有 I/O（DB / Redis / HTTP / Docker SDK）必须用 async 客户端
- 同步客户端（`requests` / `psycopg2`）禁止引入
- Celery 任务本体是同步的，但内部分发 IO 用 `asyncio.run` + 独立 loop（避免复用 worker loop）

## 2. DB 连接池

| 模式 | pool_size | max_overflow | poolclass |
|---|---|---|---|
| API 主进程 | 10 | 20 | 默认 |
| Celery worker | 1 | 0 | NullPool |

worker 用 NullPool 防"attached to a different loop"。

## 3. 沙箱生命周期

- 沙箱按需创建，**不**预热池（P0 阶段任务量小）
- 任务结束（无论成败）必须销毁容器，**不**复用
- 镜像预拉取：服务启动时 `docker pull crucible-agent-runner:base` 一次（避免首个任务延迟）

## 4. Redis Pub/Sub

- 一个长连接复用 `EventBus.publish` / `subscribe`，不要每个事件新建连接
- 订阅端用 `pubsub.get_message(timeout=...)` 轮询，避免阻塞 listen
- SSE 转发（待 P0-1）：每个客户端一个独立订阅 channel，客户端断开立即 `unsubscribe`

## 5. 报告生成

- 报告正文与证据**异步生成**（Celery），不阻塞 API 响应
- MinIO 上传用分片（boto3 默认已支持），单文件 > 5MB 必须分片
- 报告缓存：同 Task + 同 ReportVersion 命中直接返（防双跑重生成）

## 6. 前端性能

- 列表分页（默认 20 / 页），禁用"一次拉全表"
- 详情按需加载（Drawer / 独立路由）
- 长列表用虚拟滚动（antd `Virtual`）
- SSE 事件批量渲染（每 100ms flush 一次，避免高频 setState）

## 7. 监控埋点

- `prometheus-fastapi-instrumentator` 已挂（main.py）
- 自定义指标：`task_duration_seconds`、`agent_message_count`、`agent_runner_active_count`、`event_bus_publish_failures_total`
- Grafana 待补（P2-13）