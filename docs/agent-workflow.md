# Crucible Agent 阶段化编排设计

> 文档版本: v0.1 · 2026-08-11
> 定位: 把 development-guide.md「P0-0 Agent 阶段化编排」落地为**插件化**架构 ——
> 阶段工作流不在 Python 侧写 Stage 类，而是由 Claude Code 插件 `vuln-verify-expert`
> 承载，agent-runner 容器加载插件后由 SDK 调用其 agent。
>
> 阅读对象: 任何接手 Agent 执行链路的开发者。

---

## 1. 为什么是插件化（而非 Python Stage 类）

development-guide.md P0-0 原描述「新增 agent/stages.py + preflight.py + prompts.py」
是一种 Python 侧 Stage Orchestrator 思路。本项目实际采用**插件化**路线，原因：

1. **阶段语义是 Agent 的领域知识，不是平台控制流** —— 白盒审计 → 靶场搭建 →
   验证门禁 → 复现 → 出报告，这套流程及其「禁止行为 / 证据判定档位」是安全研究员的
   方法论，用 Claude Code 的 agent + skills（Markdown 描述）表达远比 Python 状态机
   自然，且可被 LLM 直接消费。
2. **复用已有资产** —— `plugins/vuln-verify-expert/` 已是完整、可独立 `claude plugin validate`
   的成熟插件（含两个 skill + references + scripts + tests），重写一遍是浪费。
3. **职责清晰** —— 平台（Crucible）负责任务管理 / 调度 / 凭据注入 / 事件持久化 /
   报告归档；插件（vuln-verify-expert）负责验证执行流程。两者解耦，插件可独立演进
   甚至被其他平台复用。

**结论**：阶段化编排 = 加载 vuln-verify-expert 插件 + 以其 agent 启动会话。
平台不解析阶段，只翻译插件产出的 `phase.updated` 事件。

---

## 2. 架构

```
┌────────────────────────────────────────────────────────────────┐
│ Celery Worker（Python）                                          │
│   agent/tasks.py::_run_analysis                                  │
│     ├─ host 上 git clone → host_workdir/project                  │
│     ├─ 写 .prompt.json + 注入 ANTHROPIC_* env（凭据零落盘）       │
│     ├─ docker run agent-runner 容器（bind mount host_workdir）    │
│     ├─ container.logs(stream=True) → LineBufferedJsonParser       │
│     │   → AgentEvent 落库 + Redis Pub/Sub → SSE 推送              │
│     └─ container.wait() → 结论分流 → 报告生成                     │
└────────────────────────────────────────────────────────────────┘
        │ docker SDK
        ▼
┌────────────────────────────────────────────────────────────────┐
│ agent-runner 容器（crucible-agent-runner:base）                  │
│   tini → python -m runner.run_one                                │
│     ├─ 读 /workspace/.prompt.json                                │
│     ├─ ClaudeAgentOptions(                                       │
│     │     plugins=[{type:"local", path:"/app/plugins/vuln-verify-expert"}],│
│     │     extra_args=["--agent", "vuln-verify-expert:vuln-verify-expert"],│
│     │     cwd="/workspace/project", ...)                          │
│     └─ async for msg in query(prompt, options):                  │
│          print(json.dumps(translate(msg)))   # stdout JSONL      │
└────────────────────────────────────────────────────────────────┘
        │ SDK 加载插件 → 启动为 vuln-verify-expert agent
        ▼
┌────────────────────────────────────────────────────────────────┐
│ 插件 vuln-verify-expert（/app/plugins/，构建时 COPY 进镜像）      │
│   agents/vuln-verify-expert.md   ← 阶段 0/A/B/C/D 工作流定义      │
│   skills/run-project-env/        ← 搭靶场                         │
│   skills/vuln-verify/            ← 白盒审计 + 复现 + 出报告        │
│   settings.json                  ← {"agent": "vuln-verify-expert"} │
└────────────────────────────────────────────────────────────────┘
```

---

## 3. 阶段定义（由插件 agent.md 承载）

完整阶段语义见 [`plugins/vuln-verify-expert/agents/vuln-verify-expert.md`](../plugins/vuln-verify-expert/agents/vuln-verify-expert.md)。摘要：

| 阶段 | 名称 | 关键产出 | 平台依赖 |
|---|---|---|---|
| 0 | 平台预检 | `preflight.completed` 事件；能力缺失 → `needs_review` / `infra_error` | browser MCP / sandbox / artifact_store（见 §4） |
| A | 接单与建仓 | 源码固定到 ref；批量任务分目录 | credential_ref（凭据注入） |
| B | 搭建靶场 | `.vuln-env/`（Dockerfile/compose）+ `.vuln-env.json` + 访问地址 | sandbox（受控 docker compose） |
| C | 漏洞验证 | 利用链分析 + PoC 复现 + 截图 + `report.md` | browser MCP + artifact_store |
| D | 交付收尾 | 一句话结论 + artifact 引用 + 启停说明 | sandbox 资源回收 |

**分支出口**（非 happy path）：

- **非 web 结束**：阶段 B 第 2 步 web 门禁判定为非 web → 直接结束，不下结论。
- **误报 Quick-Stop**：阶段 C Phase 2.5 Gate 推演不通 → 不发任何 HTTP，判误报。
- **回退环**：阶段 C Phase 3 首测未复现 → 回 Phase 2 重走链 → 试变体（上限 5 次）。

**判定档位**（由最强可观察证据决定）：已确认 / 部分确认 / 代码可达 / CODE SMELL /
误报 / 未复现。详见 [`skills/vuln-verify/SKILL.md`](../plugins/vuln-verify-expert/skills/vuln-verify/SKILL.md) 的「Verdict ↔ Evidence Tier Binding」。

---

## 4. 平台 ↔ 插件契约

插件通过以下平台能力工作（见插件 README.md「平台接口边界」）：

| 能力 | 插件用途 | 当前实现状态 | gap 与路线 |
|---|---|---|---|
| `browser` | 真实页面截图（XSS/DOM 类证据） | ❌ 未接入 | P1：Playwright MCP 或 Chrome DevTools MCP 挂进 agent-runner 容器；插件阶段 0 预检探测，缺失时降级 HTTP/API 证据 |
| `sandbox` | 受控 docker compose 搭靶场、收集产物、销毁 | ⚠️ 部分（容器隔离已就绪，但插件无法在容器内再起 docker） | P1：DinD 或挂宿主 docker.sock（受控）；当前插件搭靶场能力受限，白盒审计 + curl 验证仍可跑 |
| `credential_store` | `credential_ref` 引用注入短命凭据 | ❌ 未接入 | P1-6 Credential Proxy：任务级凭据引用 → env / 临时文件 600 注入 → 任务结束销毁 |
| `artifact_store` | 归档报告、截图、日志、原始响应 | ⚠️ 部分（MinIO 已就绪，但插件产物自动归档未接） | P0-4 已支持**手动**证据上传；P1：worker 扫描 host_workdir 的 `VULN-*/` + `.vuln-env/` 自动归档 report.md / .docx / img/* |

> v0.1 的实际能力边界：**白盒审计 + curl/HTTP 验证 + 报告产出**可端到端跑通；
> 浏览器证据 / 自动搭靶场 / 凭据注入 / 自动归档为已知 gap，按上表节奏补齐。
> 插件 agent.md 已写明「浏览器不是必需时降级 HTTP/API/OOB 证据」，故 gap 不阻塞主链路。

---

## 5. 部署

### 5.1 构建 agent-runner 镜像（build context = 项目根）

```bash
cd <repo-root>
docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .
```

Dockerfile 会 `COPY plugins/vuln-verify-expert /app/plugins/vuln-verify-expert`，
并通过 ENV 暴露 `PLUGIN_DIR` / `PLUGIN_NAME` / `AGENT_NAME` 给 `run_one.py`。

`.dockerignore` 排除 `.venv/` `node_modules/` `.git/` 等，只保留
`infrastructure/agent-runner/` 与 `plugins/vuln-verify-expert/`。

### 5.2 凭据零落盘

- 插件目录只含静态 `.md`，**不含任何密钥**
- LLM 凭据（`ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_API_KEY` /
  `ANTHROPIC_MODEL` 等）由 worker 通过 `docker run --env` 注入，容器销毁即消失
- 见 [`backend/app/contexts/agent/sdk_adapter.py`](../backend/app/contexts/agent/sdk_adapter.py)
  `ClaudeSdkAdapter.build_runner_env()`

### 5.3 插件加载的两个等价路径（`run_one.py::_build_options`）

```python
# 主路径（SDK 0.2.x typed）：
ClaudeAgentOptions(
    plugins=[{"type": "local", "path": "/app/plugins/vuln-verify-expert"}],
    extra_args=["--agent", "vuln-verify-expert:vuln-verify-expert"],
    ...
)

# 降级路径（extra_args CLI flag 透传，所有版本支持）：
ClaudeAgentOptions(
    extra_args=["--plugin-dir", "/app/plugins/vuln-verify-expert",
                "--agent", "vuln-verify-expert:vuln-verify-expert"],
    ...
)
```

`run_one.py` 先 try 主路径，`TypeError/ValueError` 时降级 —— 跨 SDK 版本兼容。

---

## 6. 事件流对接

插件 agent.md 要求「用 `phase.updated` 事件记录阶段、进度和阻塞原因」。这条事件流
天然对接已有 SSE 链路：

```
插件 agent 产出 phase.updated / agent.message / tool.call.* / agent.completed
  → SDK Message → run_one.py 翻译为统一事件 dict → stdout JSONL
  → worker LineBufferedJsonParser 解析 → AgentEvent 落库
  → Redis Pub/Sub（task.{id}.events）→ SSE 端点转发 → 前端 useTaskEvents
```

事件 schema 见 [`run_one.py`](../infrastructure/agent-runner/runner/run_one.py) `_stream_messages`。
前端 Timeline 直接消费 `phase.updated` 的 `phase` 字段（phase 标签映射在
`frontend/src/shared/lib/meta.ts::EVENT_PHASE_LABELS`）。

> **待补**：插件 agent.md 的阶段名（start / preflight / scanning / reproducing / completed
> 等）与前端 `EVENT_PHASE_LABELS` 的映射需对齐，见 P1 backlog。

---

## 7. 权限策略

### 现状（v0.1）

```python
permission_mode="bypassPermissions"  # 自动批准所有工具调用
allowed_tools=["Read","Grep","Glob","Bash","Write","Edit","WebFetch","WebSearch"]
can_use_tool=_permission_gate        # 黑白名单回调（白盒审计风格）
```

**已知限制**：`permission_mode=bypassPermissions` 下 `can_use_tool` **不会被调用**
（SDK 有 `CanUseToolShadowedWarning`）—— 即 `_permission_gate` 的 Bash 黑白名单
（禁 `rm`/`mv`/`| bash`/`/proc`/`/sys` 等）当前**不生效**。

安全护栏当前依赖：
1. 容器层：非 root + 只读 rootfs + cap_drop ALL + tmpfs + 资源限制（`AgentRunnerSpec` 默认）
2. host_workdir 任务级隔离 + 任务结束 rmtree
3. `allowed_tools` 白名单（不在表里的工具一律不可用）

### 路线（P1）

切到 `PreToolUse` hook 实现黑白名单 —— hook 在所有 permission_mode 下都会跑：

```python
hooks={
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "..."}]}]
}
```

或 SDK 若暴露 `permission_mode="dontAsk"`（待 0.2.x 确认），让 `can_use_tool` 生效。
重构后 `_permission_gate` 的规则直接复用，无需重写。

---

## 8. 不在本版本范围（gap 汇总 → 后续 PR）

| gap | 影响 | 路线 |
|---|---|---|
| browser MCP 未接入 | XSS/DOM 类证据须降级 HTTP | P1 |
| 容器内无法 docker compose 搭靶场 | 阶段 B 自建环境受限 | P1（DinD / docker.sock） |
| credential_store 未接入 | 需登录的漏洞进 `needs_credentials` | P1-6 |
| 插件产物自动归档 | 报告/截图须手动上传 | P1（worker 扫 host_workdir） |
| 权限 hook 重构 | Bash 黑白名单当前不生效 | P1 |
| 阶段名 ↔ 前端标签映射 | 部分阶段显示英文 phase key | P1 |

> v0.1 已打通的核心链路：**任务创建 → 容器加载插件 → agent 跑白盒审计 + HTTP 验证
> → phase.updated 实时推送 → 结论分流 → 报告生成 → 证据手动上传**。
