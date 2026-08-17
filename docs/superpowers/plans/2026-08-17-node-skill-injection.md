# 每节点蒸馏 skill 注入 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** AI 节点不再加载桌面 `plugins/`，改为每节点一份蒸馏 SKILL.md 作 system_prompt append；user message 只带本轮 JSON。

**Architecture:** `run_one.py` 按 `NODE_KEY` 读 `node-skills/{node}/SKILL.md`，注入 `ClaudeAgentOptions.system_prompt = {preset: claude_code, append: skill}`。镜像只 COPY `node-skills/`。编排器与 submit_result 不变。

**Tech Stack:** Claude Agent SDK 0.2.134、agent-runner 容器、pytest

**Spec:** `docs/superpowers/specs/2026-08-17-node-skill-injection-design.md`

## File structure

| 路径 | 职责 |
|---|---|
| `infrastructure/agent-runner/node-skills/{profile,env_ready,audit,reproduce,report}/SKILL.md` | 该节点 SOP（system） |
| `infrastructure/agent-runner/runner/run_one.py` | 读 skill、瘦 user、不再 `plugins=` |
| `infrastructure/agent-runner/Dockerfile` | COPY node-skills，不 COPY plugins |
| `plugins/vuln-verify-expert/` | 恢复桌面母本 |
| `backend/tests/test_run_one_node_prompt.py` | user/system 拆分与不加载插件 |
| `backend/tests/test_agent_runner_dockerfile.py` | 镜像 COPY 断言 |
| `docs/agent-workflow.md` 等 | 与实现对齐 |

## Global Constraints

- 不改 6 节点编排、input_json 组装、submit_result schema、Bash 黑白名单
- user message 禁止再写角色句 / 禁令 / 「源码在 X」（这些只在 skill 或 JSON）
- 不把 `plugins/` COPY 进镜像
- 未要求则不 commit

---

## Task 1: 失败测试（prompt 拆分 + 不加载插件）

**Files:**
- Modify: `backend/tests/test_run_one_node_prompt.py`
- Modify: `backend/tests/test_agent_runner_dockerfile.py`

- [x] **Step 1: 写失败测试**

`test_run_one_node_prompt.py`：
- 所有 AI 节点的 `_build_node_prompt` 含 `submit_result` 与 JSON 字段，**不含**「你是项目画像员」「不要执行 docker compose」「源码目录:」
- `_build_options(..., node_key="profile")`：**无** `plugins`、**无** `extra_args["agent"]`；`system_prompt` 为 dict 且 `append` 含画像职责
- 五个 `node-skills/*/SKILL.md` 存在；audit skill 不含「一次 HTTP 请求测」；profile skill 不含「docker compose up」

`test_agent_runner_dockerfile.py`：Dockerfile 含 `COPY` node-skills，不含 `COPY plugins/vuln-verify-expert`

- [ ] **Step 2: 跑测试，确认失败**

```
cd backend && python -m pytest tests/test_run_one_node_prompt.py tests/test_agent_runner_dockerfile.py -q
```

Expected: FAIL（旧 header / 仍加载插件 / 无 node-skills / Dockerfile 仍 COPY plugins）

---

## Task 2: run_one 注入 + 五份 skill

**Files:**
- Create: `infrastructure/agent-runner/node-skills/{profile,env_ready,audit,reproduce,report}/SKILL.md`
- Modify: `infrastructure/agent-runner/runner/run_one.py`

- [ ] **Step 3: 蒸馏五份 SKILL.md**（从现有平台 `agents/*.md` 切：去掉「输入字段表」，保留工作流/禁令/submit 语义；audit 不写发 HTTP；profile/env_ready 不写 compose up 由 Agent 执行）
- [ ] **Step 4: 改 `run_one.py`**
  - `NODE_AI_KEYS` 替换 `NODE_AGENT_MAP`
  - `_load_node_skill` 默认目录：`Path(__file__).parent.parent / "node-skills"`
  - `_build_options`：`system_prompt={type:preset, preset:claude_code, append:skill}`；不设 `plugins`；不设 `--agent`
  - `_build_node_prompt` 只留 submit_result 句 + JSON
  - 无 `NODE_KEY` 的 `.prompt.json` 兼容路径：不起插件、不读 skill
- [ ] **Step 5: 跑测试通过**

```
cd backend && python -m pytest tests/test_run_one_node_prompt.py tests/test_run_one_bash_policy.py -q
```

---

## Task 3: 镜像 COPY

**Files:**
- Modify: `infrastructure/agent-runner/Dockerfile`
- Modify: `.dockerignore`

- [ ] **Step 6: Dockerfile 改为 COPY `infrastructure/agent-runner/node-skills` → `/app/node-skills`；删 PLUGIN_* ENV**
- [ ] **Step 7: `.dockerignore` 去掉 `!plugins/vuln-verify-expert/**/*.md`**
- [ ] **Step 8: pytest `tests/test_agent_runner_dockerfile.py` 通过**

---

## Task 4: 恢复桌面插件

**Files:**
- Delete: `plugins/vuln-verify-expert/agents/{profiler,env-builder,auditor,reproducer,reporter}.md`
- Modify: `plugins/vuln-verify-expert/skills/run-project-env/SKILL.md`（删「Crucible 平台」节）
- Modify: `plugins/vuln-verify-expert/skills/run-project-env/references/isolation.md`（删平台节）
- Modify: `plugins/vuln-verify-expert/skills/vuln-verify/SKILL.md`（删「Crucible 平台：节点切分」）
- Modify: `plugins/vuln-verify-expert/README.md`

- [ ] **Step 9: 删 5 个子 agent、撤平台专章、README 改回桌面加载说明**

---

## Task 5: 权威文档

**Files:**
- Modify: `docs/agent-workflow.md`（现行模型：6 节点 + node-skills）
- Modify: `docs/superpowers/specs/2026-08-12-platform-node-orchestration-design.md` §6
- Modify: `docs/development-guide.md`、`CLAUDE.md`

- [ ] **Step 10: 文档与实现一致；全量相关 pytest**

```
cd backend && python -m pytest tests/test_run_one_node_prompt.py tests/test_run_one_bash_policy.py tests/test_agent_runner_dockerfile.py tests/test_ai_runner.py tests/test_report_node.py -q
```
