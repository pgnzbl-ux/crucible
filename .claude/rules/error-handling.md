---
paths: ["backend/app/**/*.py"]
---

# Crucible 错误处理规范

> 项目原则：**事件总线失败不允许影响主流程**；沙箱 / Agent 失败必须显式状态而非抛 500。

## 1. 三层错误模型

| 层 | 类型 | 处理 |
|---|---|---|
| 业务校验 | `ValueError` / Pydantic `ValidationError` | API 层转 400 + 字段级错误码 |
| 领域规则 | `PermissionError` / `ConflictError`（自定义） | API 层转 403 / 409 |
| 基础设施 | Docker / Redis / MinIO / LLM 异常 | API 层转 503 + 重试建议 |

## 2. 异常类（建议放 `shared/exceptions.py`）

- `CrucibleError` —— 业务异常基类，带 `code` + `message` + `http_status`
- `SandboxError` / `AgentError` / `EventBusError` —— 各自基类，方便上层 `except` 选择
- 自定义异常**不要**继承 `HTTPException`（那是 FastAPI 层的事）

## 3. API 层错误响应统一格式

```json
{
  "error": {
    "code": "TASK_NOT_FOUND",
    "message": "Task 123 not found",
    "details": { "task_id": "123" }
  }
}
```

由 `app.exception_handlers` 统一捕获 `CrucibleError`，未捕获异常兜底 500 + `INTERNAL_ERROR` + Sentry 上报。

## 4. 沙箱 / Agent 失败语义

| 场景 | 行为 |
|---|---|
| 容器创建失败 | 标记 task_run `failed`，reason="sandbox_create_failed"，**不重试** |
| Agent exit ≠ 0 | 标记 `failed`，reason 包含 exit code，evidence 落库（stderr） |
| Agent timeout | Celery `soft_time_limit` + `time_limit` 双兜底，强制 `revoke(terminate=True)` |
| Redis Pub/Sub 失败 | `try/except`，仅 WARN 日志，不影响任务推进 |

## 5. 日志规范

- 错误日志必须含 `task_id` / `run_id` / `correlation_id`（`shared/events.py` 提供）
- 凭据相关一律掩码（参考 `security.md` §7）
- 不要在异常信息里 dump 整个 payload，截前 256 字符

## 6. 与 validation / API 设计规则的边界

- **字段级错误** → `validation.md`
- **错误响应格式 / 状态码** → 本文件
- **API 设计契约** → `api-design.md` + `.claude/api-contract.md`