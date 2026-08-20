---
paths: ["backend/app/**/*.py"]
---

# Crucible 监控规范

> Prometheus 指标已在 `main.py` 挂载；本文件是日志与追踪约定。

## 1. 日志

- 结构化 JSON 日志（`structlog` 或 `python-json-logger`）
- 必带字段：`timestamp` / `level` / `logger` / `message` / `task_id` / `run_id` / `correlation_id`
- 凭据一律掩码（参考 `security.md` §6）
- 不要打印整个 payload，截前 256 字符

## 2. 追踪 ID 串联

- HTTP 入站：从 `X-Request-ID` header 取，无则生成 UUID4
- 跨 Celery 任务：作为 task `headers` 传入，worker 取出注入 contextvar
- 跨 Redis Pub/Sub 事件：放进 `Event.correlation_id`（`shared/events.py` 已有）

## 3. Prometheus 指标

最小集（`prometheus_client` Counter / Histogram / Gauge）：

| 指标 | 类型 | 标签 |
|---|---|---|
| `crucible_task_duration_seconds` | Histogram | `task_status` |
| `crucible_agent_messages_total` | Counter | `task_id`（高基数慎用，考虑去掉） |
| `crucible_agent_runner_active` | Gauge | `state` (running/oom/completed) |
| `crucible_event_bus_publish_failures_total` | Counter | `channel` |
| `crucible_http_requests_total` | Counter | `method`, `path`, `status` |

`task_id` 不要进 label，会爆 TSDB。用 `run_id` 或干脆不带。

## 4. Sentry（P2-13 待补）

- `SENTRY_DSN` 已预留 config 字段
- 错误事件必带 `task_id` / `run_id`
- 过滤掉 `SandboxError` 中"用户取消"分支（避免噪音）

## 5. 调试本地

- `LOG_LEVEL=DEBUG` 仅本地用，**生产禁止**
- 沙箱内 `claude` CLI 输出落库（`agent_events.payload`），不必打到 stdout