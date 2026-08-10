# MEMORY

项目特有约定索引。详见 `docs/development-guide.md`（主线文档）。

- [Context 五件套 + 跨 Context 边界](.claude/rules/backend.md) — `api/models/schemas/service/repository`，禁 ORM relationship
- [凭据零落盘 + 沙箱基线](.claude/rules/security.md) — Fernet 加密 + SandboxSpec 默认值
- [P0/P1/P2 开发路线](docs/development-guide.md#4) — SSE / 取消 / JWT / 证据 / features 填充 / RBAC / OIDC
- [踩坑记录](docs/development-guide.md#54) — bcrypt 4.0.1 / Docker SDK 7.x / Windows solo pool
- [DeepSeek 凭据注入](.claude/rules/backend.md#5) — `build_env()` 8 个 ANTHROPIC_* 变量