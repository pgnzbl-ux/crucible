# 节点 4 在线复现闸门与报告落库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** reproduce 一次 AI：校 6 档形状 + 拒 docker；成功路径同一会话交 8 节 Markdown；节点 5 成功路径只落库、误报路径才开 reporter。报告页按节解析 Markdown。

**Architecture:** `validate_output("reproduce")` 连续跑 spec §3.1（闸门）与 §3.2/§3.3（Markdown + 索引字段）。`ReportNode` 见 `reproduce.report_data` 则拷贝、不调 `run_ai_node`；否则跑 reporter 且 `final_verdict` 必须 `false_positive`。`render_report_md` 改为加标题后拼接字符串。前端 `ReportContent` 每节走现有 `MarkdownBody`。

**Tech Stack:** Python 3.11 / pytest / React 19 / vitest / Claude Agent SDK hooks

**Spec:** `docs/superpowers/specs/2026-08-14-reproduce-live-gate-design.md`（v1.1 已评审）

## Global Constraints

- 提交：`type(scope):` 英文，描述主体简体中文；**用户未明确要求则跳过每个 Task 的 Commit 步**
- Windows 提交用 `git commit -m "subject" -m "body"`，不用 HEREDOC
- 6 档不可机械证伪：禁止在 Python 里解析 HTTP 响应或 Markdown 质量
- 成功路径节点 5 **零** `run_ai_node`；禁止取消节点 5；禁止 fail 路径改纯模板
- 不装 Chromium；不核对截图落盘；不改写 MD 图片到 MinIO；不双渲染旧嵌套 JSON
- 不对调节点顺序；不拆 `vuln-verify` 目录；不拦 reproduce 的 `npm`/`pip`/`python urllib`
- 不改 `2026-08-14-audit-static-gate-design.md` 出口表
- 后端测试：`d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest ...`（cwd `backend`）
- 前端测试：`cd frontend && npx vitest run <files>`
- 当前工作树已有 lab / env_ready / audit 等未提交改动：**叠加上面改，不要还原无关 diff**

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `backend/app/contexts/agent/ai_runner.py` | 改 | reproduce/report 形状校验 + mock + schema |
| `backend/app/contexts/agent/nodes/report.py` | 改 | 双模：拷贝 vs `run_ai_node` |
| `backend/app/contexts/agent/orchestrator.py` | 改 | 收尾返回 `cvss` / `vulnerable_file` |
| `backend/app/contexts/agent/tasks.py` | 改 | 落库写索引列 |
| `backend/app/contexts/report/renderer.py` | 改 | 拼接 8 节 Markdown |
| `infrastructure/agent-runner/runner/run_one.py` | 改 | reproduce deny docker；schema；prompt 头 |
| `plugins/vuln-verify-expert/agents/reproducer.md` | 改 | Phase 5 + 平台专章 |
| `plugins/vuln-verify-expert/agents/reporter.md` | 改 | 仅误报路径 |
| `plugins/vuln-verify-expert/skills/vuln-verify/SKILL.md` | 改 | 成功路径 Phase 5 同会话 |
| `frontend/src/shared/lib/reportData.ts` | 改 | 8 节 Markdown 读取 |
| `frontend/src/shared/components/ReportContent.tsx` | 改 | Collapse + `MarkdownBody` |
| 权威文档 | 改 | 编排 §1.1/§1.3/§4.1、agent-workflow、development-guide、api-contract |
| `backend/tests/test_ai_runner.py` | 改 | 形状表驱动 |
| `backend/tests/test_report_node.py` | 新建 | 双模 |
| `backend/tests/test_run_one_bash_policy.py` | 改 | docker deny |
| `backend/tests/test_report_renderer.py` | 改 | 拼接 |
| `backend/tests/test_reproduce_url.py` | 改 | 一次 `run_ai_node` |
| `frontend/src/shared/lib/reportData.test.ts` | 改 | Markdown 节 |
| `frontend/src/shared/components/ReportContent.test.tsx` | 新建 | 报告页走 MD |

共享常量（只在 `ai_runner.py` 定义一处，renderer 可复制元组避免循环 import）：

```python
REPORT_SECTION_KEYS = (
    "product_intro", "vulnerability", "impact", "details",
    "reproduction", "poc_commands", "fix_suggestions", "reporting_decision",
)
REPORT_SECTION_TITLES = (
    "产品介绍", "漏洞描述", "影响范围", "漏洞详情",
    "漏洞复现", "POC", "修复建议", "报送判定",
)
```

---

### Task 1: reproduce / report 产出形状校验

**Files:**
- Modify: `backend/app/contexts/agent/ai_runner.py`
- Test: `backend/tests/test_ai_runner.py`

**Interfaces:**
- 抽出 `_validate_report_data_markdown(report_data) -> tuple[bool, str | None]`
- `validate_output("reproduce")`：先跑 §3.1，再跑 §3.2 + §3.3
- `validate_output("report")`：§3.2 + `final_verdict` 必须是 `false_positive`（唯一 AI 路径是出口 B）
- 错误字符串必须能被测试 `in` 命中（与 spec §3.1 / §3.2 一致）：
  - `verdict 必须是 confirmed|partial|code_reachable|code_smell|false_positive|not_reproduced`
  - `confirmed/partial 需要 reproduced=true`
  - `false_positive/not_reproduced 需要 reproduced=false`
  - `confirmed/partial 需要 evidence 至少 1 条`
  - `evidence 条目需要非空 type 和 detail`
  - `screenshots 必须是字符串数组`
  - `report_data 需要 8 节 Markdown 字符串`
  - `report_data.<key> 必须是非空字符串`
  - `cvss 需要 vector/base_score/severity`
  - `误报路径 final_verdict 必须是 false_positive`
- `_mock_output("reproduce")` 含 evidence + 8 节 md + cvss，能过 `validate_output`
- `_mock_output("report")` 为 8 节 md 字符串 + `final_verdict=false_positive`
- `NODE_INPUT_SCHEMAS["reproduce"]` 增加 `report_data` / `cvss` / `vulnerable_file`
- `NODE_INPUT_SCHEMAS["report"]["properties"]["report_data"]["description"]` 改为 8 节 Markdown 字符串
- `screenshots` 缺省视为 `[]`（在校验函数里写回，与 audit `defense_layers` 同风格）

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_ai_runner.py` **替换** `test_validate_report` / `test_validate_report_missing`，并追加（可与现有 audit 测试共存）：

```python
REPORT_MD_KEYS = (
    "product_intro", "vulnerability", "impact", "details",
    "reproduction", "poc_commands", "fix_suggestions", "reporting_decision",
)


def _md_sections(**over):
    base = {k: f"正文-{k}" for k in REPORT_MD_KEYS}
    base.update(over)
    return base


def _confirmed_ok(**over):
    base = {
        "verdict": "confirmed",
        "reproduced": True,
        "evidence": [{"type": "http_response", "detail": "200 dump"}],
        "screenshots": [],
        "cvss": {
            "vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",
            "base_score": 9.8,
            "severity": "Critical",
        },
        "vulnerable_file": "app/login.py",
        "report_data": _md_sections(),
    }
    rd = over.pop("report_data", None)
    base.update(over)
    if rd is not None:
        base["report_data"] = rd
    return base


@pytest.mark.parametrize(
    "output,needle",
    [
        ({"reproduced": True}, "verdict"),
        (_confirmed_ok(verdict="maybe"), "confirmed|partial|code_reachable"),
        (_confirmed_ok(reproduced=False), "confirmed/partial 需要 reproduced=true"),
        (
            _confirmed_ok(verdict="false_positive", reproduced=True, evidence=[]),
            "false_positive/not_reproduced 需要 reproduced=false",
        ),
        (_confirmed_ok(evidence=[]), "confirmed/partial 需要 evidence 至少 1 条"),
        (
            _confirmed_ok(evidence=[{"type": "http", "detail": ""}]),
            "evidence 条目需要非空 type 和 detail",
        ),
        (_confirmed_ok(screenshots="shot.png"), "screenshots 必须是字符串数组"),
        (_confirmed_ok(report_data={"product_intro": "x"}), "report_data 需要 8 节"),
        (
            _confirmed_ok(report_data=_md_sections(vulnerability={"type": "CWE-89"})),
            "report_data.vulnerability 必须是非空字符串",
        ),
        (_confirmed_ok(cvss={}), "cvss 需要 vector/base_score/severity"),
    ],
)
def test_validate_reproduce_shape_rejects(output, needle):
    ok, err = validate_output("reproduce", output)
    assert not ok
    assert needle in (err or "")


@pytest.mark.parametrize(
    "output",
    [
        _confirmed_ok(),
        _confirmed_ok(verdict="partial"),
        {
            "verdict": "not_reproduced",
            "reproduced": False,
            "cvss": {"vector": "AV:N/AC:L/PR:N/UI:N/C:N/I:N/A:N", "base_score": 0.0, "severity": "None"},
            "vulnerable_file": "",
            "report_data": _md_sections(),
        },
        {
            "verdict": "code_smell",
            "cvss": {"vector": "AV:N/AC:L/PR:N/UI:N/C:N/I:N/A:N", "base_score": 0.0, "severity": "None"},
            "report_data": _md_sections(),
        },
    ],
)
def test_validate_reproduce_shape_accepts(output):
    ok, err = validate_output("reproduce", output)
    assert ok, err


def test_validate_report_requires_markdown_and_false_positive():
    ok, err = validate_output(
        "report",
        {"report_data": {"x": 1}, "final_verdict": "confirmed"},
    )
    assert not ok
    assert "false_positive" in (err or "") or "8 节" in (err or "")

    ok2, err2 = validate_output(
        "report",
        {"report_data": _md_sections(), "final_verdict": "confirmed"},
    )
    assert not ok2
    assert "误报路径 final_verdict 必须是 false_positive" in (err2 or "")

    ok3, err3 = validate_output(
        "report",
        {"report_data": _md_sections(), "final_verdict": "false_positive"},
    )
    assert ok3, err3


def test_mock_reproduce_and_report_pass_validation():
    ok, err = validate_output("reproduce", _mock_output("reproduce", {}))
    assert ok, err
    ok2, err2 = validate_output("report", _mock_output("report", {}))
    assert ok2, err2
```

- [ ] **Step 2: 跑测试确认失败**

```
d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_ai_runner.py -q --tb=short
```

预期：新 reproduce/report 用例红；audit 旧用例仍绿。

- [ ] **Step 3: 实现校验与 mock**

在 `ai_runner.py`：

1. 定义 `REPORT_SECTION_KEYS`
2. `_validate_report_data_markdown`
3. `_validate_reproduce_output`（闸门表 + markdown + cvss；`vulnerable_file` 缺省 `""`；非 str 则失败）
4. `_validate_report_output`：markdown + `final_verdict == "false_positive"`
5. `validate_output` 在 required 字段之后分发 `reproduce` / `report`（与 audit 并列）
6. 更新 `_mock_output` 两支
7. 更新 `NODE_INPUT_SCHEMAS`

`cvss`：必须是 dict；`vector`/`severity` 为非空 str；`base_score` 为 `int` 或 `float`。

- [ ] **Step 4: 跑测试确认通过**

同 Step 2 命令，全部绿。

- [ ] **Step 5: Commit**（用户未要求则跳过）

---

### Task 2: ReportNode 双模 + 落库索引列

**Files:**
- Modify: `backend/app/contexts/agent/nodes/report.py`
- Modify: `backend/app/contexts/agent/orchestrator.py`（return 补 `cvss` / `vulnerable_file`）
- Modify: `backend/app/contexts/agent/tasks.py`（INSERT Report 时写索引列）
- Create: `backend/tests/test_report_node.py`
- Modify: `backend/tests/test_reproduce_url.py`（断言 `run_ai_node` 只调一次、无 `attempt`）

**Interfaces:**
- `ReportNode.execute`：
  - `rd = (ctx.previous_outputs.get("reproduce") or {}).get("report_data")` 为合法 8 节 → **不**调用 `run_ai_node`；返回 `{report_data, final_verdict: reproduce.verdict, cvss, vulnerable_file, authored_by: "reproduce"}`
  - 否则 `run_ai_node(node_key="report", ...)`；返回值加上 `authored_by: "reporter"`（校验已在 `run_ai_node`→`validate_output`）
- 拷贝路径若 markdown 不合法：`raise RuntimeError(err)`（节点 5 失败）
- `is_ai` 可保持 `True`（步骤条样式）；行为以是否调用 runner 为准
- `run_orchestration` 返回值增加 `cvss`、`vulnerable_file`（从 `report` output 取）
- `tasks.py` 创建 `Report(...)` 时：`cvss_score` / `severity` 来自 `orch_result["cvss"]`，`vulnerable_file` 来自 `orch_result["vulnerable_file"]`，`summary` 仍取 `product_intro[:500]`
- 抽出模块级函数便于测落库字段（放 `tasks.py` 顶部附近，避免把 Celery 任务拆乱）：

```python
def report_columns_from_orch_result(orch_result: dict) -> dict:
    """从编排收尾 dict 抽出 Report 索引列。"""
    rd = orch_result.get("report_data") or {}
    cvss = orch_result.get("cvss") or {}
    intro = rd.get("product_intro") if isinstance(rd, dict) else ""
    score = cvss.get("base_score") if isinstance(cvss, dict) else None
    return {
        "verdict": orch_result.get("verdict"),
        "cvss_score": float(score) if isinstance(score, (int, float)) else None,
        "severity": cvss.get("severity") if isinstance(cvss, dict) else None,
        "vulnerable_file": orch_result.get("vulnerable_file") or None,
        "summary": str(intro or "")[:500],
    }
```

- [ ] **Step 1: 写失败测试**

`backend/tests/test_report_node.py`：

```python
import sys, os
from unittest.mock import AsyncMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.report import ReportNode
from app.contexts.agent.tasks import report_columns_from_orch_result
from tests.test_ai_runner import _confirmed_ok, _md_sections


def _ctx(**prev):
    return NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp/w",
        source_path="/tmp/w", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs=prev,
    )


@pytest.mark.asyncio
async def test_report_node_copies_reproduce_without_ai():
    repro = _confirmed_ok()
    with patch("app.contexts.agent.ai_runner.run_ai_node", AsyncMock(side_effect=AssertionError("成功路径不该跑 AI"))) as mocked:
        out = await ReportNode().execute(_ctx(reproduce=repro, audit={"gate_verdict": "pass"}))
    mocked.assert_not_called()
    assert out["authored_by"] == "reproduce"
    assert out["final_verdict"] == "confirmed"
    assert out["report_data"]["product_intro"] == repro["report_data"]["product_intro"]
    assert out["cvss"]["base_score"] == 9.8


@pytest.mark.asyncio
async def test_report_node_runs_ai_when_reproduce_skipped():
    fake = AsyncMock(return_value={
        "report_data": _md_sections(),
        "final_verdict": "false_positive",
    })
    with patch("app.contexts.agent.ai_runner.run_ai_node", fake):
        out = await ReportNode().execute(_ctx(reproduce={}, audit={"gate_verdict": "fail"}))
    fake.assert_awaited_once()
    assert fake.await_args.kwargs["node_key"] == "report"
    assert out["authored_by"] == "reporter"
    assert out["final_verdict"] == "false_positive"


def test_report_columns_from_orch_result():
    cols = report_columns_from_orch_result({
        "verdict": "confirmed",
        "report_data": _md_sections(product_intro="产品X介绍" * 20),
        "cvss": {"base_score": 9.8, "severity": "Critical"},
        "vulnerable_file": "app/login.py",
    })
    assert cols["cvss_score"] == 9.8
    assert cols["severity"] == "Critical"
    assert cols["vulnerable_file"] == "app/login.py"
    assert cols["summary"].startswith("产品X")
    assert len(cols["summary"]) <= 500
```

在 `test_reproduce_url.py` 的 rewrite 测试末尾加：

```python
assert captured["node_key"] == "reproduce"
assert "attempt" not in captured
```

（`fake_run_ai_node` 已 `captured.update(kwargs)`。）

- [ ] **Step 2: 跑测试确认失败**

```
d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_report_node.py tests/test_reproduce_url.py -q --tb=short
```

- [ ] **Step 3: 实现双模与落库**

`report.py`：先校验 reproduce 的 `report_data`（调用 `ai_runner._validate_report_data_markdown` 或 `validate_output("reproduce", ...)`——**不要**对拷贝路径再跑整份 reproduce 闸门，以免 skipped `{}` 误伤。只调 markdown 校验）。

成功拷贝用 reproduce 已校验过的字段。

`tasks.py` 创建 Report 时：`**` 不要乱，显式传入 `report_columns_from_orch_result` 的键。

`orchestrator.py` completed return：

```python
"cvss": report_out.get("cvss") if report_out else None,
"vulnerable_file": report_out.get("vulnerable_file") if report_out else None,
```

needs_review 分支保持 `report_data: None`，不必加 cvss。

- [ ] **Step 4: 跑测试确认通过**

同 Step 2，外加：

```
d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_orchestrator.py tests/test_ai_runner.py -q --tb=short
```

编排测试仍 patch `ReportNode.execute`，应继续绿。

- [ ] **Step 5: Commit**（用户未要求则跳过）

---

### Task 3: reproduce 拒 docker + runner schema / prompt

**Files:**
- Modify: `infrastructure/agent-runner/runner/run_one.py`
- Test: `backend/tests/test_run_one_bash_policy.py`

**Interfaces:**
- `REPRODUCE_DENY_RES = [(re.compile(r"\bdocker\b"), "docker")]`
- `_classify_bash`：`node_key=="reproduce"` 时扫该名单（与 env_ready/audit 并列）
- `_allowed_tools_for("reproduce")` **不改**（默认全集，含 Write/WebFetch）
- `NODE_INPUT_SCHEMAS["reproduce"]` 与 worker 对齐（`report_data`/`cvss`/`vulnerable_file`）
- `NODE_INPUT_SCHEMAS["report"]` 的 `report_data` description 改为 8 节 Markdown
- `_build_prompt` reproduce 头补一句：同一会话交 8 节 Markdown 报告，禁止 docker
- report 头改为：仅误报路径；8 节 Markdown；`final_verdict=false_positive`

- [ ] **Step 1: 写失败测试**

在 `test_run_one_bash_policy.py` 追加：

```python
@pytest.mark.parametrize("cmd", ["docker compose up", "docker-compose up", "docker ps"])
def test_reproduce_denies_docker(cmd):
    decision, reason = _classify_bash(cmd, node_key="reproduce")
    assert decision == "deny"
    assert reason and "docker" in reason


def test_reproduce_still_allows_curl():
    decision, _ = _classify_bash("curl -s http://x", node_key="reproduce")
    assert decision == "allow"


def test_reproduce_keeps_write_and_webfetch():
    tools = _allowed_tools_for("reproduce")
    assert "Write" in tools and "WebFetch" in tools and "Bash" in tools
```

已有 `test_reproduce_still_allows_curl` 则不要重复定义，只加 deny / tools 两条。

- [ ] **Step 2: 跑测试确认失败**

```
d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_run_one_bash_policy.py -q --tb=short
```

- [ ] **Step 3: 实现 deny 与 schema**

注意：全局 `BLACKLIST_RES` **没有** docker，必须挂节点名单。不要把 docker 加进全局黑名单（env_ready 已有，reproduce 才需要，其它节点行为不变）。

- [ ] **Step 4: 跑测试确认通过**

同 Step 2。另搜 `test_run_one_bash_policy.py` 里 audit curl deny 仍在。

- [ ] **Step 5: Commit**（用户未要求则跳过）

---

### Task 4: `render_report_md` 拼接 Markdown

**Files:**
- Modify: `backend/app/contexts/report/renderer.py`
- Modify: `backend/tests/test_report_renderer.py`

**Interfaces:**
- `render_report_md(report_data)`：对 `REPORT_SECTION_KEYS` 逐节输出 `## {n}. {title}\n\n{body}\n`
- 某节缺失或非 str → 正文用 `—`，**不崩**
- **禁止**再访问 `vulnerability.get("type")` / `poc_commands` 当 list
- 节标题元组与 spec §3.2 表一致

- [ ] **Step 1: 重写测试夹具为 8 字符串**

替换 `SAMPLE_REPORT_DATA` 与断言：

```python
SAMPLE_REPORT_DATA = {
    "product_intro": "X 是一个基于 Flask 的博客系统。",
    "vulnerability": "CWE-89 SQL 注入。文件 `app/login.py:42`。入口 POST /login。",
    "impact": "- 受影响：all\n- 不受影响：—",
    "details": "### 4.1 代码审计\n\n`app/login.py` f-string 拼接 SQL。",
    "reproduction": "HTTP `0.0.0.0:5000`。\n\n![step1](img/step1_login.png)",
    "poc_commands": "```bash\ncurl -X POST http://localhost:5000/login\n```",
    "fix_suggestions": "P0: 参数化查询。",
    "reporting_decision": "建议报送。实际危害高。",
}


def test_render_all_8_sections():
    from app.contexts.report.renderer import render_report_md
    md = render_report_md(SAMPLE_REPORT_DATA)
    for i in range(1, 9):
        assert f"## {i}." in md
    assert "Flask" in md
    assert "CWE-89" in md
    assert "img/step1_login.png" in md


def test_render_does_not_look_up_nested_objects():
    from app.contexts.report.renderer import render_report_md
    md = render_report_md(SAMPLE_REPORT_DATA)
    assert "正文已是 Markdown" not in md  # 占位，保证函数不因 .get("type") 崩
    assert "## 2. 漏洞描述" in md


def test_render_handles_missing_optional_fields():
    from app.contexts.report.renderer import render_report_md
    md = render_report_md({"product_intro": "x"})
    assert "## 1." in md and "## 8." in md
    assert "—" in md
```

删除依赖嵌套 CVSS 向量的旧测试（CVSS 在 Report 列，不在 md 拼接里）。若正文里写了向量，那是 Agent 的事。

- [ ] **Step 2: 跑测试确认失败**

```
d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_report_renderer.py -q --tb=short
```

- [ ] **Step 3: 重写 renderer**

短函数即可，YAGNI 不要保留旧表格填空。

- [ ] **Step 4: 跑测试确认通过**

同 Step 2。

- [ ] **Step 5: Commit**（用户未要求则跳过）

---

### Task 5: 插件文案与权威文档

**Files:**
- Modify: `plugins/vuln-verify-expert/agents/reproducer.md`
- Modify: `plugins/vuln-verify-expert/agents/reporter.md`
- Modify: `plugins/vuln-verify-expert/skills/vuln-verify/SKILL.md`
- Modify: `docs/superpowers/specs/2026-08-12-platform-node-orchestration-design.md`
- Modify: `docs/agent-workflow.md`
- Modify: `docs/development-guide.md`
- Modify: `.claude/api-contract.md`

**不改** `references/reproduction-guide.md` / `report_template.md` 全文。

- [ ] **Step 1: 改 `reproducer.md`**

开篇改为兼容 `runtime_dependent`（不要写死「已走通 Gate」）。

在「输入」后插入：

```markdown
## Crucible 平台

你做 Phase 3 / 4 / **5**。8 节报告在本节点 `submit_result.report_data` 交齐，**不要**等节点 report 再写一遍。

靶场已由节点 2 拉起。禁止 `docker` / `compose`。必须对注入的 `target_url` 发 HTTP；平台不代打、不根据响应改档。

无真实浏览器：XSS/DOM 若只有 curl，不得 `confirmed`（用 `code_reachable` 或 `partial`）。

5 次变体在本容器内完成，不要等平台重开一轮。

`report_data` 每节是 Markdown 正文，不要写 `## 1.` 这种平台标题，不要嵌套 JSON。
```

`submit_result` 字段表改为 spec §3.4。工作流加 **Phase 5** 小节：按 `report_template.md` 8 节写 Markdown 正文 + 交 `cvss` / `vulnerable_file`。

保留已有 `runtime_dependent` 句。

- [ ] **Step 2: 改 `reporter.md`**

整篇收成「仅误报路径」：audit fail、无 reproduce；禁止 HTTP；同构 8 节 Markdown；`final_verdict` 必须 `false_positive`。删「结构化 JSON 不是 markdown」「供导出 Word」完成条件。

- [ ] **Step 3: 改 SKILL.md**

把现有「Phase 5/5.5 是 report」平台注改为：

```markdown
### Crucible 平台：节点切分

- audit 只跑到 Phase 2.5 Gate
- 成功路径：reproduce = Phase 3–5（一次会话交证据 + 8 节 Markdown）；无 Chromium；docker 由平台管
- 误报路径（audit `fail`）：平台跳过 reproduce，节点 report 出同构误报稿
- 待复核（`uncertain`）：不打靶、不出报告
- 平台正本是 `reports.report_data` 的 8 节 Markdown 字段，不是磁盘 `report.md`

**不要删除**下面的 Phase 3–5 正文（本地 Claude 仍用）。
```

Phase 3 节可再加一句短注：Crucible 上变体循环在 reproduce 容器内，不是平台节点循环。

- [ ] **Step 4: 权威文档**

编排设计：

- §1.1 节点 5：`■代码落库；仅出口 B 为 □AI`
- 流程图节点 4 产出含 `report_data` 8 md；节点 5 标注拷贝/误报 AI
- §1.3 reproduce / report 行与 spec §3 对齐
- §4.1 嵌套 JSON **改为** 8 个字符串键；注明旧树作废
- §4.2：成功路径节点 5 不默认开 Agent

`docs/agent-workflow.md` §2 回退环那条改为：**Agent 容器内**循环（上限 5 次），不是平台出口。补一句：成功路径报告由 reproduce 交，节点 5 落库。

`docs/development-guide.md` §3.1 平台 6 节点那一行补「reproduce 一次 AI、拒 docker、成功路径报告同会话」。

`.claude/api-contract.md` Report 详情：`report_data` 8 节值为 Markdown 字符串；导出 md = 平台加 `## N.` 后拼接。

- [ ] **Step 5: 一致性检查**

全文搜索（仓库，排除 `docs/superpowers/plans/2026-08-12*` 历史计划）：

- `report_data 是结构化 JSON`
- `禁止写报告（那是节点 report）`（reproducer 旧句，应已删）
- `poc_commands: ["curl` 作为 schema 正本（测试夹具/mock 旧数据在 T1/T4 已换）

`reproducer.md` 必须同时含「禁止 docker」「无真实浏览器」「Phase 5」。
`reporter.md` 必须含「仅误报」。

- [ ] **Step 6: Commit**（用户未要求则跳过）

---

### Task 6: 前端报告页 Markdown

**Files:**
- Modify: `frontend/src/shared/lib/reportData.ts`
- Modify: `frontend/src/shared/lib/reportData.test.ts`
- Modify: `frontend/src/shared/components/ReportContent.tsx`
- Create: `frontend/src/shared/components/ReportContent.test.tsx`

**Interfaces:**
- `REPORT_SECTIONS: { key, label }[]` 8 项，label 为 `§N 标题`
- `asMarkdownSection(value: unknown): string | null` — 非空 string 才返回，object/array/空串 → `null`
- `ReportContent`：顶栏 **只读** `report.verdict` / `cvss_score` / `severity` / `vulnerable_file`（不要 `asRecord(rd.vulnerability)`）
- 每节 Collapse children：`asMarkdownSection` 有值 → `<MarkdownBody source={...} />`；`null` → 「报告格式已升级，请重新跑任务」或 `—`
- 删除 `ReportDetails` 及 Table/Descriptions 嵌套节
- `report_data` 整体为 null 时仍回退 `reasoning`（现有行为）
- **不改** `summarizeNodeOutput('reproduce')`

- [ ] **Step 1: 写失败测试**

`reportData.test.ts` 追加：

```typescript
import { asMarkdownSection, REPORT_SECTIONS } from './reportData'

it('has 8 report sections', () => {
  expect(REPORT_SECTIONS).toHaveLength(8)
  expect(REPORT_SECTIONS.map((s) => s.key)).toContain('product_intro')
})

it('only accepts non-empty markdown strings', () => {
  expect(asMarkdownSection('一段 **md**')).toBe('一段 **md**')
  expect(asMarkdownSection('  ')).toBeNull()
  expect(asMarkdownSection({ type: 'CWE-89' })).toBeNull()
  expect(asMarkdownSection(['curl'])).toBeNull()
})
```

`ReportContent.test.tsx`（`renderToStaticMarkup` + antd `App` 包裹，对齐 `MarkdownBody.test.tsx`）：

```tsx
import { App } from 'antd'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { ReportContent } from './ReportContent'
import type { ReportDetail } from '../lib/api'

const base = {
  id: 'r1', task_id: 't1', run_id: 'u1', owner_id: 'o1',
  status: 'generated', conclusion: 'unconfirmed', title: '报告',
  summary: null, reasoning: null, evidence_summary: null, artifact_key: null,
  md_artifact_key: null, docx_artifact_key: null, published_at: null,
  created_at: '', updated_at: '', evidence: [],
} as ReportDetail

describe('ReportContent', () => {
  it('renders markdown sections and index columns', () => {
    const html = renderToStaticMarkup(
      <App>
        <ReportContent
          report={{
            ...base,
            verdict: 'confirmed',
            cvss_score: 9.8,
            severity: 'Critical',
            vulnerable_file: 'app/login.py',
            report_data: {
              product_intro: '这是 **产品**',
              vulnerability: 'CWE-89',
              impact: 'all',
              details: '`file.py`',
              reproduction: '步骤 1',
              poc_commands: '```bash\ncurl x\n```',
              fix_suggestions: '参数化',
              reporting_decision: '报送',
            },
          }}
        />
      </App>,
    )
    expect(html).toMatch(/<strong>产品<\/strong>/)
    expect(html).toContain('app/login.py')
    expect(html).toContain('9.8')
    expect(html).not.toMatch(/漏洞文件[\s\S]*asRecord/)
  })

  it('shows upgrade hint for nested leftover JSON', () => {
    const html = renderToStaticMarkup(
      <App>
        <ReportContent
          report={{
            ...base,
            verdict: null, cvss_score: null, severity: null, vulnerable_file: null,
            report_data: { product_intro: { nested: true } },
          }}
        />
      </App>,
    )
    expect(html).toMatch(/报告格式已升级/)
  })
})
```

若 antd `App` 在 vitest 里报 context，改为只测 `asMarkdownSection` + 在 `ReportContent` 把导出按钮包在已有 `App.useApp` 外、节渲染抽纯函数。优先让组件测试过；抽函数是退路，不要为测试引入新抽象层。

- [ ] **Step 2: 跑测试确认失败**

```
cd frontend && npx vitest run src/shared/lib/reportData.test.ts src/shared/components/ReportContent.test.tsx
```

- [ ] **Step 3: 实现**

`ReportContent` import `MarkdownBody`。保留导出 JSON/md 按钮。顶栏 Descriptions 三列：判定 / CVSS / 漏洞文件。

- [ ] **Step 4: 跑测试确认通过**

同 Step 2，外加：

```
npx vitest run src/shared/components/MarkdownBody.test.tsx src/shared/lib/nodeOutput.test.ts
npx tsc --noEmit
```

- [ ] **Step 5: Commit**（用户未要求则跳过）

---

## Self-Review

| Spec 节 | Task |
|---|---|
| §0 一次 AI、5 次变体在容器内 | T1 mock + T2 reproduce 无 attempt + T5 文案 |
| §0 只让 Agent 发 HTTP | 不实现探活；T3 允许 curl |
| §0 平台只校形状 | T1 |
| §0 拒 docker / 无 Chromium | T3 + T5 |
| §0 成功路径节点 5 不调模型 | T2 |
| §0 fail 路径同构误报报告 | T1 report 校验 + T2 AI 分支 + T5 reporter |
| §0 8 节 Markdown 正本 | T1 + T4 + T6 |
| §0 索引列不从 MD 解析 | T2 columns + T6 顶栏 |
| §3.1 闸门表 | T1 |
| §3.2 / §3.3 | T1 |
| §4 ReproduceNode 一次 | 现状 + T2 断言 |
| §4.1 产物流 | T2 落库；不改 archive 扫描 |
| §5 ReportNode 双模 | T2 |
| §6 工具策略 | T3 |
| §7 插件 | T5 |
| §8 权威文档 | T5 |
| §9 前端 | T6；明确不改 reproduce 步骤条摘要 |
| §10 测试表 | T1–T4、T6；不测截图文件、不测 MinIO 图 |

无 TBD。`_validate_report_data_markdown` 只在 T1 定义。`REPRODUCE_DENY_RES` 只在 T3 定义。`report_columns_from_orch_result` 只在 T2 定义。
