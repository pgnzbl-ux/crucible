# 审计射击清单 → 复现直打 → 完整 PoC 入库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 audit `pass` 交出可发的 HTTP 请求模板；reproduce 第一枪必须打 `payloads[0]`，失败后可深挖同一 `core_claim`（不设次数上限）；打通后 reproduce 写完整 PoC，入库并用现有 Markdown 展示。

**Architecture:** worker `validate_output` 收紧 audit payload 对象与 reproduce `poc` 形状；`ReportNode` 用代码覆盖 `report_data.poc_commands` 并原样挂 `poc`；`reports` 四列存正本；前端 §6 优先渲 `poc_code`。平台不按次数循环、不跑 PoC、不解析是否换题。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 / Alembic / pytest / React 19 / vitest

**Spec:** `docs/superpowers/specs/2026-08-17-audit-reproduce-poc-handoff-design.md`（v1.0 已确认）

## Global Constraints

- 提交：`type(scope):` 英文，描述主体简体中文；**用户未明确要求则跳过每个 Task 的 Commit 步**（仓库 git-workflow）
- Windows 提交用 `git commit -m "subject" -m "body"`，不用 HEREDOC
- 平台只校验形状，禁止解析 kill_chain 真伪、禁止执行 PoC、禁止数 HTTP 次数
- 不对调 audit / env_ready；不拆写 PoC 为第 7 节点；不加 CodeMirror；不改桌面 `plugins/`
- 错误字符串必须能被测试 `in` 命中（见各 Task Interfaces）
- 后端测试：`d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest <args> -v`（cwd `backend`）
- 前端测试：`cd frontend && npx vitest run <file>`
- `run_one.py` 的 `NODE_INPUT_SCHEMAS` 必须与 `ai_runner.NODE_INPUT_SCHEMAS` 同形（submit_result 工具用容器内那份）
- 叠加上面改，不要还原无关 diff

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `backend/app/contexts/agent/ai_runner.py` | 改 | audit payload/`core_claim`/`unresolved_facts` 校验；reproduce `poc` 校验；`render_poc_markdown` / `apply_poc_to_report_output`；mock |
| `infrastructure/agent-runner/runner/run_one.py` | 改 | submit_result schema 与 worker 对齐 |
| `infrastructure/agent-runner/node-skills/{audit,reproduce,report}/SKILL.md` | 改 | 第一枪、判定即停、PoC 正本、报告不改代码 |
| `backend/app/contexts/agent/nodes/report.py` | 改 | AI 返回后覆盖 `poc_commands` 并挂 `poc` |
| `backend/app/contexts/agent/orchestrator.py` | 改 | 收尾 dict 带 `poc` |
| `backend/app/contexts/agent/tasks.py` | 改 | `report_columns_from_orch_result` + 建 Report 写四列 |
| `backend/app/contexts/report/models.py` | 改 | `poc_*` 四列 |
| `backend/app/contexts/report/schemas.py` | 改 | `ReportDetail` 四字段 |
| `backend/app/contexts/report/service.py` | 改 | `_to_detail` 带回四字段 |
| `backend/alembic/versions/f1a8c3d04e25_report_poc_columns.py` | 新建 | 迁移 |
| `frontend/src/shared/lib/api.ts` | 改 | `ReportDetail` 四字段 |
| `frontend/src/shared/lib/reportData.ts` | 改 | `pocToMarkdown` |
| `frontend/src/shared/components/ReportContent.tsx` | 改 | §6 优先 `poc_code` |
| 权威文档 | 改 | 编排 §1.3、agent-workflow、live-gate 文首、api-contract、development-guide 一句 |

---

### Task 1: audit 射击清单形状

**Files:**
- Modify: `backend/tests/test_ai_runner.py`（`_pass_ok` / 表驱动）
- Modify: `backend/app/contexts/agent/ai_runner.py`（`_validate_audit_output` / `_mock_output` / `NODE_INPUT_SCHEMAS["audit"]` / `NODE_OUTPUT_SCHEMAS["audit"]`）
- Modify: `infrastructure/agent-runner/runner/run_one.py`（`NODE_INPUT_SCHEMAS["audit"]` 与 worker 同形）
- Test: `backend/tests/test_ai_runner.py`

**Interfaces:**
- Consumes: 现有 `validate_output("audit", output) -> tuple[bool, str | None]`
- Produces: 同签名。`pass` 额外：
  - `core_claim` 非空字符串
  - `payloads` 为对象数组，每条 `method`/`path`/`expected_observable` 均为非空字符串；`path` 必须以 `/` 开头
  - `runtime_dependent is True` 时 `unresolved_facts` 为长度≥1 的字符串数组（元素 `strip()` 非空）
- 错误字符串（`in` 命中）：
  - `pass 需要非空 core_claim`
  - `pass 的 payloads 必须是请求模板对象`
  - `payload path 必须以 / 开头`
  - `pass 需要 unresolved_facts 为非空字符串数组`
- `_mock_output("audit")` 含 `core_claim` 与一条对象 payload，且 `validate_output` 通过
- `NODE_INPUT_SCHEMAS["audit"]["properties"]` 含 `core_claim`、`unresolved_facts`；`payloads.items` 为 object（`required`: `method`,`path`,`expected_observable`）。`required` 仍只有 `gate_verdict`,`gate_reason`（fail/uncertain 不强制 core_claim）

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_ai_runner.py` 把 `_pass_ok` 改为对象 payload，并追加拒绝/接受用例：

```python
def _payload_ok(**over):
    base = {
        "method": "POST",
        "path": "/login",
        "expected_observable": "响应回显管理员密码哈希",
    }
    base.update(over)
    return base


def _pass_ok(**over):
    base = {
        "gate_verdict": "pass",
        "gate_reason": "Q1 有危害。Q2 连通。Q3 无阻断。",
        "kill_chain": "login → sql",
        "defense_layers": [],
        "payloads": [_payload_ok()],
        "runtime_dependent": False,
        "core_claim": "匿名攻击者可经 /login 读出管理员密码哈希",
    }
    base.update(over)
    return base
```

在 `test_validate_audit_shape_rejects` 的 parametrize 列表**追加**（保留原有项；其中 `_pass_ok(payloads=[])` 仍有效）：

```python
        (_pass_ok(core_claim=""), "pass 需要非空 core_claim"),
        (_pass_ok(payloads=["' OR 1=1"]), "pass 的 payloads 必须是请求模板对象"),
        (_pass_ok(payloads=[_payload_ok(path="login")]), "payload path 必须以 / 开头"),
        (_pass_ok(runtime_dependent=True, unresolved_facts=[]), "pass 需要 unresolved_facts 为非空字符串数组"),
        (_pass_ok(runtime_dependent=True), "pass 需要 unresolved_facts 为非空字符串数组"),
```

把 `test_validate_audit_shape_accepts` 里的 `_pass_ok(runtime_dependent=True)` 改成：

```python
        _pass_ok(runtime_dependent=True, unresolved_facts=["需登录后的 CSRF token"]),
```

追加：

```python
def test_audit_input_schema_has_payload_template():
    spec = NODE_INPUT_SCHEMAS["audit"]
    assert "core_claim" in spec["properties"]
    items = spec["properties"]["payloads"]["items"]
    assert items["type"] == "object"
    assert set(items["required"]) == {"method", "path", "expected_observable"}
```

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_ai_runner.py::test_validate_audit_shape_rejects tests/test_ai_runner.py::test_audit_mock_output_passes_validation tests/test_ai_runner.py::test_audit_input_schema_has_payload_template -v`

Expected: FAIL（字符串 payload 仍被旧校验当成 pass；缺 `core_claim` 仍 ok；schema 无 `core_claim`）

- [ ] **Step 3: 最小实现**

在 `ai_runner.py` 的 `_validate_audit_output`，`gate == "pass"` 分支在现有 `kill_chain` / `payloads 至少 1 条` / `runtime_dependent` / `defense_layers` 之后追加：

```python
        claim = output.get("core_claim")
        if not isinstance(claim, str) or not claim.strip():
            return False, "pass 需要非空 core_claim"
        for item in payloads:
            if not isinstance(item, dict):
                return False, "pass 的 payloads 必须是请求模板对象"
            method = item.get("method")
            path = item.get("path")
            expected = item.get("expected_observable")
            if not isinstance(method, str) or not method.strip():
                return False, "pass 的 payloads 必须是请求模板对象"
            if not isinstance(path, str) or not path.strip():
                return False, "pass 的 payloads 必须是请求模板对象"
            if not isinstance(path, str) or not path.startswith("/"):
                return False, "payload path 必须以 / 开头"
            if not isinstance(expected, str) or not expected.strip():
                return False, "pass 的 payloads 必须是请求模板对象"
        if output.get("runtime_dependent") is True:
            facts = output.get("unresolved_facts")
            if (
                not isinstance(facts, list)
                or len(facts) < 1
                or any(not isinstance(f, str) or not f.strip() for f in facts)
            ):
                return False, "pass 需要 unresolved_facts 为非空字符串数组"
```

`_mock_output("audit")` 改为：

```python
    if node_key == "audit":
        return {
            "kill_chain": "[Mock] entry → sink(模拟调用链)",
            "defense_layers": [{"name": "validator", "bypass": "模拟绕过"}],
            "payloads": [{
                "method": "GET",
                "path": "/mock",
                "expected_observable": "[Mock] 回显 marker",
            }],
            "gate_verdict": "pass",
            "gate_reason": "[Mock] 三问通过",
            "runtime_dependent": False,
            "core_claim": "[Mock] 匿名可读 /mock 回显",
        }
```

`NODE_OUTPUT_SCHEMAS["audit"]["optional"]` 加入 `core_claim`、`unresolved_facts`。

`NODE_INPUT_SCHEMAS["audit"]["properties"]` 增加：

```python
            "core_claim": {"type": "string", "description": "一条 HTTP 可观察危害"},
            "unresolved_facts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "runtime_dependent 时缺的运行时事实",
            },
```

并把 `payloads` 改为带 `items` 的 object schema（`required`: `method`,`path`,`expected_observable`；可选 `headers`/`body`/`content_type`）。

把同一 `audit` properties 块同步到 `infrastructure/agent-runner/runner/run_one.py` 的 `NODE_INPUT_SCHEMAS["audit"]`。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_ai_runner.py::test_validate_audit_shape_rejects tests/test_ai_runner.py::test_validate_audit_shape_accepts tests/test_ai_runner.py::test_audit_mock_output_passes_validation tests/test_ai_runner.py::test_audit_input_schema_has_payload_template tests/test_ai_runner.py::test_audit_input_schema_has_uncertain_and_runtime_dependent -v`

Expected: PASS

- [ ] **Step 5: Commit**（用户未要求则跳过）

```bash
git add backend/tests/test_ai_runner.py backend/app/contexts/agent/ai_runner.py infrastructure/agent-runner/runner/run_one.py
git commit -m "feat(agent): 收紧 audit 射击清单形状" -m "pass 必须交出 core_claim 与可发的请求模板，供 reproduce 第一枪直接使用。"
```

---

### Task 2: reproduce PoC 形状

**Files:**
- Modify: `backend/tests/test_ai_runner.py`（`_confirmed_ok` / 表驱动）
- Modify: `backend/app/contexts/agent/ai_runner.py`（`_validate_reproduce_output` / `_mock_output("reproduce")` / schemas）
- Modify: `infrastructure/agent-runner/runner/run_one.py`（`NODE_INPUT_SCHEMAS["reproduce"]` 增加 `poc`）
- Test: `backend/tests/test_ai_runner.py`

**Interfaces:**
- Consumes: Task 1 之后的 `validate_output`；现有 `_confirmed_ok` / `_not_reproduced_ok`
- Produces:
  - `_validate_poc(poc: dict, *, required: bool) -> tuple[bool, str | None]`
  - `confirmed`/`partial` 必须有合法 `poc`；其余 verdict 若 `poc` 为含非空 `code` 的 dict 则失败
  - 错误字符串：
    - `confirmed/partial 需要 poc.language/filename/code/usage`
    - `poc.language 必须是 python|bash|other`
    - `非 python 的 poc 需要 language_reason`
    - `未确认判定不得交 poc`
- `_mock_output("reproduce")` 含 python `poc`，且 `validate_output("reproduce", ...)` 通过
- MCP schema 的 `required` **不加** `poc`（仅 confirmed 才有）

- [ ] **Step 1: 写失败测试**

在 `test_ai_runner.py` 增加 helper，并让 `_confirmed_ok` 默认带 poc（否则现有 accept 会在实现后失败）：

```python
def _poc_ok(**over):
    base = {
        "language": "python",
        "filename": "poc.py",
        "code": (
            "import argparse\n"
            "parser = argparse.ArgumentParser()\n"
            "parser.add_argument('--url', required=True)\n"
            "args = parser.parse_args()\n"
            "print(args.url)\n"
            "raise SystemExit(0)\n"
        ),
        "usage": "python poc.py --url http://host.docker.internal:8080",
    }
    base.update(over)
    return base
```

`_confirmed_ok` 的 `base` 增加 `"poc": _poc_ok()`。`_not_reproduced_ok` **不要**加 `poc`。

在 `test_validate_reproduce_shape_rejects` 追加：

```python
        ({**_confirmed_ok(), "poc": None}, "confirmed/partial 需要 poc.language/filename/code/usage"),
        (_confirmed_ok(poc=_poc_ok(language="perl", language_reason="")), "poc.language 必须是 python|bash|other"),
        (_confirmed_ok(poc=_poc_ok(language="bash")), "非 python 的 poc 需要 language_reason"),
        (_not_reproduced_ok(poc=_poc_ok()), "未确认判定不得交 poc"),
```

注意：`_confirmed_ok(poc=_poc_ok(language="bash"))` 缺 `language_reason`。bash 合法语言但缺 reason。

`test_validate_reproduce_shape_accepts` 追加一条：

```python
        _confirmed_ok(poc=_poc_ok(language="bash", language_reason="需 openssl s_client 握手")),
```

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_ai_runner.py::test_validate_reproduce_shape_rejects tests/test_ai_runner.py::test_mock_reproduce_and_report_pass_validation -v`

Expected: FAIL（缺 poc 仍通过；mock 无 poc 在 Step 3 后也会先红若先跑 mock）

- [ ] **Step 3: 最小实现**

在 `ai_runner.py` 增加（放在 `_validate_reproduce_output` 之前）：

```python
_POC_LANGS = ("python", "bash", "other")


def _poc_has_code(poc: Any) -> bool:
    return isinstance(poc, dict) and isinstance(poc.get("code"), str) and bool(str(poc.get("code")).strip())


def _validate_poc(poc: Any, *, required: bool) -> tuple[bool, str | None]:
    if not required:
        if poc in (None, {}) or poc is False:
            return True, None
        if _poc_has_code(poc):
            return False, "未确认判定不得交 poc"
        return True, None
    if not isinstance(poc, dict):
        return False, "confirmed/partial 需要 poc.language/filename/code/usage"
    lang = poc.get("language")
    if lang not in _POC_LANGS:
        return False, "poc.language 必须是 python|bash|other"
    for key in ("filename", "code", "usage"):
        val = poc.get(key)
        if not isinstance(val, str) or not val.strip():
            return False, "confirmed/partial 需要 poc.language/filename/code/usage"
    if lang != "python":
        reason = poc.get("language_reason")
        if not isinstance(reason, str) or not reason.strip():
            return False, "非 python 的 poc 需要 language_reason"
    return True, None
```

在 `_validate_reproduce_output` 末尾、`return True, None` 之前：

```python
    need_poc = verdict in _CONFIRMED_VERDICTS
    ok, err = _validate_poc(output.get("poc"), required=need_poc)
    if not ok:
        return False, err
```

`_mock_output("reproduce")` 增加：

```python
            "poc": {
                "language": "python",
                "filename": "poc.py",
                "code": (
                    "import argparse\n"
                    "parser = argparse.ArgumentParser()\n"
                    "parser.add_argument('--url', required=True)\n"
                    "args = parser.parse_args()\n"
                    "print('[Mock]', args.url)\n"
                    "raise SystemExit(0)\n"
                ),
                "usage": "python poc.py --url http://host.docker.internal:8080",
            },
```

`NODE_OUTPUT_SCHEMAS["reproduce"]["optional"]` 加入 `poc`。

`NODE_INPUT_SCHEMAS["reproduce"]["properties"]["poc"]`：

```python
            "poc": {
                "type": "object",
                "description": "仅 confirmed/partial 的完整 PoC",
                "properties": {
                    "language": {"type": "string", "enum": ["python", "bash", "other"]},
                    "filename": {"type": "string"},
                    "code": {"type": "string"},
                    "usage": {"type": "string"},
                    "language_reason": {"type": "string"},
                },
            },
```

同步到 `run_one.py` 的 reproduce schema。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_ai_runner.py::test_validate_reproduce_shape_rejects tests/test_ai_runner.py::test_validate_reproduce_shape_accepts tests/test_ai_runner.py::test_mock_reproduce_and_report_pass_validation tests/test_ai_runner.py::test_txt_screenshot_rejected_even_if_file_exists -v`

Expected: PASS

- [ ] **Step 5: Commit**（用户未要求则跳过）

```bash
git add backend/tests/test_ai_runner.py backend/app/contexts/agent/ai_runner.py infrastructure/agent-runner/runner/run_one.py
git commit -m "feat(agent): reproduce 打通后必须交完整 poc" -m "confirmed/partial 以结构化 poc 为正本；未确认判定禁止带代码。"
```

---

### Task 3: 蒸馏 skill 文案

**Files:**
- Modify: `infrastructure/agent-runner/node-skills/audit/SKILL.md`
- Modify: `infrastructure/agent-runner/node-skills/reproduce/SKILL.md`
- Modify: `infrastructure/agent-runner/node-skills/report/SKILL.md`
- Test: `backend/tests/test_run_one_node_prompt.py`（已有 skill 加载测试，补关键字断言）

**Interfaces:**
- Consumes: 容器 `_load_node_skill(node_key)` 读上述 SKILL.md
- Produces: audit skill 要求对象形 `payloads` + `core_claim`；reproduce skill 删除「上限 5 次」，写明第一枪打 `payloads[0]`、判定即停、打通后交 `poc`；report skill 声明 §6 由平台覆盖、禁止改写 `poc.code`

- [ ] **Step 1: 写失败测试**

在 `backend/tests/test_run_one_node_prompt.py` 追加：

```python
def test_audit_skill_requires_payload_template():
    text = _load_node_skill("audit")
    assert "core_claim" in text
    assert "expected_observable" in text
    assert "method" in text


def test_reproduce_skill_drops_attempt_cap_and_requires_first_shot():
    text = _load_node_skill("reproduce")
    assert "上限 5 次" not in text
    assert "payloads[0]" in text
    assert "判定即停" in text
    assert "poc" in text
    assert "python" in text.lower()


def test_report_skill_poc_is_platform_owned():
    text = _load_node_skill("report")
    assert "覆盖" in text or "正本" in text
    assert "禁止改写" in text
```

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_run_one_node_prompt.py::test_audit_skill_requires_payload_template tests/test_run_one_node_prompt.py::test_reproduce_skill_drops_attempt_cap_and_requires_first_shot tests/test_run_one_node_prompt.py::test_report_skill_poc_is_platform_owned -v`

Expected: FAIL

- [ ] **Step 3: 改三份 SKILL.md**

`audit/SKILL.md`：原料句补 `core_claim`。完成表 `pass` 行改为：非空 `core_claim`；`payloads` 每条含 `method`/`path`（以 `/` 开头）/`expected_observable`；`runtime_dependent=true` 时非空 `unresolved_facts`。Phase 2「还原真实请求格式」后加一句：必须写入 `payloads[]` 对象，禁止只把 payload 写成 `' OR 1=1` 这种无法发的字符串、禁止写进 `kill_chain` 代替模板。

`reproduce/SKILL.md` 整段替换「关键约束」里「5 次变体…」为：

```markdown
- 禁止 docker / compose。靶场已由平台拉起。
- 无真实浏览器：XSS/DOM 若只有 curl，不得 `confirmed`（用 `code_reachable` 或 `partial`）。
- 不设验证次数上限。能诚实判定 6 档之一就 submit_result（判定即停）。不要等平台重开一轮。
```

「工作流」Phase 3 改为：

```markdown
1. 截图放到 `{source_path}/VULN-<NNN>-<title>/img/`，且必须是真实图片文件。
2. 第一枪强制执行 `audit.payloads[0]`：`url = target_url.rstrip('/') + path`，带上 `initial_creds`。禁止第一枪另选端点。其余 payloads 是后续候选。
3. 必须仍测 `audit.core_claim`，禁止换题、禁止黑盒扫。
4. 若 `audit.runtime_dependent === true`，先按 `unresolved_facts` / `gate_reason` 补运行时事实再发攻击请求。
5. 未打出危害：允许重读源码、修正同一主张的利用链/编码/鉴权，然后继续 HTTP。判定即停。
6. 诊断证据不能作为已确认；确认证据必须是 HTTP 可观察危害。
7. SSRF 必须 preflight 验证回调通道可达。
8. `confirmed` / `partial` 必须交完整 `poc`（Python 脚本优先，bash 次之，其它须 `language_reason`）。`code` 必须是已打通请求的完整可运行源码，不是 demo。其余 verdict 不得交 poc。
```

判定表 `confirmed`/`partial` 行追加「必须交 `poc`」。

`report/SKILL.md` 在漏洞报告 `poc_commands` 那条改为：

```markdown
- `poc_commands`：平台会用 reproduce 的 `poc` 正本覆盖本节。禁止改写 `poc.code`。你仍须交非空占位字符串以满足 8 节形状。
```

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_run_one_node_prompt.py -v`

Expected: PASS（含既有 thin-prompt 测试）

- [ ] **Step 5: Commit**（用户未要求则跳过）

```bash
git add infrastructure/agent-runner/node-skills backend/tests/test_run_one_node_prompt.py
git commit -m "docs(agent): 蒸馏 skill 改为第一枪打审计模板" -m "去掉复现次数上限；打通后交完整 poc；报告不得改写正本。"
```

---

### Task 4: 报告节点覆盖 poc_commands

**Files:**
- Modify: `backend/app/contexts/agent/ai_runner.py`（新增 `render_poc_markdown`、`apply_poc_to_report_output`）
- Modify: `backend/app/contexts/agent/nodes/report.py`
- Modify: `backend/tests/test_report_node.py`
- Test: `backend/tests/test_ai_runner.py`（纯函数）+ `backend/tests/test_report_node.py`

**Interfaces:**
- Consumes: Task 2 的 `_poc_ok` / `_validate_poc`；`ReportNode.execute` 现有 `run_ai_node` 之后逻辑
- Produces:
  - `render_poc_markdown(poc: dict) -> str`：围栏语言 `python`/`bash`/`text`，正文为 `code`，末行 `用法：\`{usage}\``
  - `apply_poc_to_report_output(output: dict, poc: dict | None, expected_verdict: str) -> dict`：confirmed/partial 覆盖 `report_data.poc_commands` 并 `output["poc"]=poc`；否则 `pop poc` 且从 `report_data` 去掉 `poc_commands`
  - `ReportNode.execute` 在 `run_ai_node` 成功且 verdict 未漂移之后调用 `apply_poc_to_report_output`；confirmed 缺 poc 则 `RuntimeError("confirmed/partial 缺少 reproduce.poc")`

- [ ] **Step 1: 写失败测试**

`backend/tests/test_ai_runner.py`：

```python
from app.contexts.agent.ai_runner import apply_poc_to_report_output, render_poc_markdown


def test_render_poc_markdown_python_fence():
    md = render_poc_markdown(_poc_ok())
    assert md.startswith("```python\n")
    assert "argparse" in md
    assert "用法：`python poc.py --url http://host.docker.internal:8080`" in md


def test_apply_poc_overwrites_model_poc_commands():
    out = apply_poc_to_report_output(
        {"report_data": _md_sections(poc_commands="```bash\ncurl x\n```"), "final_verdict": "confirmed"},
        _poc_ok(code="print('REAL')\n"),
        "confirmed",
    )
    assert "print('REAL')" in out["report_data"]["poc_commands"]
    assert "curl x" not in out["report_data"]["poc_commands"]
    assert out["poc"]["code"].startswith("print")


def test_apply_poc_strips_on_verification_record():
    out = apply_poc_to_report_output(
        {"report_data": _record_sections(), "final_verdict": "false_positive"},
        None,
        "false_positive",
    )
    assert "poc" not in out
    assert "poc_commands" not in out["report_data"]
```

`test_report_node.py` 追加（`_confirmed_ok` 在 Task 2 已含 poc）：

```python
@pytest.mark.asyncio
async def test_report_node_overwrites_poc_commands_from_reproduce():
    repro = _confirmed_ok(poc=_poc_ok(code="print('FROM_REPRO')\n"))
    fake = AsyncMock(return_value={
        "report_data": _md_sections(poc_commands="```bash\ncurl rewritten\n```"),
        "final_verdict": "confirmed",
        "cvss": repro["cvss"],
        "vulnerable_file": repro["vulnerable_file"],
    })
    with patch("app.contexts.agent.ai_runner.run_ai_node", fake):
        out = await ReportNode().execute(_ctx(reproduce=repro, audit={"gate_verdict": "pass"}))
    assert "FROM_REPRO" in out["report_data"]["poc_commands"]
    assert "curl rewritten" not in out["report_data"]["poc_commands"]
    assert out["poc"]["filename"] == "poc.py"
```

在 `test_report_node.py` 增加 `from tests.test_ai_runner import _poc_ok`。

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_ai_runner.py::test_render_poc_markdown_python_fence tests/test_ai_runner.py::test_apply_poc_overwrites_model_poc_commands tests/test_report_node.py::test_report_node_overwrites_poc_commands_from_reproduce -v`

Expected: FAIL（import 失败或未覆盖）

- [ ] **Step 3: 最小实现**

`ai_runner.py`：

```python
_POC_FENCE = {"python": "python", "bash": "bash", "other": "text"}


def render_poc_markdown(poc: dict[str, Any]) -> str:
    fence = _POC_FENCE.get(str(poc.get("language") or ""), "text")
    code = str(poc.get("code") or "").rstrip()
    usage = str(poc.get("usage") or "").strip()
    return f"```{fence}\n{code}\n```\n\n用法：`{usage}`\n"


def apply_poc_to_report_output(
    output: dict[str, Any], poc: dict[str, Any] | None, expected_verdict: str,
) -> dict[str, Any]:
    if expected_verdict in _CONFIRMED_VERDICTS:
        ok, err = _validate_poc(poc, required=True)
        if not ok:
            raise RuntimeError(err or "confirmed/partial 缺少 reproduce.poc")
        assert isinstance(poc, dict)
        rd = dict(output.get("report_data") or {})
        rd["poc_commands"] = render_poc_markdown(poc)
        output["report_data"] = rd
        output["poc"] = poc
        return output
    output.pop("poc", None)
    rd = output.get("report_data")
    if isinstance(rd, dict):
        rd.pop("poc_commands", None)
    return output
```

`report.py` 在 `if final != expected: raise` 之后、`authored_by` 之前：

```python
        from app.contexts.agent.ai_runner import apply_poc_to_report_output

        repro_poc = repro.get("poc") if isinstance(repro.get("poc"), dict) else None
        output = apply_poc_to_report_output(output, repro_poc, expected)
```

`repro` 已在函数前部取出。confirmed 缺 poc 时 `apply_poc_to_report_output` 抛错，节点失败。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_ai_runner.py::test_render_poc_markdown_python_fence tests/test_ai_runner.py::test_apply_poc_overwrites_model_poc_commands tests/test_ai_runner.py::test_apply_poc_strips_on_verification_record tests/test_report_node.py -v`

Expected: PASS（含既有 drift / skip-reproduce 用例）

- [ ] **Step 5: Commit**（用户未要求则跳过）

```bash
git add backend/app/contexts/agent/ai_runner.py backend/app/contexts/agent/nodes/report.py backend/tests/test_ai_runner.py backend/tests/test_report_node.py
git commit -m "feat(report): 用 reproduce.poc 覆盖报告 PoC 节" -m "防止报告模型把已打通脚本改写成 demo curl。"
```

---

### Task 5: reports 四列 + API 落库

**Files:**
- Create: `backend/alembic/versions/f1a8c3d04e25_report_poc_columns.py`
- Modify: `backend/app/contexts/report/models.py`
- Modify: `backend/app/contexts/report/schemas.py`
- Modify: `backend/app/contexts/report/service.py`（`_to_detail`）
- Modify: `backend/app/contexts/agent/tasks.py`（`report_columns_from_orch_result` + `Report(...)`）
- Modify: `backend/app/contexts/agent/orchestrator.py`（收尾 dict 加 `poc`）
- Modify: `backend/tests/test_report_node.py`（columns 断言）
- Test: `backend/tests/test_report_node.py`

**Interfaces:**
- Consumes: Task 4 的 report output 含 `poc`；`run_orchestration` 现有返回 dict
- Produces:
  - `Report.poc_language: str | None`（String(16)）
  - `Report.poc_filename: str | None`（String(255)）
  - `Report.poc_code: str | None`（Text）
  - `Report.poc_usage: str | None`（String(1024)）
  - `report_columns_from_orch_result` 增加同名四键；未确认判定四键均为 `None`
  - `run_orchestration` 返回 `"poc": report_out.get("poc")`
  - `ReportDetail` 与 `_to_detail` 含四字段（可空）
  - 迁移 `down_revision = "e4b7c2d91a03"`

- [ ] **Step 1: 写失败测试**

扩展 `test_report_columns_from_orch_result_confirmed`：

```python
        "poc": {
            "language": "python",
            "filename": "poc.py",
            "code": "print('x')\n",
            "usage": "python poc.py --url http://x",
        },
    })
    assert cols["poc_language"] == "python"
    assert cols["poc_filename"] == "poc.py"
    assert cols["poc_code"] == "print('x')\n"
    assert cols["poc_usage"] == "python poc.py --url http://x"
```

扩展 `test_report_columns_drop_cvss_for_not_reproduced`：即使 orch 误带 poc，未确认也要清空：

```python
        "poc": {"language": "python", "filename": "poc.py", "code": "print(1)", "usage": "x"},
    })
    assert cols["poc_language"] is None
    assert cols["poc_code"] is None
```

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_report_node.py::test_report_columns_from_orch_result_confirmed tests/test_report_node.py::test_report_columns_drop_cvss_for_not_reproduced -v`

Expected: FAIL（KeyError `poc_language`）

- [ ] **Step 3: 最小实现**

`models.py` 在 `docx_artifact_key` 后增加四列（nullable，带 comment）。

`schemas.py` 的 `ReportDetail` 增加：

```python
    poc_language: str | None = None
    poc_filename: str | None = None
    poc_code: str | None = None
    poc_usage: str | None = None
```

`service.py` `_to_detail` 传入这四项（`report.poc_language` 等）。

`report_columns_from_orch_result`：

```python
    poc = orch_result.get("poc") if isinstance(orch_result.get("poc"), dict) else {}
    ...
        "poc_language": (str(poc["language"]) if confirmed and poc.get("language") else None),
        "poc_filename": (str(poc["filename"]) if confirmed and poc.get("filename") else None),
        "poc_code": (str(poc["code"]) if confirmed and poc.get("code") else None),
        "poc_usage": (str(poc["usage"]) if confirmed and poc.get("usage") else None),
```

`tasks.py` 建 `Report(...)` 时传入 `poc_language=cols["poc_language"]` 等四键。

`orchestrator.py` 完成返回 dict 增加 `"poc": report_out.get("poc") if report_out else None`。

新建迁移 `backend/alembic/versions/f1a8c3d04e25_report_poc_columns.py`：

```python
"""add report poc columns

Revision ID: f1a8c3d04e25
Revises: e4b7c2d91a03
Create Date: 2026-08-17
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "f1a8c3d04e25"
down_revision: Union[str, None] = "e4b7c2d91a03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("reports") as batch:
        batch.add_column(sa.Column("poc_language", sa.String(length=16), nullable=True, comment="python/bash/other"))
        batch.add_column(sa.Column("poc_filename", sa.String(length=255), nullable=True))
        batch.add_column(sa.Column("poc_code", sa.Text(), nullable=True, comment="完整 PoC 源码"))
        batch.add_column(sa.Column("poc_usage", sa.String(length=1024), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("reports") as batch:
        batch.drop_column("poc_usage")
        batch.drop_column("poc_code")
        batch.drop_column("poc_filename")
        batch.drop_column("poc_language")
```

本地开发库执行：`cd backend && alembic upgrade head`（SQLite 用 `batch_alter_table`）。内存测试走 `create_all`，不依赖迁移。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_report_node.py tests/test_report_list.py tests/test_orchestrator.py -v`

Expected: PASS

- [ ] **Step 5: Commit**（用户未要求则跳过）

```bash
git add backend/alembic/versions/f1a8c3d04e25_report_poc_columns.py backend/app/contexts/report/models.py backend/app/contexts/report/schemas.py backend/app/contexts/report/service.py backend/app/contexts/agent/tasks.py backend/app/contexts/agent/orchestrator.py backend/tests/test_report_node.py
git commit -m "feat(report): PoC 正本落入 reports 四列" -m "详情 API 可直接取完整脚本；未确认判定保持空列。"
```

---

### Task 6: 前端 Markdown 展示 PoC

**Files:**
- Modify: `frontend/src/shared/lib/api.ts`（`ReportDetail`）
- Modify: `frontend/src/shared/lib/reportData.ts`（`pocToMarkdown`）
- Modify: `frontend/src/shared/lib/reportData.test.ts`
- Modify: `frontend/src/shared/components/ReportContent.tsx`
- Modify: `frontend/src/shared/components/ReportContent.test.tsx`
- Test: 上述两个测试文件

**Interfaces:**
- Consumes: Task 5 的详情四字段（可空）
- Produces:
  - `pocToMarkdown(language, code, usage): string | null` — `code` 空白则 `null`；fence 与后端 `render_poc_markdown` 一致
  - `ReportContent`：`poc_code` 非空时 §6 用 `pocToMarkdown` 的结果喂 `MarkdownBody`，Collapse label 为 `§6 POC · {poc_filename}`（filename 空则仍 `§6 POC`）；否则回退 `report_data.poc_commands`
  - 验证记录路径不变（无 §6 POC）
  - `package.json` 不新增依赖

- [ ] **Step 1: 写失败测试**

`reportData.test.ts` 追加：

```typescript
import { pocToMarkdown } from './reportData'

it('builds python fence from poc columns', () => {
  const md = pocToMarkdown('python', "print('x')\n", 'python poc.py --url http://x')
  expect(md).toContain('```python')
  expect(md).toContain("print('x')")
  expect(md).toContain('用法：`python poc.py --url http://x`')
})

it('returns null when poc code is empty', () => {
  expect(pocToMarkdown('python', '  ', 'x')).toBeNull()
})
```

`ReportContent.test.tsx` 的 `base` 增加 `poc_language: null, poc_filename: null, poc_code: null, poc_usage: null`。

追加：

```typescript
  it('renders poc_code fence instead of leftover curl markdown', () => {
    const html = render(
      <ReportContent
        report={{
          ...base,
          verdict: 'confirmed',
          poc_language: 'python',
          poc_filename: 'poc.py',
          poc_code: "print('FROM_COLUMN')\n",
          poc_usage: 'python poc.py --url http://x',
          report_data: {
            product_intro: 'p',
            vulnerability: 'v',
            impact: 'i',
            details: 'd',
            reproduction: 'r',
            poc_commands: '```bash\ncurl leftover\n```',
            fix_suggestions: 'f',
            reporting_decision: 's',
          },
        }}
      />,
    )
    expect(html).toContain('FROM_COLUMN')
    expect(html).toContain('poc.py')
    expect(html).not.toContain('curl leftover')
  })
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx vitest run src/shared/lib/reportData.test.ts src/shared/components/ReportContent.test.tsx`

Expected: FAIL

- [ ] **Step 3: 最小实现**

`api.ts` `ReportDetail` 增加四字段 `string | null`。

`reportData.ts`：

```typescript
export function pocToMarkdown(
  language: string | null | undefined,
  code: string | null | undefined,
  usage: string | null | undefined,
): string | null {
  const src = (code ?? '').trim()
  if (!src) return null
  const fence = language === 'python' ? 'python' : language === 'bash' ? 'bash' : 'text'
  const how = (usage ?? '').trim()
  return how
    ? `\`\`\`${fence}\n${src}\n\`\`\`\n\n用法：\`${how}\`\n`
    : `\`\`\`${fence}\n${src}\n\`\`\`\n`
}
```

`ReportContent.tsx` 在 map sections 时：若 `section.key === 'poc_commands'` 且 `pocToMarkdown(report.poc_language, report.poc_code, report.poc_usage)` 非空，则用该字符串代替 `rd.poc_commands`；label 在 `report.poc_filename` 非空时为 `${section.label} · ${report.poc_filename}`。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx vitest run src/shared/lib/reportData.test.ts src/shared/components/ReportContent.test.tsx && npx tsc --noEmit`

Expected: PASS

- [ ] **Step 5: Commit**（用户未要求则跳过）

```bash
git add frontend/src/shared/lib/api.ts frontend/src/shared/lib/reportData.ts frontend/src/shared/lib/reportData.test.ts frontend/src/shared/components/ReportContent.tsx frontend/src/shared/components/ReportContent.test.tsx
git commit -m "feat(frontend): 报告 §6 用入库 PoC 渲染 Markdown" -m "有 poc_code 时覆盖旧 curl 节；不加新编辑器依赖。"
```

---

### Task 7: 权威文档同步

**Files:**
- Modify: `docs/superpowers/specs/2026-08-12-platform-node-orchestration-design.md` §1.3 audit/reproduce/report 行
- Modify: `docs/agent-workflow.md`（回退环）
- Modify: `docs/superpowers/specs/2026-08-14-reproduce-live-gate-design.md` 文首
- Modify: `.claude/api-contract.md` Report 详情
- Modify: `docs/development-guide.md` P0-0 那行补「PoC 正本入库」
- Modify: `docs/superpowers/specs/2026-08-17-audit-reproduce-poc-handoff-design.md` 状态改为已评审

**Interfaces:**
- Consumes: 本 spec 已确认决策
- Produces: 文档与实现一致；桌面 `plugins/` 不改

- [ ] **Step 1: 改编排 §1.3**

节点 3 output 行补：`core_claim`；`payloads[{method,path,expected_observable,...}]`；`runtime_dependent=true` 时 `unresolved_facts[]`。

节点 4 行：删「一次 AI，5 次变体在容器内」；改为「第一枪执行 payloads[0]；容器内深挖同一 core_claim，判定即停；confirmed/partial 交 poc」。

节点 5 行：补「平台用 reproduce.poc 覆盖 poc_commands」。

- [ ] **Step 2: 改 agent-workflow**

把「回退环：reproduce 容器内最多 5 次变体」改为「回退环：reproduce 容器内自行深挖同一 core_claim，判定即停（不是平台节点循环）」。

- [ ] **Step 3: live-gate 文首加覆盖声明**

在 `2026-08-14-reproduce-live-gate-design.md` 状态段加一句：变体上限 5 次与 `poc_commands` 由 report 撰写，已被 `2026-08-17-audit-reproduce-poc-handoff-design.md` 覆盖。

- [ ] **Step 4: api-contract + development-guide + spec 状态**

`GET /reports/{id}` 结构化字段列表加入 `poc_language/poc_filename/poc_code/poc_usage`。漏洞报告条改为：PoC 正本在四列；`poc_commands` 是平台生成的围栏。

`development-guide.md` 平台 6 节点编排那句补「confirmed/partial 的 PoC 正本入 reports 四列」。

本设计 spec 文首状态：`已评审`。

- [ ] **Step 5: Commit**（用户未要求则跳过）

```bash
git add docs .claude/api-contract.md
git commit -m "docs: 同步审计射击清单与 PoC 入库契约" -m "编排、API 与 agent-workflow 与已实现行为对齐。"
```

---

## Self-Review

| Spec 节 | Task |
|---|---|
| §2 audit `core_claim` / 对象 payloads / `unresolved_facts` / mock | Task 1 |
| §3 第一枪 / 无次数上限 / skill | Task 3 |
| §3.3 poc 形状 / mock | Task 2 |
| §4.1 平台覆盖 poc_commands | Task 4 |
| §4.2–4.3 四列 + API + orchestrator 返回 | Task 5 |
| §5 前端 Markdown、无 CodeMirror | Task 6 |
| §6 文档 | Task 7 |
| §7 测试表 | Task 1/2/4/5/6 的红绿用例覆盖；orchestrator gate_fail 回归在 Task 5 Step 4 |

无 TBD。`_confirmed_ok` 在 Task 2 起默认含 `poc`，Task 4/5 依赖该 helper。fence 语言映射前后端均为 python/bash/text。
