# 节点 3 静态白盒门禁 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平台把 audit 收成静态白盒：形状校验三值 `gate_verdict`、按枚举路由（含出口 D 待复核）、硬拦截 HTTP/改源码；不把模型自评当成可观察排障循环。

**Architecture:** `validate_output("audit")` 按 spec §3 做形状表；编排器新增出口 D（`uncertain` → skip reproduce+report、`task=needs_review`、保留 host_workdir）。`run_one.py` 对 `NODE_KEY=audit` 收紧 `allowed_tools` 并 deny curl/wget/httpie。插件只声明停在 Phase 2.5。AuditNode 仍一次 `run_ai_node`，无节点内重试。

**Tech Stack:** Python 3.11 / FastAPI / pytest / React 19 / vitest / Claude Agent SDK hooks

**Spec:** `docs/superpowers/specs/2026-08-14-audit-static-gate-design.md`（v1.1 已评审）

## Global Constraints

- 提交：`type(scope):` 英文，描述主体简体中文；**用户未明确要求则跳过每个 Task 的 Commit 步**（仓库 git-workflow）
- Windows 提交用 `git commit -m "subject" -m "body"`，不用 HEREDOC
- 三值分类不可机械证伪：平台只校验形状并路由，禁止在 Python 里解析 kill_chain 真伪
- `uncertain` **一次即出口 D**，禁止仿 env_ready 做 attempt 循环
- 不对调 audit / env_ready 顺序；不拆 `vuln-verify` 目录；不拦 `python urllib`
- 不给 TaskRun 加 `needs_review`；run 仍 `completed`，task 才是 `needs_review`
- `LIVE_TASK_STATUSES` **不**把 `needs_review` 算 live（Lab 不续 TTL）
- 后端测试：`d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest ...`（cwd `backend`）
- 前端测试：`cd frontend && npx vitest run src/shared/lib/nodeOutput.test.ts`
- 当前工作树已有配方闭环 / Node 钉版本等未提交改动：**叠加上面改，不要还原无关 diff**

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `backend/app/contexts/agent/ai_runner.py` | 改 | audit 形状校验 + mock 补 `runtime_dependent` + schema enum |
| `backend/app/contexts/agent/orchestrator.py` | 改 | 出口 D；resume 同样走 D |
| `backend/app/contexts/agent/tasks.py` | 改 | `should_retain_hostdir` 含 `needs_review` |
| `backend/app/contexts/agent/nodes/audit.py` | 改（仅注释/确认无 target_url） | 一次 AI，不注入靶场 |
| `infrastructure/agent-runner/runner/run_one.py` | 改 | audit 工具白名单 + Bash deny + submit_result schema |
| `plugins/vuln-verify-expert/agents/auditor.md` | 改 | Crucible 平台专章 + 三值契约 |
| `plugins/vuln-verify-expert/agents/reproducer.md` | 改 | 读 `runtime_dependent` |
| `plugins/vuln-verify-expert/skills/vuln-verify/SKILL.md` | 改 | 平台：audit 停在 2.5 |
| `frontend/src/shared/lib/nodeOutput.ts` | 改 | uncertain / runtime_dependent 摘要 + 详情 kill_chain |
| `frontend/src/shared/components/NodeSteps.tsx` | 改 | 非 compact audit 用详情函数 |
| 权威文档 | 改 | 编排 §1.2/§1.3、agent-workflow 出口 D、development-guide 一句 |

---

### Task 1: audit 产出形状校验

**Files:**
- Modify: `backend/app/contexts/agent/ai_runner.py`（`NODE_OUTPUT_SCHEMAS` / `NODE_INPUT_SCHEMAS` / `validate_output` / `_mock_output`）
- Test: `backend/tests/test_ai_runner.py`（改现有 `test_validate_audit_*`，加表驱动）

**Interfaces:**
- Consumes: 现有 `validate_output(node_key: str, output: dict) -> tuple[bool, str | None]`
- Produces: 同签名；`node_key=="audit"` 时按 spec §3 形状表返回。错误字符串必须能被测试 `in` 命中：
  - `缺必需字段: gate_reason`
  - `gate_verdict 必须是 pass|fail|uncertain`
  - `gate_reason 不能为空`
  - `pass 需要非空 kill_chain`
  - `pass 需要 payloads 至少 1 条`
  - `pass 需要 runtime_dependent 为 bool`
  - `fail 需要 defense_layers 为长度≥1 的数组`
  - `uncertain 不得带非空 payloads`
- `NODE_INPUT_SCHEMAS["audit"]["properties"]["gate_verdict"]["enum"]` = `["pass","fail","uncertain"]`
- `NODE_INPUT_SCHEMAS["audit"]["properties"]["runtime_dependent"]` = `{"type":"boolean"}`
- `NODE_INPUT_SCHEMAS["audit"]["required"]` = `["gate_verdict","gate_reason"]`
- `_mock_output("audit")` 含 `runtime_dependent: False` 且能通过 `validate_output`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_ai_runner.py` **替换** `test_validate_audit_gate_verdict` / `test_validate_audit_no_gate`，并追加：

```python
def _pass_ok(**over):
    base = {
        "gate_verdict": "pass",
        "gate_reason": "Q1 有危害。Q2 连通。Q3 无阻断。",
        "kill_chain": "login → sql",
        "defense_layers": [],
        "payloads": ["' OR 1=1"],
        "runtime_dependent": False,
    }
    base.update(over)
    return base


@pytest.mark.parametrize(
    "output,needle",
    [
        ({"kill_chain": "..."}, "gate_verdict"),
        ({"gate_verdict": "fail"}, "gate_reason"),
        (_pass_ok(gate_verdict="maybe"), "pass|fail|uncertain"),
        (_pass_ok(gate_reason="  "), "gate_reason 不能为空"),
        (_pass_ok(kill_chain=""), "kill_chain"),
        (_pass_ok(payloads=[]), "payloads"),
        (_pass_ok(runtime_dependent="yes"), "runtime_dependent"),
        (
            {"gate_verdict": "fail", "gate_reason": "阻断", "kill_chain": "a", "defense_layers": []},
            "defense_layers",
        ),
        (
            {"gate_verdict": "uncertain", "gate_reason": "对不上", "payloads": ["x"]},
            "uncertain 不得带非空 payloads",
        ),
    ],
)
def test_validate_audit_shape_rejects(output, needle):
    ok, err = validate_output("audit", output)
    assert not ok
    assert needle in (err or "")


@pytest.mark.parametrize(
    "output",
    [
        _pass_ok(),
        _pass_ok(runtime_dependent=True),
        {
            "gate_verdict": "fail",
            "gate_reason": "结构性阻断",
            "kill_chain": "entry 到 validator 停",
            "defense_layers": [{"name": "validator", "bypass": "不可绕过"}],
            "payloads": [],
        },
        {"gate_verdict": "uncertain", "gate_reason": "描述对不上任何 sink"},
        {"gate_verdict": "uncertain", "gate_reason": "锁不住 harm", "payloads": []},
    ],
)
def test_validate_audit_shape_accepts(output):
    ok, err = validate_output("audit", output)
    assert ok, err


def test_audit_input_schema_has_uncertain_and_runtime_dependent():
    spec = NODE_INPUT_SCHEMAS["audit"]
    assert spec["properties"]["gate_verdict"]["enum"] == ["pass", "fail", "uncertain"]
    assert spec["properties"]["runtime_dependent"]["type"] == "boolean"
    assert "gate_reason" in spec["required"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_ai_runner.py -q`

Expected: `test_validate_audit_shape_rejects` / `test_validate_audit_shape_accepts` 失败（旧 `validate_output` 只查字段是否存在，`{"gate_verdict":"fail"}` 仍会 ok）。

- [ ] **Step 3: 最小实现**

在 `ai_runner.py`：

- `NODE_OUTPUT_SCHEMAS["audit"]["required"]` = `["gate_verdict", "gate_reason"]`；optional 含 `kill_chain` / `defense_layers` / `payloads` / `runtime_dependent`
- `NODE_INPUT_SCHEMAS["audit"]` 按 Interfaces 改 enum / required / `runtime_dependent`
- `_mock_output` 的 audit 分支加 `"runtime_dependent": False`
- `validate_output` 在通用 required 检查之后，若 `node_key == "audit"` 调用：

```python
def _validate_audit_output(output: dict) -> tuple[bool, str | None]:
    gate = output.get("gate_verdict")
    if gate not in ("pass", "fail", "uncertain"):
        return False, "gate_verdict 必须是 pass|fail|uncertain"
    reason = output.get("gate_reason")
    if not isinstance(reason, str) or not reason.strip():
        return False, "gate_reason 不能为空"
    if gate == "pass":
        chain = output.get("kill_chain")
        if not isinstance(chain, str) or not chain.strip():
            return False, "pass 需要非空 kill_chain"
        payloads = output.get("payloads")
        if not isinstance(payloads, list) or len(payloads) < 1:
            return False, "pass 需要 payloads 至少 1 条"
        if not isinstance(output.get("runtime_dependent"), bool):
            return False, "pass 需要 runtime_dependent 为 bool"
        layers = output.get("defense_layers")
        if layers is None:
            output["defense_layers"] = []
        elif not isinstance(layers, list):
            return False, "pass 需要 defense_layers 为数组"
    elif gate == "fail":
        chain = output.get("kill_chain")
        if not isinstance(chain, str) or not chain.strip():
            return False, "fail 需要非空 kill_chain"
        layers = output.get("defense_layers")
        if not isinstance(layers, list) or len(layers) < 1:
            return False, "fail 需要 defense_layers 为长度≥1 的数组"
    else:
        payloads = output.get("payloads")
        if isinstance(payloads, list) and len(payloads) > 0:
            return False, "uncertain 不得带非空 payloads"
    return True, None
```

不要在校验里改写 `gate_verdict`。`defense_layers` 缺省补 `[]` 仅允许 `pass`（避免调用方收到 None）；`fail` 不补，直接拒。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_ai_runner.py -q`

Expected: PASS

- [ ] **Step 5: Commit**（用户未要求则跳过）

---

### Task 2: 编排出口 D + 待复核保留工作区

**Files:**
- Modify: `backend/app/contexts/agent/orchestrator.py`
- Modify: `backend/app/contexts/agent/tasks.py`（`should_retain_hostdir`）
- Modify: `backend/tests/test_hostdir_cleanup.py`
- Modify: `backend/tests/test_reproduce_url.py`（断言整份 audit 含 `runtime_dependent` 原样前传）
- Test: `backend/tests/test_orchestrator.py`

**Interfaces:**
- Consumes: `previous_outputs["audit"]["gate_verdict"]`（`pass`/`fail`/`uncertain`）
- Produces:
  - `_skip_after_uncertain_audit(session, run_id, task_id, previous_outputs, on_node_event) -> bool`：仅当 gate==`uncertain` 时把 reproduce(index 4) 与 report(index 5) 标 `skipped`（已 `completed` 的不改），return True
  - `run_orchestration` 在 audit 执行后 **以及** 续跑复用 completed audit 后：先 `_skip_reproduce_if_gate_fail`，再 `_skip_after_uncertain_audit`
  - 主循环结束后若 audit 为 `uncertain`：`task.status="needs_review"`，`run.status="completed"`，`task.verdict=None`，return `{"status":"needs_review","verdict":None,...}`。**不要**再写成 `completed`
  - `should_retain_hostdir(status) -> bool`：`status in ("failed", "cancelled", "needs_review")`
  - `LIVE_TASK_STATUSES` **禁止**改动

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_orchestrator.py` 追加（沿用文件里 `_seed_task_run` / `session_factory`）：

```python
@pytest.mark.asyncio
async def test_gate_uncertain_skips_reproduce_and_report(session_factory):
    """uncertain → 出口 D：skip 4+5，task=needs_review，verdict 空。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun, Task
    from sqlalchemy import select

    async with session_factory() as session:
        task, run = await _seed_task_run(session)

        async def fake_source(ctx):
            return {"source_path": ctx.source_path}

        async def fake_profile(ctx):
            return {"is_web": True, "language": "python"}

        async def fake_env(ctx):
            return {"target_url": "http://localhost:5000", "compose_path": "x.yml"}

        async def fake_audit(ctx):
            return {"gate_verdict": "uncertain", "gate_reason": "描述对不上"}

        real_nodes = orch.NODE_ORDER
        patches = [
            patch.object(real_nodes[0], "execute", fake_source),
            patch.object(real_nodes[1], "execute", fake_profile),
            patch.object(real_nodes[2], "execute", fake_env),
            patch.object(real_nodes[3], "execute", fake_audit),
            patch.object(real_nodes[4], "execute", AsyncMock(side_effect=AssertionError("uncertain 不该 reproduce"))),
            patch.object(real_nodes[5], "execute", AsyncMock(side_effect=AssertionError("uncertain 不该 report"))),
        ]
        for p in patches:
            p.__enter__()

        result = await orch.run_orchestration(
            task_id=task.id, run_id=run.id, session=session,
            host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
        )

        assert result["status"] == "needs_review"
        assert result["verdict"] is None
        refreshed = await session.get(Task, task.id)
        assert refreshed.status == "needs_review"
        assert refreshed.verdict is None

        nodes = (await session.execute(
            select(NodeRun).where(NodeRun.run_id == run.id).order_by(NodeRun.node_index)
        )).scalars().all()
        by_key = {n.node_key: n for n in nodes}
        assert by_key["audit"].status == "completed"
        assert "描述对不上" in (by_key["audit"].output_json or "")
        assert by_key["reproduce"].status == "skipped"
        assert by_key["report"].status == "skipped"


@pytest.mark.asyncio
async def test_resume_reuses_audit_uncertain_skips_reproduce_and_report(session_factory):
    """续跑：已 completed 的 uncertain 不得进 reproduce/report。"""
    from app.contexts.agent import orchestrator as orch
    from app.contexts.task.models import NodeRun, Task
    from sqlalchemy import select

    async with session_factory() as session:
        task, run = await _seed_task_run(session)
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=0, node_key="source",
            status="completed", output_json='{"source_path": "/p"}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=1, node_key="profile",
            status="completed", output_json='{"is_web": true}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=2, node_key="env_ready",
            status="completed",
            output_json='{"target_url": "http://localhost:5000", "compose_path": "x.yml"}',
        ))
        session.add(NodeRun(
            run_id=run.id, task_id=task.id, node_index=3, node_key="audit",
            status="completed",
            output_json='{"gate_verdict": "uncertain", "gate_reason": "对不上"}',
        ))
        await session.flush()

        real_nodes = orch.NODE_ORDER
        with patch.object(real_nodes[4], "execute", AsyncMock(side_effect=AssertionError("续跑不得 reproduce"))), \
             patch.object(real_nodes[5], "execute", AsyncMock(side_effect=AssertionError("续跑不得 report"))):
            result = await orch.run_orchestration(
                task_id=task.id, run_id=run.id, session=session,
                host_workdir="/tmp/w", source_path="/tmp/w", runner_env={},
            )

        assert result["status"] == "needs_review"
        refreshed = await session.get(Task, task.id)
        assert refreshed.status == "needs_review"
        statuses = {n.node_key: n.status for n in (await session.execute(
            select(NodeRun).where(NodeRun.run_id == run.id)
        )).scalars().all()}
        assert statuses["reproduce"] == "skipped"
        assert statuses["report"] == "skipped"
```

`test_hostdir_cleanup.py`：把 `assert should_retain_hostdir("needs_review") is False` 改成 `is True`（文件里有两处相同测试，都改）。函数名 `test_cleanup_hostdir_on_success` 不再覆盖 needs_review，改成只断言 `completed` / `None` 为 False。

`test_reproduce_url.py` 的 audit 种子改为含 `runtime_dependent: True`，断言 `inp["audit"]["runtime_dependent"] is True`。

- [ ] **Step 2: 跑测试确认失败**

Run:

`d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py::test_gate_uncertain_skips_reproduce_and_report tests/test_orchestrator.py::test_resume_reuses_audit_uncertain_skips_reproduce_and_report tests/test_hostdir_cleanup.py tests/test_reproduce_url.py -q`

Expected: 出口 D 两测失败（task 仍 completed、report 被执行）；hostdir 的 needs_review 断言失败。

- [ ] **Step 3: 最小实现**

`orchestrator.py`：在 `_skip_reproduce_if_gate_fail` 旁新增 `_skip_after_uncertain_audit`，对 node_index 4 和 5 做与 reproduce skip 相同的「未 completed → skipped + on_node_event」。

两处 audit 分支（执行后、续跑 `continue` 前）都调用它。

主循环收尾：

```python
    audit_gate = previous_outputs.get("audit", {}).get("gate_verdict")
    if audit_gate == "uncertain":
        if await _is_cancelled(session, task, run):
            return _cancelled_result()
        task.verdict = None
        run.status = "completed"
        task.status = "needs_review"
        run.finished_at = datetime.now(timezone.utc)
        await session.commit()
        return {
            "status": "needs_review",
            "verdict": None,
            "report_data": None,
            "non_web": skipped_due_to_non_web,
            "repo_dirname": repo_dirname_from_outputs(previous_outputs),
            "project_path": previous_outputs.get("source", {}).get("project_path"),
        }
```

这段必须在「写成 completed」之前。现有 `test_gate_fail_skips_reproduce_and_sets_false_positive` 必须仍 PASS（fail 仍跑 report）。

`tasks.py`：

```python
def should_retain_hostdir(status: str | None) -> bool:
    return status in ("failed", "cancelled", "needs_review")
```

ReproduceNode 已整包前传 `audit`，**不要**为 `runtime_dependent` 改节点代码。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py tests/test_hostdir_cleanup.py tests/test_reproduce_url.py -q`

Expected: PASS（含原有 gate_fail / 取消 / 非 web）

- [ ] **Step 5: Commit**（用户未要求则跳过）

---

### Task 3: audit 工具白名单 + Bash 拒 HTTP

**Files:**
- Modify: `infrastructure/agent-runner/runner/run_one.py`
- Test: `backend/tests/test_run_one_bash_policy.py`（扩展）
- Test: `backend/tests/test_run_one_node_prompt.py`（可追加 allowed_tools 断言，若 `_build_options` 在 MagicMock 下不好测则只测纯函数）

**Interfaces:**
- Produces:
  - `AUDIT_DENY_RES`：`curl` / `wget` / `httpie` 的 `\b...\b` 正则列表，形态对齐 `ENV_READY_DENY_RES`
  - `_classify_bash(cmd, node_key)`：`node_key=="audit"` 时在全局黑名单之后走 `AUDIT_DENY_RES`
  - `_allowed_tools_for(node_key: str | None) -> list[str]`：
    - `profile` → `["Read","Grep","Glob"]`
    - `audit` → `["Read","Grep","Glob","Bash","WebSearch"]`
    - 其它（含 `None` / env_ready / reproduce / report）→ 现有 `Read Grep Glob Bash Write Edit WebFetch WebSearch`
  - `_build_options` 用 `_allowed_tools_for`；随后仍追加 `submit_result`
  - `NODE_INPUT_SCHEMAS["audit"]` 与 worker 对齐：enum 含 `uncertain`，properties 含 `runtime_dependent`，required 含 `gate_reason`

- [ ] **Step 1: 写失败测试**

扩展 `backend/tests/test_run_one_bash_policy.py`：

```python
from runner.run_one import _allowed_tools_for, _classify_bash


@pytest.mark.parametrize("cmd", ["curl http://x", "wget http://x", "httpie GET x"])
def test_audit_denies_http_clients(cmd):
    decision, reason = _classify_bash(cmd, node_key="audit")
    assert decision == "deny"
    assert reason and "blocked by policy" in reason


def test_reproduce_still_allows_curl():
    decision, _ = _classify_bash("curl -s http://x", node_key="reproduce")
    assert decision == "allow"


def test_audit_allowed_tools_are_read_plus_bash_websearch():
    tools = _allowed_tools_for("audit")
    assert tools == ["Read", "Grep", "Glob", "Bash", "WebSearch"]
    assert "Write" not in tools
    assert "WebFetch" not in tools


def test_env_ready_allowed_tools_keep_write():
    tools = _allowed_tools_for("env_ready")
    assert "Write" in tools and "WebFetch" in tools
```

保留现有 `test_audit_still_allows_npm`。

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_run_one_bash_policy.py -q`

Expected: 新测 FAIL（`_allowed_tools_for` 未定义；curl 在 audit 仍 allow）。

- [ ] **Step 3: 最小实现**

`run_one.py`：

```python
AUDIT_DENY_RES = [
    (re.compile(r"\bcurl\b"), "curl"),
    (re.compile(r"\bwget\b"), "wget"),
    (re.compile(r"\bhttpie\b"), "httpie"),
]


def _allowed_tools_for(node_key: str | None) -> list[str]:
    if node_key == "profile":
        return ["Read", "Grep", "Glob"]
    if node_key == "audit":
        return ["Read", "Grep", "Glob", "Bash", "WebSearch"]
    return ["Read", "Grep", "Glob", "Bash", "Write", "Edit", "WebFetch", "WebSearch"]
```

`_classify_bash`：`env_ready` 分支旁加 `if node_key == "audit":` 扫 `AUDIT_DENY_RES`。

`_build_options` 把内联三元换成 `"allowed_tools": _allowed_tools_for(node_key)`。

同步 `NODE_INPUT_SCHEMAS["audit"]` 与 Task 1 的 worker schema（enum / required / `runtime_dependent`）。MCP 工具只做 JSON Schema 提示，**真正拒形状仍是 worker `validate_output`**。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_run_one_bash_policy.py tests/test_run_one_node_prompt.py -q`

Expected: PASS

- [ ] **Step 5: Commit**（用户未要求则跳过）

---

### Task 4: 前端摘要与详情

**Files:**
- Modify: `frontend/src/shared/lib/nodeOutput.ts`
- Modify: `frontend/src/shared/components/NodeSteps.tsx`（非 compact 的 audit 行）
- Test: `frontend/src/shared/lib/nodeOutput.test.ts`

**Interfaces:**
- `summarizeNodeOutput('audit', ...)`：
  - `fail`：保持 `Gate 失败 · {gate_reason}`（无 reason 则 `Gate 失败（误报）`）
  - `pass` 且 `runtime_dependent === true`：`Gate 通过 · 运行时依赖`
  - 其它 `pass`：`Gate 通过`
  - `uncertain`：`待复核 · {gate_reason}`（无 reason 则 `待复核`）
- Produces: `formatAuditDetail(output, status?) -> string`：先 `summarizeNodeOutput('audit', ...)`；若 status 为 `completed`（或空）且 `kill_chain` 非空且尚未出现在摘要里，则 `\n` 拼上 kill_chain
- `NodeSteps` 非 compact：`node_key==='audit'` 时用 `formatAuditDetail` 代替 `summarizeNodeOutput`

- [ ] **Step 1: 写失败测试**

在 `frontend/src/shared/lib/nodeOutput.test.ts` 的 `summarizeNodeOutput` describe 内追加：

```typescript
  it('audit gate pass', () => {
    expect(
      summarizeNodeOutput('audit', { gate_verdict: 'pass', runtime_dependent: false }, 'completed'),
    ).toBe('Gate 通过')
  })

  it('audit runtime_dependent pass', () => {
    expect(
      summarizeNodeOutput(
        'audit',
        { gate_verdict: 'pass', runtime_dependent: true, gate_reason: '需登录态' },
        'completed',
      ),
    ).toBe('Gate 通过 · 运行时依赖')
  })

  it('audit uncertain', () => {
    expect(
      summarizeNodeOutput('audit', { gate_verdict: 'uncertain', gate_reason: '对不上 sink' }, 'completed'),
    ).toBe('待复核 · 对不上 sink')
  })
```

并新增（记得从 `./nodeOutput` import `formatAuditDetail`）：

```typescript
describe('formatAuditDetail', () => {
  it('appends kill_chain under the summary', () => {
    expect(
      formatAuditDetail(
        { gate_verdict: 'uncertain', gate_reason: '对不上', kill_chain: '只找到 /login' },
        'completed',
      ),
    ).toBe('待复核 · 对不上\n只找到 /login')
  })
})
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/shared/lib/nodeOutput.test.ts`

Expected: FAIL（uncertain 仍落到 `kill_chain || 审计完成`）

- [ ] **Step 3: 最小实现**

`nodeOutput.ts` 的 audit 分支：

```typescript
    case 'audit': {
      const gate = str(o.gate_verdict)
      if (gate === 'fail') return str(o.gate_reason) ? `Gate 失败 · ${str(o.gate_reason)}` : 'Gate 失败（误报）'
      if (gate === 'pass') {
        return o.runtime_dependent === true ? 'Gate 通过 · 运行时依赖' : 'Gate 通过'
      }
      if (gate === 'uncertain') return str(o.gate_reason) ? `待复核 · ${str(o.gate_reason)}` : '待复核'
      return str(o.kill_chain) || '审计完成'
    }
```

新增 `formatAuditDetail`。`NodeSteps.tsx` 的 `nodeSummary`：

```typescript
function nodeSummary(n: Pick<NodeRun, 'node_key' | 'status' | 'output' | 'error_message'>): string {
  if (n.status === 'failed' && n.error_message) return n.error_message
  if (n.node_key === 'audit') return formatAuditDetail(n.output, n.status)
  return summarizeNodeOutput(n.node_key, n.output, n.status)
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/shared/lib/nodeOutput.test.ts`

Expected: PASS

- [ ] **Step 5: Commit**（用户未要求则跳过）

---

### Task 5: 插件文案与权威文档

**Files:**
- Modify: `plugins/vuln-verify-expert/agents/auditor.md`
- Modify: `plugins/vuln-verify-expert/agents/reproducer.md`
- Modify: `plugins/vuln-verify-expert/skills/vuln-verify/SKILL.md`
- Modify: `docs/superpowers/specs/2026-08-12-platform-node-orchestration-design.md` §1.2 / §1.3 / 流程图出口
- Modify: `docs/agent-workflow.md` 分支出口列表
- Modify: `docs/development-guide.md` 文首与 §3.1 编排那一行（出口 D 点名，不新开 P0）

**Interfaces:**
- 无新 Python API。文案必须与 spec §2/§3/§6/§7 一致：三值、`runtime_dependent`、uncertain 不得带 payloads、禁止 HTTP/Write、无节点内重试。

- [ ] **Step 1: 改 auditor.md**

在「输入」后、「工作流」前插入专章（口吻对齐 env-builder 的「Crucible 平台」）：

```markdown
## Crucible 平台

你只做 Phase 1 / 2 / 2.5。**禁止进入 Phase 3**（发 HTTP 是节点 reproduce 的事）。
旁边的靶场由平台在节点 2 拉起，本节点不要去打。平台会拒绝 `curl`/`wget`/`WebFetch`/`Write`/`Edit`。

`gate_verdict`：
- `fail`：结构性阻断 / 无安全影响 / 路径证明断开
- `pass`：纸上 payload 通，或静态解不开/runtime-dependent（skill：不是 Quick-Stop）。此时必须交 `runtime_dependent`（bool）和至少 1 条 `payloads`
- `uncertain`：**仅**描述对不上代码、或锁不住具体 HTTP 危害。不得带非空 `payloads`。平台会直接待复核，不要自己循环

禁止把「不是 100% 确定但路径可能连得上」写成 `uncertain`——那是 `pass` + `runtime_dependent=true`。
```

`submit_result` 字段表改为与 spec §3 一致（`gate_verdict` 三值、`gate_reason` 必填、`runtime_dependent` 在 pass 必填）。删掉「只有 pass 或 fail」的旧句。

- [ ] **Step 2: 改 SKILL.md**

在 Phase 2.5 节末（Quick-Stop 模板之后、CWE mapping 之前）加：

```markdown
### Crucible 平台：节点 audit 停在 2.5

在 Crucible 上本 skill 被拆到三个节点：audit 只跑到本 Gate；Phase 3–4 是 reproduce；Phase 5/5.5 是 report。
audit 的 `gate_verdict`：结构性 Quick-Stop → `fail`；纸上通或 runtime-dependent → `pass`（后者 `runtime_dependent=true`）；描述对不上/锁不住 harm → `uncertain`（平台待复核，不打靶）。
**不要删除**下面的 Phase 3–5 正文（reproduce / 本地 Claude 仍用）。
```

- [ ] **Step 3: 改 reproducer.md**

输入里 `audit` 一行改为含 `runtime_dependent`。工作流加一句：若 `audit.runtime_dependent===true`，不要把 `payloads` 当成已证明可打通，按 `gate_reason` 补运行时事实再发 HTTP。

- [ ] **Step 4: 权威文档**

编排设计 §1.2 表增加：

`| D 待复核 | 节点 3 `gate_verdict=uncertain` | node 4 和 5 标 skipped | `needs_review`，verdict 空 |`

§1.3 audit output 改为三值 + `runtime_dependent` + `gate_reason` 必填。流程图节点 3 增加 uncertain 出口。

`docs/agent-workflow.md` 分支出口补一条平台出口 D（与误报 Quick-Stop 并列，注明是平台路由不是 skill 5 轮未复现）。

`docs/development-guide.md` 文首括号里的「3 分支出口」改为含待复核；§3.1 平台 6 节点那一行补「audit uncertain→needs_review」。

- [ ] **Step 5: 一致性检查**

确认：`auditor.md` / SKILL 平台节 / `ai_runner.NODE_INPUT_SCHEMAS` / `run_one.NODE_INPUT_SCHEMAS` 的 enum 都是 `pass|fail|uncertain`。全文搜索 `gate_verdict=fail` 旧两值描述，该改的改，测试夹具里合法 fail 不必改。

- [ ] **Step 6: Commit**（用户未要求则跳过）

---

## Self-Review

| Spec 节 | Task |
|---|---|
| §0 平台只校验形状并路由 | T1 |
| §0 uncertain 一次即 D，无循环 | T2（无 attempt 循环可测：AuditNode 仍单次 execute） |
| §0 硬拦截必须 | T3 |
| §0 待复核保留 hostdir；Lab 不改 live | T2（hostdir）；明确不改 `LIVE_TASK_STATUSES` |
| §2 映射 / runtime_dependent | T1 schema + T5 文案 + T2 前传测试 |
| §3 形状表 | T1 |
| §4 AuditNode 一次、不注入 target_url | 现状已满足；不新增循环。禁止在 T2/T3 给 AuditNode 加 attempt |
| §5 出口 D | T2 |
| §5.1 资源 | T2 |
| §6 工具策略 | T3 |
| §7 插件 | T5 |
| §8 前端 | T4 |
| §9 权威文档 | T5 |
| §10 测试表 | T1–T4 覆盖；不测「第 2 轮才 pass」 |

无 TBD。`_skip_after_uncertain_audit` 只在 T2 定义。`_allowed_tools_for` 只在 T3 定义。
