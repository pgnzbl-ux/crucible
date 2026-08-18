# Crucible Agent 阶段化编排设计

> 文档版本: v0.3 · 2026-08-17
> 现行：**平台 6 节点编排** + 每节点蒸馏 skill 注入 runner。
> `plugins/vuln-verify-expert` 是桌面端母本，**不** COPY 进 agent-runner。
> 节点 SOP：`infrastructure/agent-runner/node-skills/{profile,env_ready,audit,reproduce,report}/SKILL.md`
> 设计细节：`docs/superpowers/specs/2026-08-12-platform-node-orchestration-design.md`
> 与 `docs/superpowers/specs/2026-08-17-node-skill-injection-design.md`

阅读对象: 任何接手 Agent 执行链路的开发者。

---

## 1. 为什么是平台编排 + 蒸馏 skill

桌面插件已经跑通白盒验证方法论，适合当**指导母本**。平台侧不能把整包插件当 runtime：

1. 控制流是 6 节点状态机（断点续跑、分支出口），不是桌面 0/A/B/C/D 总 agent。
2. 整包加载会把「发 HTTP / compose up」带进不该做这些事的节点。
3. 生产惯例：system = 该工种 SOP，user = 本单 JSON。Claude Code skill 触发是可选的，节点方法论必须每次生效，因此用 `system_prompt` append，而不是 `--agent` 加载插件。

**结论**：Celery `orchestrator` 驱动节点；每个 AI 节点起一只新容器；`run_one.py` 读该节点 `SKILL.md` 作 system_prompt，user 只带 `.node.json` 的 input_json。

---

## 2. 架构

```
┌────────────────────────────────────────────────────────────────┐
│ Celery Worker                                                    │
│   orchestrator.py  6 节点循环                                    │
│     source ■代码 → profile → env_ready → audit → reproduce → report │
│     AI 节点：ai_runner 写 .node.json → docker run agent-runner    │
└────────────────────────────────────────────────────────────────┘
        │ docker SDK（bind mount host_workdir → /workspace）
        ▼
┌────────────────────────────────────────────────────────────────┐
│ agent-runner（crucible-agent-runner:base）                       │
│   python -m runner.run_one                                       │
│     读 /workspace/.node.json                                     │
│     system_prompt = claude_code preset + /app/node-skills/{key}/SKILL.md │
│     user = 「调用 submit_result」+ input_json                    │
│     不加载 plugins/                                              │
└────────────────────────────────────────────────────────────────┘
```

分支出口、节点 schema 见编排设计 spec §1。reproduce 交结构化测试事实（verdict / attempts / evidence）；confirmed/partial 须交 `poc` 正本，**不写报告**；report 节点是唯一文档作者：confirmed/partial 出漏洞报告，其余出验证记录（无 PoC、无 CVSS）。

---

## 3. 阶段语义（桌面母本，供蒸馏参考）

完整桌面阶段见 [`plugins/vuln-verify-expert/agents/vuln-verify-expert.md`](../plugins/vuln-verify-expert/agents/vuln-verify-expert.md)。平台切分：

| 平台节点 | 蒸馏自 | skill 路径 |
|---|---|---|
| profile | run-project-env 第 1–2 步 | `node-skills/profile/SKILL.md` |
| env_ready | 写配方（不含 compose up） | `node-skills/env_ready/SKILL.md` |
| audit | vuln-verify Phase 1/2/2.5 | `node-skills/audit/SKILL.md` |
| reproduce | Phase 3/4（HTTP 事实，不写报告） | `node-skills/reproduce/SKILL.md` |
| report | Phase 5：漏洞报告或验证记录 | `node-skills/report/SKILL.md` |

**分支出口**（非 happy path）：

- **非 web 结束**：节点 1 `is_web is not True` → skip 2–5。
- **误报 Quick-Stop**：audit `gate_verdict=fail` → skip reproduce，仍跑 report。
- **待复核**：audit `uncertain` → skip reproduce，仍跑 report 产 `needs_review` 验证记录，`task=needs_review`（task.verdict 空）。
- **报告失败不得覆盖 B/D**：report 节点失败时，若 audit 已是 `fail` / `uncertain`，任务仍 `completed`+`false_positive` 或 `needs_review`；只把 report NodeRun 标 `failed`。
- **回退环**：reproduce 容器内自行深挖同一 `core_claim`，判定即停（不是平台节点循环）。
- **单节点重试**：`POST /tasks/{id}/retry?from_node=env_ready|audit|reproduce|report` 拷贝前置产出、从该节点起重跑；默认无参数仍从 source 整条重跑。

---

## 4. 部署

### 4.1 构建 agent-runner 镜像（build context = 项目根）

```bash
cd <repo-root>
docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .
```

Dockerfile `COPY infrastructure/agent-runner/node-skills` → `/app/node-skills/`，**不** COPY `plugins/`。
镜像是分析员工具箱（Python 3.11.15 + Node 20.20.2 + 完整 Linux 命令，非 slim）；
被测项目的运行时仍写在 `.vuln-env`，由宿主机 `docker compose up` 拉起。

### 4.2 凭据零落盘

- 蒸馏 skill 只含静态 `.md`，**不含任何密钥**
- LLM 凭据由 worker 通过 `docker run --env` 注入，容器销毁即消失
- 见 `ClaudeSdkAdapter.build_runner_env()`

### 4.3 Prompt 注入（`run_one.py::_build_options`）

```python
ClaudeAgentOptions(
    system_prompt={"type": "preset", "preset": "claude_code", "append": skill_md},
    cwd="/workspace/{仓库名}",
    mcp_servers={"crucible": submit_result_server},
    # 没有 plugins=，没有 --agent
)
```

user message 只含「调用 submit_result」+ 本轮 `input_json`。

---

## 5. 事件流对接


插件 agent.md 要求「用 `phase.updated` 事件记录阶段、进度和阻塞原因」。这条事件流
天然对接已有 SSE 链路：

```
插件 agent 产出 phase.updated / agent.thinking / agent.message / tool.call.* / agent.completed / agent.failed
  → SDK Message → run_one.py 翻译为统一事件 dict → stdout JSONL
  → worker LineBufferedJsonParser 解析 → AgentEvent 落库
  → Redis Pub/Sub（task.{id}.events）→ SSE 端点转发 → 前端 useTaskEvents
```

事件 schema 见 [`run_one.py`](../infrastructure/agent-runner/runner/run_one.py) `_stream_messages`。
`agent.thinking` 来自 SDK ThinkingBlock（或鸭子类型 / dict）；`agent.failed` 带 `title` + `hint` 便于排错。
前端「事件流」Tab 渲染思考 / 回复 / 工具 / 错误（`EVENT_TYPE_LABELS` / `EVENT_PHASE_LABELS` 在 `frontend/src/shared/lib/meta.ts`）；失败节点在步骤条展示人类可读全文。节点最终 `failed` 时（真实 SDK，非 Mock）会把本轮 jsonl / 配方快照打成 `node_run` 包写入 MinIO `crucible-task`，索引表 `node_run_failures`。
详情顶栏流程图与「进度」竖条均可点（`pending` 除外），按节点过滤事件。无 `node_key` 的思考/工具按同一 run 的 `sequence` 归到当时节点，不用 SDK `created_at`（时间戳经常早于 `node.updated`）。

> **待补**：插件 agent.md 的阶段名（start / preflight / scanning / reproducing / completed
> 等）与前端 `EVENT_PHASE_LABELS` 的映射需对齐，见 P1 backlog。

---

## 6. 权限策略

### 现状（v0.2）

```python
permission_mode="bypassPermissions"  # 非 Bash 工具自动批准（减少摩擦）
allowed_tools=["Read","Grep","Glob","Bash","Write","Edit","WebFetch","WebSearch"]
hooks={"PreToolUse": [{"matcher": "Bash", "hooks": [_pre_tool_use_hook]}]}  # Bash 黑名单
```

**双层模型**（v0.2 重构后，`can_use_tool` 已废弃）：

1. **工具类型白名单**（`allowed_tools`）—— 限定可用工具集合，不在表里的一律不可用
2. **Bash 命令黑名单**（`PreToolUse` hook）—— `matcher: "Bash"`，拦截破坏性命令
   （`rm`/`mv`/`cp`/`chmod`/`chown`/`dd`/`mkfs`/`|bash`/`>/etc/`/`/proc/`/`/sys/`），其余放开
   （插件工作流需要 `docker compose`/`git`/`curl`/`python`）

**为什么用 hook 而非 `can_use_tool`**：SDK 的 `permission_mode=bypassPermissions` 会
shadow `can_use_tool`（`CanUseToolShadowedWarning`，自动批准发生在回调之前）；而
`PreToolUse` hook 在**所有** permission_mode 下都执行，`permissionDecision: "deny"` 由
`_bundled/claude` CLI 原生消费，bypassPermissions 之前拦截。v0.1 用 `can_use_tool` 是
无效的（黑白名单不生效），v0.2 切 hook 后真正生效。

**hook 形态**：SDK 0.2.x 的 `hooks` 字段直接接受 **async Python 回调**（`HookCallback`），
非 shell command —— 复用纯 Python 的黑白名单逻辑，无需外部脚本。deny 时同步输出
`tool.call.denied` 审计事件（worker 落 `AgentEvent`，前端可见）。

安全护栏全栈：
1. 容器层：非 root + 只读 rootfs + cap_drop ALL + tmpfs + 资源限制（`AgentRunnerSpec` 默认）
2. host_workdir 任务级隔离 + 任务结束 rmtree
3. `allowed_tools` 工具类型白名单
4. `PreToolUse` hook Bash 命令黑名单（v0.2 生效）

---

## 7. 不在本版本范围（gap 汇总 → 后续 PR）

| gap | 影响 | 状态 / 路线 |
|---|---|---|
| 权限 hook 重构 | ~~Bash 黑白名单不生效~~ | ✅ v0.2 完成（PreToolUse hook） |
| 插件产物自动归档 | ~~报告/截图须手动上传~~ | ✅ v0.2 完成（worker 扫 host_workdir/{仓库名} → MinIO + Evidence） |
| browser MCP 未接入 | XSS/DOM 类证据须降级 HTTP | P1（独立子项目，见下） |
| 容器内无法 docker compose 搭靶场 | 阶段 B 自建环境受限 | P1（DinD / docker.sock 架构决策） |
| credential_store 未接入 | ~~需登录的漏洞进 `needs_credentials`~~ | ✅ v0.2 完成（P1-6 Credential Proxy：env_var/file 注入） |
| 阶段名 ↔ 前端标签映射 | 部分阶段显示英文 phase key | P1 |

### browser MCP 子项目（独立立项，非阻塞）

插件 agent.md 已设计「浏览器非必需时降级 HTTP/API/OOB」，故 browser MCP 是**增强**而非
阻塞项。接入是个独立子项目，涉及：

- **镜像**：agent-runner 装 chromium（~500MB）或外挂 MCP server 容器
- **权限**：headless chrome 需 `--no-sandbox` 或额外 cap（与只读 rootfs + cap_drop ALL 冲突，需单独容器）
- **MCP 配置**：`ClaudeAgentOptions` 的 MCP server 注入（`setting_sources` 或 `mcp_servers`）
- **网络**：agent-runner 容器 ↔ 浏览器 MCP server ↔ 靶场网络打通
- **方案候选**：① 单独跑 `playwright-mcp` / `chrome-devtools-mcp` 容器，agent-runner 通过 MCP 连接；② agent-runner 镜像装 chromium + playwright，容器内跑（需放开 sandbox 限制）

建议单独立项评估，本次不实现。

### v0.2 已打通的核心链路

任务创建 → 6 节点编排 → AI 节点注入蒸馏 skill → JSONL 事件 → 报告落库。
