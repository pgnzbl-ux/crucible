# Platform Security Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复审查确认的六项平台安全与可靠性缺陷，并明确保留 Runner→宿主 Lab 网络访问这一兼容性剩余风险。

**Architecture:** 在 API/Service/Repository 边界统一 owner 查询；以共享 URL 策略约束 Provider；在宿主 Compose 执行前增加准入门；以显式提交和补偿清理保证任务与 Lab 状态可观察。

**Tech Stack:** Python 3.11、FastAPI、Pydantic v2、SQLAlchemy Async、Celery、Docker Compose、pytest。

**Spec:** `docs/superpowers/specs/2026-08-17-platform-security-hardening-design.md`

## Global Constraints

- 不新增域名白名单、RBAC、Transactional Outbox 或出站代理。
- Base URL 仅允许 HTTPS 公网域名，不允许 IP 字面量和非公网解析结果。
- 越权资源统一返回 404。
- 不覆盖现有未提交的 SQLite `source_artifacts` 迁移修复。
- 所有生产代码遵循测试先行。

---

### Task 1: Provider 鉴权与 URL 策略

**Files:**
- Create: `backend/app/core/url_security.py`
- Modify: `backend/app/contexts/settings/api.py`
- Modify: `backend/app/contexts/settings/schemas.py`
- Modify: `backend/app/contexts/settings/service.py`
- Test: `backend/tests/test_settings_api_security.py`
- Test: `backend/tests/test_url_security.py`

**Interfaces:**
- Produces: `async def validate_public_https_url(value: str) -> str`
- Consumes: `CurrentUserId`

- [x] 编写表格驱动失败测试：HTTP、IP 字面量、私网 DNS、回环、link-local、userinfo、fragment 被拒绝，公网 HTTPS 通过。
- [x] 运行测试，确认因共享校验函数和鉴权缺失而失败。
- [x] 实现共享 URL 校验并接入 create/update/test/Runner env 读取路径。
- [x] 给全部 `/settings/llm/**` 端点注入 `CurrentUserId`，显式关闭重定向。
- [x] 运行 Provider 与 URL 测试直至通过。

### Task 2: Task owner 隔离

**Files:**
- Modify: `backend/app/contexts/task/api.py`
- Modify: `backend/app/contexts/task/service.py`
- Modify: `backend/app/contexts/task/repository.py`
- Test: `backend/tests/test_task_api_security.py`

**Interfaces:**
- Produces: owner-aware Task 查询与状态变更方法。

- [x] 编写匿名/跨 owner 的详情、事件、SSE、取消、重试、删除、节点记录失败测试。
- [x] 运行并确认测试暴露现有越权。
- [x] 所有路由注入 `CurrentUserId`，Repository 查询增加 owner 条件。
- [x] SSE 创建响应前验证 owner。
- [x] 运行 Task 安全测试直至通过。

### Task 3: Report 与 Evidence owner 隔离

**Files:**
- Modify: `backend/app/contexts/report/api.py`
- Modify: `backend/app/contexts/report/service.py`
- Modify: `backend/app/contexts/report/repository.py`
- Test: `backend/tests/test_report_api_security.py`

**Interfaces:**
- Produces: owner-aware Report/Evidence 查询与发布方法。

- [x] 编写跨 owner 的详情、按任务读取、发布、导出和证据列表失败测试。
- [x] 运行并确认测试失败。
- [x] Repository/Service/API 全链路绑定 owner，移除开发环境宽松判断。
- [x] 运行 Report 安全测试直至通过。

### Task 4: Celery 提交后投递

**Files:**
- Modify: `backend/app/contexts/task/service.py`
- Test: `backend/tests/test_task_dispatch.py`

**Interfaces:**
- Produces: `TaskService` 创建/重试的 commit-before-send 行为。

- [x] 编写顺序测试和 broker 失败状态测试。
- [x] 运行并确认现有实现失败。
- [x] 显式 commit 后投递；投递失败写 TaskRun/Task failed 并再次 commit。
- [x] 运行投递测试直至通过。

### Task 5: Compose 准入门

**Files:**
- Create: `backend/app/contexts/lab/compose_policy.py`
- Modify: `backend/app/contexts/agent/nodes/env_ready.py`
- Test: `backend/tests/test_compose_policy.py`

**Interfaces:**
- Produces: `validate_compose_file(compose_path: str, workdir: str) -> None`

- [x] 编写高危字段与安全 Compose 的表格驱动测试。
- [x] 运行并确认策略模块不存在。
- [x] 使用安全 YAML 解析实现准入；在 `docker_compose_up` 前调用。
- [x] 运行策略和 env_ready 测试直至通过。

### Task 6: Lab 补偿与 Runner 网络裁决

**Files:**
- Modify: `backend/app/contexts/agent/nodes/env_ready.py`
- Modify: `backend/app/contexts/lab/service.py`
- Test: `backend/tests/test_env_ready_lab_reuse.py`
- Test: `backend/tests/test_lab_ttl.py`

**Interfaces:**
- Produces: upload/mark_ready 失败后的 compose 补偿；保留 host-gateway 的兼容性裁决记录。

- [x] 编写上传失败清理和 failed Lab 巡检测试。
- [x] 运行并确认失败。
- [x] 增加 compose down 补偿与 failed/destroyed 巡检。
- [x] 确认 reproduce 仍依赖 `host.docker.internal`，不做会破坏 Lab 访问的无效别名删除。
- [x] 运行相关测试直至通过。

### Task 7: 文档与全量验证

**Files:**
- Modify: `.claude/api-contract.md`
- Modify: `.claude/rules/security.md`
- Modify: `docs/development-guide.md`

- [x] 同步鉴权、URL、Compose、Runner 网络与投递契约。
- [x] 运行 `python -m pytest backend/tests -q`。
- [x] 运行 `npx tsc --noEmit`。
- [x] 运行受影响文件 Ruff 检查。
- [x] 检查 `git diff --check` 并审阅完整 diff。
