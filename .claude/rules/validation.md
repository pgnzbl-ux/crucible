---
paths: ["backend/app/**/*.py"]
---

# Crucible 数据校验规范

> 双层校验：Pydantic v2 schema（API 边界）+ 领域规则（service 层）。前端校验仅 UX。

## 1. 边界分层

| 层 | 工具 | 失败时 |
|---|---|---|
| HTTP 入参 | FastAPI `Depends` + Pydantic schema | 自动 422 + 字段级错误 |
| 业务规则 | service 显式 `raise` + 自定义异常 | API 层转 409/422/403 |
| ORM 落库 | SQLAlchemy 约束 + `IntegrityError` 捕获 | API 层转 409 |

## 2. Pydantic v2 写法

- 用 `model_config = ConfigDict(from_attributes=True, extra="forbid")`
- `extra="forbid"` 防止字段穿透
- 时间统一 `datetime` ISO-8601 + UTC；序列化时显式 `.isoformat()`
- ID 用 `UUID4`（项目 `BaseModel` 已统一）
- 状态机字段用 `Enum`（`Literal` 不够时用 `StrEnum`）

## 3. 字段长度 / 格式硬约定

| 字段 | 约束 |
|---|---|
| `users.username` | 3-32 字符，`[a-zA-Z0-9_-]` |
| `users.password` | ≥ 8 字符（前端 UX），bcrypt hash 落库 60 字节 |
| `tasks.repo_url` | URL 形式（HTTPS 优先，HTTP 警告） |
| `tasks.description` | 10-8000 字符 |
| `llm_providers.base_url` | URL，必须 HTTPS（生产环境） |
| `llm_providers.api_key` | **Fernet 入库**(存 `api_key_encrypted`);接口只回显掩码 `***last4`；存量明文可读 |

## 4. 错误消息

- 字段级错误用 Pydantic 标准结构 `{loc, msg, type}`，前端 antd Form 可直接消费
- 错误消息中文 OK（参考 CLAUDE.md），但枚举值英文
- 错误码 UPPER_SNAKE_CASE，与 `.claude/api-contract.md` 对齐

## 5. 与 error-handling / api-design 的边界

- **错误响应格式** → `error-handling.md`
- **API 端点契约 / 路由 / 状态码** → `api-design.md` + `.claude/api-contract.md`