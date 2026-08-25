# Token 用量记账（SDK 优先）

> 裁决顺序：Claude Agent SDK Python 官方用量说明 → Messages `usage` 键名语义（仅解释）→ 仓库约束。  
> 仓库锁定：`claude-agent-sdk==0.2.134`（agent-runner）。

## 权威来源（只认回传，禁止自算）

| 来源 | 何时用 |
|------|--------|
| `ResultMessage.usage` | 单次 `query()` 终态；主环 snake_case 四键；**不含** subagent |
| `ResultMessage.model_usage` | 有则优先；整树（主环+subagent+compaction）；`ModelUsage` camelCase，对各模型**求和落库**（仍是 SDK 数） |
| `ResultMessage.total_cost_usd` | 可选展示；**客户端估价，非账单** |
| `AssistantMessage.usage` | 同 query 逐步；`output_tokens` 为 placeholder，**终态只信 Result** |
| 快模型网关 `usage` | `screen` T2；Anthropic 兼容体有则透传同名字段 |

**禁止：** 用 prompt 体积 / 轮次 / `total−input` 反推 cache；把 SystemMessage `thinking_tokens` 当计费；`count_tokens` 当消耗；历史估算回填。

Messages 键名语义（解释用）：

```text
total_input ≈ input_tokens + cache_creation_input_tokens + cache_read_input_tokens
```

开启 prompt cache 时 `input_tokens` 多为断点后未缓存段。

## Crucible 落库

- 表 `agent_usage` 一行 = 一次 Agent `query()` 或一次快模型 HTTP。
- 字段：`prompt_tokens`←`input_tokens`；`completion_tokens`←`output_tokens`；`cache_read_input_tokens` / `cache_creation_input_tokens`（缺省 0）。
- **整树优先** `model_usage`；若其 cache 为 0 而同次 `usage` 带回 `cache_*`（部分兼容网关），台账保留 `usage` 侧回传值——仍是 API 数据，禁止自算。
- 任务合计：`total_tokens = prompt + completion + cache_read + cache_creation`（体积口径；预算软停用此值）。
- 多轮形状回喂：各轮回传字段相加（含 cache）。
- 实时：会话完成后 SSE `usage.updated`（不转发 `thinking_tokens`）。
- **部署注意：** 改台账后须重启 Celery worker；仅升级 API 进程不会让正在跑的 worker 写入 `cache_*`。

实现入口：`backend/app/contexts/agent/usage_ledger.py`。
