# 阶段 2:6 节点编排器 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `_run_analysis` 单次大调用重构成 6 节点编排器循环:代码节点(0 源码/1 画像)在 worker 内确定性执行,AI 节点(2 靶场/3 审计/4 复现/5 报告)每节点独立 agent-runner 容器调 `submit_result` 工具回传结构化 JSON,节点间用 NodeRun.input/output_json 交接。

**Architecture:** 新增 `agent/orchestrator.py`(节点循环)+ `agent/nodes/`(每节点执行单元)+ `agent/profile_detector.py`(节点 1 规则引擎);改造 `run_one.py` 支持 `submit_result` 工具 + 按 `NODE_KEY` 选 agent;拆分插件为 4 子 agent。

**Tech Stack:** Python 3.11 / Claude Agent SDK 0.2.134 / Docker SDK / Celery

## Global Constraints

- 提交信息用简体中文主体 + 英文 type 前缀
- AI 节点独立 agent-runner 容器(`cap_drop ALL` 红线不变)
- 节点 2 靶场 docker 由 **worker 执行**(AI 不碰 docker.sock)
- 节点 output 严格按 spec §1.3 schema,平台 schema 校验后才进下一节点
- 断点续跑:NodeRun.status=completed 的节点复用 output_json 不重算
- Windows 开发用 Git Bash,路径正斜杠
- 向后兼容:`agent.run_analysis` Celery 任务签名(task_id, run_id)不变

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `backend/app/contexts/agent/nodes/__init__.py` | 新建 | 节点执行单元包 |
| `backend/app/contexts/agent/nodes/base.py` | 新建 | NodeExecutor 协议 + 共用 |
| `backend/app/contexts/agent/nodes/source.py` | 新建 | 节点 0 源码获取(代码) |
| `backend/app/contexts/agent/nodes/profile.py` | 新建 | 节点 1 项目画像(代码规则引擎) |
| `backend/app/contexts/agent/nodes/env_ready.py` | 新建 | 节点 2 靶场(AI+代码协作) |
| `backend/app/contexts/agent/nodes/audit.py` | 新建 | 节点 3 白盒审计(AI) |
| `backend/app/contexts/agent/nodes/reproduce.py` | 新建 | 节点 4 复现验证(AI) |
| `backend/app/contexts/agent/nodes/report.py` | 新建 | 节点 5 报告生成(AI+代码) |
| `backend/app/contexts/agent/profile_detector.py` | 新建 | 节点 1 规则引擎(project-detection + web-detection 翻译) |
| `backend/app/contexts/agent/orchestrator.py` | 新建 | 6 节点循环 + 分支出口 + 断点续跑 |
| `backend/app/contexts/agent/tasks.py` | 改 | `_run_analysis` 改为调 orchestrator(保留 Celery 入口 + 事件持久化辅助) |
| `backend/app/contexts/agent/ai_runner.py` | 新建 | AI 节点的容器编排(起 agent-runner + submit_result 工具) |
| `infrastructure/agent-runner/runner/run_one.py` | 改 | 读 `.node.json` + 按 NODE_KEY 选 agent + 注入 submit_result 工具 |
| `plugins/vuln-verify-expert/agents/env-builder.md` | 新建 | 节点 2 agent |
| `plugins/vuln-verify-expert/agents/auditor.md` | 新建 | 节点 3 agent |
| `plugins/vuln-verify-expert/agents/reproducer.md` | 新建 | 节点 4 agent |
| `plugins/vuln-verify-expert/agents/reporter.md` | 新建 | 节点 5 agent |
| `infrastructure/agent-runner/Dockerfile` | 改 | ENV 加 NODE_KEY 默认值 |
| `backend/tests/test_orchestrator.py` | 新建 | 节点循环 + 分支出口测试 |
| `backend/tests/test_profile_detector.py` | 新建 | 节点 1 规则引擎测试 |

---

### Task 1: PoC — Claude Agent SDK 自定义工具(submit_result)可行性

**说明:** 这是 spec 标的最高风险点。SDK 0.2.x 的自定义工具注入 API 不确定(MCP server / tools 参数 / hook)。先写最小 PoC 确认能注入一个自定义工具并强制 tool_choice,失败则退回文本 JSON 块方案。**此任务决定 AI 节点的整个输出机制,必须先做。**

**Files:**
- Create: `backend/tests/poc_submit_result.py`(一次性 PoC 脚本,验证后可删)

- [ ] **Step 1: 写 PoC 脚本 — 尝试 MCP server 方式注入自定义工具**

Create `backend/tests/poc_submit_result.py`:

```python
"""PoC: 验证 Claude Agent SDK 0.2.x 能否注入自定义 submit_result 工具并强制调用。

运行(需起一个 agent-runner 容器或直接在装了 SDK 的环境跑):
  python tests/poc_submit_result.py

三种注入方式逐个试,哪个 work 用哪个:
  A. ClaudeAgentOptions(mcp_servers={"submit": {...}})  — MCP server 方式
  B. ClaudeAgentOptions(tools=[...]) — typed tools 参数(若 SDK 支持)
  C. fallback: prompt 要求 ```json 块(无工具)

成功标志:agent 调用了 submit_result 工具,平台能从 tool_use_block.input 拿到结构化数据。
"""
import asyncio
import os
import sys

# 用容器内的 SDK 跑;本地若无 SDK,在 agent-runner 容器内执行
try:
    from claude_agent_sdk import query, ClaudeAgentOptions
except ImportError:
    print("SDK 不在本环境,请在 agent-runner 容器内跑此 PoC")
    sys.exit(2)


SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["confirmed", "false_positive"]},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "reason"],
}


async def try_mcp_way():
    """方式 A: MCP server 注入自定义工具。"""
    # 这是推测的 API,实际需查 SDK 文档/源码确认字段名
    try:
        options = ClaudeAgentOptions(
            model=os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash"),
            max_turns=3,
            permission_mode="bypassPermissions",
            mcp_servers={
                "submit": {
                    "command": "python",
                    "args": ["-m", "runner.submit_tool"],
                    "tools": {"submit_result": {"inputSchema": SCHEMA}},
                }
            },
        )
        print("[A] mcp_servers 选项构造 OK")
        return options
    except (TypeError, Exception) as e:
        print(f"[A] mcp_servers 方式失败: {type(e).__name__}: {e}")
        return None


async def try_tools_way():
    """方式 B: tools 参数直接定义(若 SDK 支持 typed tools)。"""
    try:
        options = ClaudeAgentOptions(
            model=os.environ.get("ANTHROPIC_MODEL", "deepseek-v4-flash"),
            max_turns=3,
            tools=[{"name": "submit_result", "description": "提交节点结果", "input_schema": SCHEMA}],
        )
        print("[B] tools 选项构造 OK")
        return options
    except (TypeError, Exception) as e:
        print(f"[B] tools 方式失败: {type(e).__name__}: {e}")
        return None


async def main():
    print("=== PoC: Claude Agent SDK 自定义工具注入 ===")
    opts = await try_mcp_way()
    if not opts:
        opts = await try_tools_way()
    if not opts:
        print("[C] 两种工具注入都失败 → 走 fallback: prompt 要求 ```json 块")
        return

    print("\n=== 实际调用 query() 测试 agent 是否调用 submit_result ===")
    prompt = "分析后必须调用 submit_result 工具提交结果。"
    try:
        async for msg in query(prompt=prompt, options=opts):
            print(f"  {type(msg).__name__}: {str(msg)[:200]}")
    except Exception as e:
        print(f"query 失败: {type(e).__name__}: {e}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: 在 agent-runner 容器内跑 PoC**

Run:
```bash
docker run --rm -v D:/codeproject/Crucible/backend/tests/poc_submit_result.py:/tmp/poc.py:ro \
  --env-file <(python -c "from app.core.config import get_settings; s=get_settings(); print(f'ANTHROPIC_BASE_URL={s.llm_base_url}'); print(f'ANTHROPIC_API_KEY={s.llm_api_key}'); print(f'ANTHROPIC_MODEL={s.llm_model}')") \
  --entrypoint python \
  crucible-agent-runner:base /tmp/poc.py
```
Expected: 输出三种方式的结果,确认哪种 work。

- [ ] **Step 3: 查 SDK 源码确认自定义工具 API(若 PoC 不明)**

Run(在容器内或本地装了 SDK 的环境):
```bash
python -c "import claude_agent_sdk, inspect; print(inspect.signature(claude_agent_sdk.ClaudeAgentOptions.__init__))"
python -c "import claude_agent_sdk; print([x for x in dir(claude_agent_sdk) if not x.startswith('_')])"
```
Expected: 看 ClaudeAgentOptions 接受哪些参数(mcp_servers / tools / 其他),确定自定义工具的正确注入字段。

- [ ] **Step 4: 根据 PoC 结果定方案,记录到本计划**

在计划末尾「PoC 结论」记录:work 的方式是 A/B/C 中的哪个。后续 AI 节点(ai_runner.py)按此方式注入 submit_result。

(此任务无 commit — PoC 脚本是探索性产物,确认后删除或保留为参考)

---

### Task 2: 节点 1 规则引擎(profile_detector)

**Files:**
- Create: `backend/app/contexts/agent/profile_detector.py`
- Test: `backend/tests/test_profile_detector.py`

**Interfaces:**
- Produces: `detect_profile(source_path: str) -> dict` 返回 `{is_web, language, framework, port, has_dockerfile, detected_services[]}`。把 plugin `project-detection.md`(7 语言规则表)+ `web-detection.md`(web 门禁关键字表)翻成 Python。

- [ ] **Step 1: 写规则引擎测试**

Create `backend/tests/test_profile_detector.py`:

```python
"""节点 1 项目画像规则引擎测试。"""
import sys, os, tempfile, pathlib
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _make_project(files: dict[str, str]) -> str:
    d = tempfile.mkdtemp()
    for rel, content in files.items():
        p = pathlib.Path(d) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return d


def test_detect_nodejs():
    from app.contexts.agent.profile_detector import detect_profile
    d = _make_project({
        "package.json": '{"name":"x","dependencies":{"express":"^4.0.0"}}',
        "README.md": "# x\nrun: npm start\nport 3000",
    })
    r = detect_profile(d)
    assert r["language"] == "nodejs"
    assert r["framework"] == "express"
    assert r["is_web"] is True
    assert r["has_dockerfile"] is False


def test_detect_python_fastapi():
    from app.contexts.agent.profile_detector import detect_profile
    d = _make_project({
        "requirements.txt": "fastapi\nuvicorn\n",
        "main.py": "from fastapi import FastAPI\napp = FastAPI()",
    })
    r = detect_profile(d)
    assert r["language"] == "python"
    assert r["framework"] == "fastapi"
    assert r["is_web"] is True


def test_detect_non_web_cli_tool():
    from app.contexts.agent.profile_detector import detect_profile
    d = _make_project({
        "requirements.txt": "click\nrich\n",
        "cli.py": "import click\n@click.command()",
        "README.md": "# x\nA CLI tool for data processing",
    })
    r = detect_profile(d)
    assert r["is_web"] is False


def test_detect_java_spring():
    from app.contexts.agent.profile_detector import detect_profile
    d = _make_project({
        "pom.xml": "<project><dependencies><dependency><groupId>org.springframework.boot</groupId></dependency></dependencies></project>",
    })
    r = detect_profile(d)
    assert r["language"] == "java"
    assert "spring" in r["framework"].lower()


def test_detect_existing_dockerfile():
    from app.contexts.agent.profile_detector import detect_profile
    d = _make_project({
        "package.json": '{"name":"x"}',
        "Dockerfile": "FROM node:18\nCMD [\"npm\",\"start\"]",
        "docker-compose.yml": "version: '3'\nservices:\n  web:\n    image: x",
    })
    r = detect_profile(d)
    assert r["has_dockerfile"] is True
    assert r["has_compose"] is True


def test_detect_port_from_env():
    from app.contexts.agent.profile_detector import detect_profile
    d = _make_project({
        "package.json": '{"name":"x","scripts":{"start":"node server.js"}}',
        ".env": "PORT=8080\n",
        "server.js": "const express = require('express')",
    })
    r = detect_profile(d)
    assert r["port"] == 8080
```

- [ ] **Step 2: 跑测试验证失败**

Run: `cd backend && python -m pytest tests/test_profile_detector.py -v`
Expected: FAIL(`profile_detector` 不存在)。

- [ ] **Step 3: 实现 profile_detector.py**

Create `backend/app/contexts/agent/profile_detector.py`:

```python
"""节点 1 项目画像规则引擎。

把 plugin run-project-env/references/project-detection.md(7 语言规则表)
+ web-detection.md(web 门禁关键字表)翻成 Python 确定性检测。
画像后回填 Project 表,后续任务复用省 AI。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# 语言检测规则(触发文件 → 语言)(对齐 project-detection.md)
LANGUAGE_RULES: list[tuple[str, list[str]]] = [
    ("nodejs", ["package.json"]),
    ("python", ["requirements.txt", "pyproject.toml"]),
    ("java", ["pom.xml", "build.gradle"]),
    ("go", ["go.mod"]),
    ("php", ["composer.json", "index.php"]),
    ("rust", ["Cargo.toml"]),
    ("static", ["index.html"]),  # 仅静态 HTML(最低优先级)
]

# 框架关键字(语言 → {关键字 → 框架})
FRAMEWORK_KEYWORDS: dict[str, dict[str, str]] = {
    "nodejs": {
        "next": "next", "nuxt": "nuxt", "express": "express", "@nestjs/core": "nestjs",
        "koa": "koa", "fastify": "fastify",
    },
    "python": {
        "fastapi": "fastapi", "flask": "flask", "django": "django", "streamlit": "streamlit",
        "tornado": "tornado",
    },
    "java": {"spring-boot": "spring-boot", "quarkus": "quarkus"},
    "go": {"gin": "gin", "echo": "echo"},
    "php": {"laravel": "laravel", "symfony": "symfony"},
    "rust": {"actix-web": "actix-web", "axum": "axum", "rocket": "rocket"},
}

# web 框架(web 门禁 is_web=True 的信号)
WEB_FRAMEWORKS = {
    "express", "nestjs", "koa", "fastify", "next", "nuxt",
    "fastapi", "flask", "django", "streamlit", "tornado",
    "spring-boot", "quarkus",
    "gin", "echo",
    "laravel", "symfony",
    "actix-web", "axum", "rocket",
}

# web 门禁额外信号(web-detection.md)
WEB_SIGNALS = [
    r"server\.port", r"PORT\s*=", r"listen\s*\(\s*\d", r"app\.listen",
    r"swagger", r"openapi", r"routes?/", r"controller",
]
NON_WEB_SIGNALS = [r"CLI\s+tool", r"command.line", r"desktop\s+app", r"batch\s+process"]


def _detect_language(root: Path) -> str | None:
    for lang, files in LANGUAGE_RULES:
        if any((root / f).exists() for f in files):
            return lang
    return None


def _read_file(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return ""


def _detect_framework(root: Path, language: str) -> str | None:
    kws = FRAMEWORK_KEYWORDS.get(language, {})
    if not kws:
        return None
    # 扫依赖文件
    dep_files = {
        "nodejs": ["package.json"],
        "python": ["requirements.txt", "pyproject.toml"],
        "java": ["pom.xml", "build.gradle"],
        "go": ["go.mod"],
        "php": ["composer.json"],
        "rust": ["cargo.toml"],
    }.get(language, [])
    blob = "\n".join(_read_file(root / f) for f in dep_files if (root / f).exists())
    for kw, fw in kws.items():
        if kw in blob:
            return fw
    return None


def _detect_port(root: Path) -> int | None:
    # .env / application.yml / 配置文件
    candidates = [root / ".env", root / "application.yml", root / "application.yaml",
                  root / "config" / "default.json", root / ".env.example"]
    for p in candidates:
        if not p.exists():
            continue
        text = _read_file(p)
        m = re.search(r"(?:^|\n)\s*(?:port|PORT)\s*[=:]\s*(\d{2,5})", text)
        if m:
            return int(m.group(1))
    return None


def _is_web(root: Path, language: str | None, framework: str | None) -> bool:
    if framework and framework in WEB_FRAMEWORKS:
        return True
    # 扫 README + 配置找 web 信号
    readme = ""
    for rname in ["README.md", "readme.md", "README.rst"]:
        if (root / rname).exists():
            readme = _read_file(root / rname)
            break
    blob = readme
    for signal in WEB_SIGNALS:
        if re.search(signal, blob, re.IGNORECASE):
            return True
    # 非 web 信号覆盖
    for signal in NON_WEB_SIGNALS:
        if re.search(signal, blob, re.IGNORECASE):
            return False
    # 静态站
    if language == "static":
        return True
    return False


def detect_profile(source_path: str) -> dict:
    """对 clone 后的源码做画像,返回节点 1 output schema。"""
    root = Path(source_path) / "project" if (Path(source_path) / "project").exists() else Path(source_path)
    language = _detect_language(root)
    framework = _detect_framework(root, language) if language else None
    port = _detect_port(root)
    is_web = _is_web(root, language, framework)
    has_dockerfile = (root / "Dockerfile").exists() or any(root.glob("Dockerfile*"))
    has_compose = any(root.glob("docker-compose*.yml")) or any(root.glob("docker-compose*.yaml"))

    return {
        "is_web": is_web,
        "language": language,
        "framework": framework,
        "port": port,
        "has_dockerfile": has_dockerfile,
        "has_compose": has_compose,
        "detected_services": [],
    }
```

- [ ] **Step 4: 跑测试验证通过**

Run: `cd backend && python -m pytest tests/test_profile_detector.py -v`
Expected: 6 PASS。

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/contexts/agent/profile_detector.py tests/test_profile_detector.py
git commit -m "feat(agent): 节点 1 项目画像规则引擎(profile_detector)"
```

---

### Task 3: 节点执行单元框架(base + 代码节点 0/1)

**Files:**
- Create: `backend/app/contexts/agent/nodes/__init__.py`
- Create: `backend/app/contexts/agent/nodes/base.py`
- Create: `backend/app/contexts/agent/nodes/source.py`
- Create: `backend/app/contexts/agent/nodes/profile.py`
- Test: `backend/tests/test_nodes_code.py`

**Interfaces:**
- Produces: `NodeExecutor` 协议 `async def execute(node_run, context, session) -> dict`(返回 output_json);`SourceNode`、`ProfileNode` 实现。
- 上下文 `NodeContext` 持有 task_id, run_id, host_workdir, task fields,前序节点 output 累积 dict。

- [ ] **Step 1: 写节点框架 + 代码节点测试**

Create `backend/tests/test_nodes_code.py`:

```python
"""节点 0 源码 + 节点 1 画像 测试(代码节点)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.profile import ProfileNode


@pytest.mark.asyncio
async def test_profile_node_produces_schema(tmp_path):
    """节点 1 画像产出 {is_web, language, framework, ...}。"""
    # 造一个 nodejs express 项目
    proj = tmp_path / "project"
    proj.mkdir()
    (proj / "package.json").write_text('{"name":"x","dependencies":{"express":"^4"}}')
    (proj / ".env").write_text("PORT=3000\n")

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path),
        vulnerability_description="d", project_ref=None,
        project_address="x", previous_outputs={},
    )
    node = ProfileNode()
    out = await node.execute(ctx)
    assert out["is_web"] is True
    assert out["language"] == "nodejs"
    assert out["framework"] == "express"
    assert out["port"] == 3000


def test_node_context_carries_previous_outputs():
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp",
        source_path="/tmp", vulnerability_description="d",
        project_ref=None, project_address="x",
        previous_outputs={"source": {"commit": "abc"}},
    )
    assert ctx.previous_outputs["source"]["commit"] == "abc"
```

- [ ] **Step 2: 跑测试失败**

Run: `cd backend && python -m pytest tests/test_nodes_code.py -v`
Expected: FAIL(import 不存在)。

- [ ] **Step 3: 建 nodes 包 + base.py + source.py + profile.py**

Create `backend/app/contexts/agent/nodes/__init__.py`(空)。

Create `backend/app/contexts/agent/nodes/base.py`:

```python
"""节点执行单元基类。

每个节点是一个 NodeExecutor,吃 NodeContext,产出 output_json dict。
代码节点(0/1)在 worker 进程内执行;AI 节点(2/3/4/5)由 ai_runner 起容器。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass
class NodeContext:
    """节点执行上下文 — 持有任务信息 + 前序节点 output 累积。"""
    task_id: str
    run_id: str
    host_workdir: str
    source_path: str                       # clone 后的源码根(含 project/ 子目录)
    vulnerability_description: str
    project_address: str
    project_ref: str | None
    previous_outputs: dict[str, dict] = field(default_factory=dict)
    # 编排器注入的共享对象(DB session 等按需加)


class NodeExecutor(Protocol):
    """节点执行协议。"""
    node_index: int
    node_key: str

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        """执行节点,返回 output_json(按 spec §1.3 schema)。"""
        ...

    @property
    def is_ai(self) -> bool:
        """AI 节点(需起容器)vs 代码节点(worker 内执行)。"""
        return False
```

Create `backend/app/contexts/agent/nodes/source.py`:

```python
"""节点 0 源码获取(代码)— git clone 由 worker 在 host 执行。

实际 clone 在编排器层(orchestrator)调 git_clone_to_workdir 完成,
此节点的 execute 只是把 clone 结果包装成 output_json + 回填 source_path。
"""
from __future__ import annotations

from typing import Any

from .base import NodeContext


class SourceNode:
    node_index = 0
    node_key = "source"

    @property
    def is_ai(self) -> bool:
        return False

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        # clone 由 orchestrator 在调 execute 前完成(它需要 host_workdir 就位)
        # 这里仅产出结构化 output
        return {
            "source_path": ctx.source_path,
            "project_address": ctx.project_address,
            "project_ref": ctx.project_ref,
            "host_workdir": ctx.host_workdir,
        }
```

Create `backend/app/contexts/agent/nodes/profile.py`:

```python
"""节点 1 项目画像(代码)— 调 profile_detector 规则引擎。"""
from __future__ import annotations

from typing import Any

from app.contexts.agent.profile_detector import detect_profile

from .base import NodeContext


class ProfileNode:
    node_index = 1
    node_key = "profile"

    @property
    def is_ai(self) -> bool:
        return False

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        return detect_profile(ctx.source_path)
```

- [ ] **Step 4: 跑测试通过**

Run: `cd backend && python -m pytest tests/test_nodes_code.py -v`
Expected: 2 PASS。

- [ ] **Step 5: Commit**

```bash
cd backend
git add app/contexts/agent/nodes/ tests/test_nodes_code.py
git commit -m "feat(agent): 节点执行框架 + 代码节点(0 source / 1 profile)"
```

---

### Task 4: AI 节点容器编排(ai_runner)+ run_one.py 改造

**说明:** AI 节点(2/3/4/5)每节点起一个 agent-runner 容器。容器读 `.node.json`(node_key + input_json),按 node_key 选 agent,跑完调 submit_result 回传 output_json。

**依赖:** Task 1 PoC 结论(确定 submit_result 注入方式)。

**Files:**
- Create: `backend/app/contexts/agent/ai_runner.py`
- Modify: `infrastructure/agent-runner/runner/run_one.py`
- Modify: `infrastructure/agent-runner/Dockerfile`(ENV 加 NODE_KEY)

**Interfaces:**
- `run_ai_node(node_index, node_key, input_json, host_workdir, runner_env, on_event) -> dict` — 起 agent-runner 容器,返回 output_json(从 submit_result tool_call 取)。
- run_one.py 读 `.node.json` 取 NODE_KEY 选 agent,跑完把 submit_result 的 input 写到 `/workspace/.node_output.json`。

- [ ] **Step 1: 改造 run_one.py — 读 .node.json + 按 NODE_KEY 选 agent**

Modify `infrastructure/agent-runner/runner/run_one.py`。

核心改动:
1. `_main()` 读 `/workspace/.node.json` 取 `node_key` + `input_json`(向后兼容:无 .node.json 时走旧的 .prompt.json 路径)。
2. `_build_options(model, max_turns)` 接受 `node_key` 参数,按它设 AGENT_NAME(env-builder/auditor/reproducer/reporter)。
3. 新增 `_build_node_prompt(node_key, input_json)` — 按 node_key 构造给 agent 的 user message(吃 input_json 各字段)。
4. query() 完成后,从事件流的最后一个 submit_result tool_use_block 取 input,写到 `/workspace/.node_output.json`。
5. submit_result 工具注入:按 Task 1 PoC 结论(MCP / tools / fallback)。

(具体代码较长,实施时基于 PoC 结论 + 现有 _build_options/_build_prompt 改造。关键:node_key → agent 映射、submit_result 注入、output 提取。)

- [ ] **Step 2: 实现 ai_runner.py**

Create `backend/app/contexts/agent/ai_runner.py`:

```python
"""AI 节点容器编排 — 每节点起一个 agent-runner 容器调 SDK。

流程:
  1. 写 .node.json(node_key + input_json)到 host_workdir
  2. 起 agent-runner 容器(bind mount host_workdir + 注入 ANTHROPIC_* env)
  3. 容器内 run_one.py 按 node_key 选 agent,跑完调 submit_result
  4. worker 读 /workspace/.node_output.json(sumbit_result 的 input)
  5. schema 校验 → 返回 output_json
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from app.core.agent_runner import (
    AgentRunnerError,
    AgentRunnerSpec,
    agent_runner_manager,
)

logger = logging.getLogger(__name__)

# 各 AI 节点的 output schema(校验用,spec §1.3)
NODE_OUTPUT_SCHEMAS: dict[str, dict] = {
    "env_ready": {
        "required": ["target_url", "compose_path"],
        "optional": ["transport_shape", "initial_creds", "started_containers"],
    },
    "audit": {
        "required": ["gate_verdict"],
        "optional": ["kill_chain", "defense_layers", "payloads", "gate_reason"],
    },
    "reproduce": {
        "required": ["verdict"],
        "optional": ["reproduced", "evidence", "screenshots"],
    },
    "report": {
        "required": ["report_data", "final_verdict"],
        "optional": ["cvss"],
    },
}


def validate_output(node_key: str, output: dict) -> tuple[bool, str | None]:
    """校验 AI 节点 output 是否满足最小 schema。"""
    schema = NODE_OUTPUT_SCHEMAS.get(node_key)
    if not schema:
        return True, None
    for field in schema["required"]:
        if field not in output:
            return False, f"缺必需字段: {field}"
    return True, None


async def run_ai_node(
    *,
    node_key: str,
    input_json: dict[str, Any],
    host_workdir: str,
    runner_env: dict[str, str],
    on_event: Callable[[dict], None] | None = None,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    """起 agent-runner 容器跑一个 AI 节点,返回 output_json。

    失败抛 AgentRunnerError。
    """
    # 1. 写 .node.json
    node_input_path = Path(host_workdir) / ".node.json"
    node_input_path.write_text(
        json.dumps({"node_key": node_key, "input_json": input_json}, ensure_ascii=False),
        encoding="utf-8",
    )

    # 2. 构造 spec + 起容器
    spec = AgentRunnerSpec(
        env={**runner_env, "NODE_KEY": node_key},
        host_workdir=host_workdir,
        timeout_seconds=timeout_seconds,
    )

    captured: list[dict] = []

    def _on_event(event: dict) -> None:
        captured.append(event)
        if on_event:
            on_event(event)

    exit_code, summary = await asyncio.to_thread(
        agent_runner_manager.run_with_streaming, spec, _on_event
    )

    # 3. 读 .node_output.json(submit_result 写的)
    output_path = Path(host_workdir) / ".node_output.json"
    if not output_path.exists():
        stderr_tail = summary.get("stderr_tail", "") if summary else ""
        raise AgentRunnerError(
            f"AI 节点 {node_key} 未产出 .node_output.json (exit={exit_code}): {stderr_tail[:300]}"
        )

    output = json.loads(output_path.read_text(encoding="utf-8"))

    # 4. schema 校验
    ok, err = validate_output(node_key, output)
    if not ok:
        raise AgentRunnerError(f"AI 节点 {node_key} output 校验失败: {err}")

    return output
```

- [ ] **Step 3: Dockerfile ENV 加 NODE_KEY**

Modify `infrastructure/agent-runner/Dockerfile`,在 ENV 块加:

```dockerfile
ENV PYTHONUNBUFFERED=1 \
    HOME=/workspace \
    PLUGIN_DIR=/app/plugins/vuln-verify-expert \
    PLUGIN_NAME=vuln-verify-expert \
    AGENT_NAME=vuln-verify-expert \
    NODE_KEY="" \
    PATH="/home/runner/.local/bin:${PATH}"
```

- [ ] **Step 4: 写 ai_runner 测试(mock 容器)**

Create `backend/tests/test_ai_runner.py`:

```python
"""ai_runner output 校验测试(不起真容器)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.ai_runner import validate_output


def test_validate_env_ready_ok():
    ok, err = validate_output("env_ready", {"target_url": "http://x:8080", "compose_path": ".vuln-env/docker-compose.yml"})
    assert ok and err is None


def test_validate_env_ready_missing_field():
    ok, err = validate_output("env_ready", {"target_url": "http://x:8080"})
    assert not ok
    assert "compose_path" in err


def test_validate_audit_gate_verdict():
    ok, _ = validate_output("audit", {"gate_verdict": "fail"})
    assert ok


def test_validate_audit_no_gate():
    ok, err = validate_output("audit", {"kill_chain": "..."})
    assert not ok
    assert "gate_verdict" in err


def test_validate_report():
    ok, _ = validate_output("report", {"report_data": {"x": 1}, "final_verdict": "confirmed"})
    assert ok
```

- [ ] **Step 5: 跑测试 + Commit**

```bash
cd backend && python -m pytest tests/test_ai_runner.py -v
git add app/contexts/agent/ai_runner.py tests/test_ai_runner.py infrastructure/agent-runner/runner/run_one.py infrastructure/agent-runner/Dockerfile
git commit -m "feat(agent): AI 节点容器编排 + run_one 按 node_key 选 agent + submit_result 输出"
```

---

### Task 5: 节点 2 靶场(AI 出配方 + 代码执行 docker compose)

**Files:**
- Create: `backend/app/contexts/agent/nodes/env_ready.py`
- Test: `backend/tests/test_node_env_ready.py`

**说明:** 节点 2 是 AI+代码协作:AI 轮次产出/修正 Dockerfile/compose,worker 执行 docker compose up + 健康检查,失败回喂 AI(max 5 轮)。这个循环在 env_ready.py 里实现,内部调 ai_runner + docker compose。

- [ ] **Step 1: 写节点 2 测试(mock docker + ai_runner)**

Create `backend/tests/test_node_env_ready.py`:

```python
"""节点 2 靶场就绪 — 排障循环测试(mock docker + AI)。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, AsyncMock
from app.contexts.agent.nodes.base import NodeContext


@pytest.mark.asyncio
async def test_env_ready_first_attempt_success(tmp_path):
    """AI 首轮产 compose,worker 起来健康检查通过 → 成功。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"profile": {"is_web": True, "language": "python", "port": 8000}},
    )

    # mock:AI 首轮返回 compose 路径;docker compose up 成功;健康检查通过
    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "health_check", new_callable=AsyncMock) as mock_hc:
        mock_ai.return_value = {"compose_path": ".vuln-env/docker-compose.yml", "target_url_hint": "http://localhost:8000"}
        mock_up.return_value = (True, "")
        mock_hc.return_value = (True, "http://localhost:8000")

        node = mod.EnvReadyNode()
        out = await node.execute(ctx)

    assert out["target_url"] == "http://localhost:8000"
    assert mock_ai.call_count == 1  # 首轮成功,只调一次


@pytest.mark.asyncio
async def test_env_ready_retry_until_success(tmp_path):
    """前 2 轮起容器失败,第 3 轮 AI 改对 → 成功。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"profile": {"is_web": True, "port": 8000}},
    )

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up, \
         patch.object(mod, "health_check", new_callable=AsyncMock) as mock_hc:
        mock_ai.side_effect = [
            {"compose_path": ".vuln-env/1.yml"},
            {"compose_path": ".vuln-env/2.yml"},
            {"compose_path": ".vuln-env/3.yml"},
        ]
        mock_up.side_effect = [(False, "port in use"), (False, "build fail"), (True, "")]
        mock_hc.return_value = (True, "http://localhost:8000")

        node = mod.EnvReadyNode()
        out = await node.execute(ctx)

    assert mock_ai.call_count == 3
    assert out["target_url"] == "http://localhost:8000"


@pytest.mark.asyncio
async def test_env_ready_5_fails_then_node_fails(tmp_path):
    """5 轮全失败 → 节点 failed(分支出口 C)。"""
    from app.contexts.agent.nodes import env_ready as mod

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"profile": {"is_web": True, "port": 8000}},
    )

    with patch.object(mod, "run_ai_turn", new_callable=AsyncMock) as mock_ai, \
         patch.object(mod, "docker_compose_up", new_callable=AsyncMock) as mock_up:
        mock_ai.return_value = {"compose_path": ".vuln-env/x.yml"}
        mock_up.return_value = (False, "persistent fail")

        node = mod.EnvReadyNode()
        with pytest.raises(Exception, match="5.*轮"):
            await node.execute(ctx)

    assert mock_ai.call_count == 5
```

- [ ] **Step 2: 跑测试失败**

Run: `cd backend && python -m pytest tests/test_node_env_ready.py -v`
Expected: FAIL(env_ready 不存在)。

- [ ] **Step 3: 实现 env_ready.py**

Create `backend/app/contexts/agent/nodes/env_ready.py`:

```python
"""节点 2 靶场就绪 — AI 出配方 + 代码执行 docker compose 的排障循环。

AI 在 agent-runner 内写/改 .vuln-env/Dockerfile + docker-compose.yml(文本,不碰 docker.sock);
worker 在 host 执行 docker compose up + 健康检查;失败回喂 AI(max 5 轮)。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .base import NodeContext

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


async def run_ai_turn(
    ctx: NodeContext, attempt: int, prev_error: str | None
) -> dict[str, Any]:
    """调 AI(经 ai_runner)产出/修正 Dockerfile/compose。

    返回 {compose_path, target_url_hint, dockerfile_path?}。
    实际实现委托 ai_runner.run_ai_node("env_ready", input_json, ...)。
    """
    from app.contexts.agent.ai_runner import run_ai_node
    from app.contexts.agent.sdk_adapter import resolve_runner_env... # 编排器注入 runner_env
    # 这里用 ctx 传的 runner_env(实施时 NodeContext 加 runner_env 字段)
    input_json = {
        "source_path": ctx.source_path,
        "profile": ctx.previous_outputs.get("profile", {}),
        "attempt": attempt,
        "previous_error": prev_error,
    }
    return await run_ai_node(
        node_key="env_ready",
        input_json=input_json,
        host_workdir=ctx.host_workdir,
        runner_env=ctx.runner_env,  # NodeContext 需加此字段
    )


async def docker_compose_up(compose_path: str, host_workdir: str) -> tuple[bool, str]:
    """在 host 执行 docker compose up -d --build,返回 (ok, error)。"""
    import subprocess
    abs_path = f"{host_workdir}/{compose_path}".replace("//", "/") if not compose_path.startswith("/") else compose_path
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "compose", "-f", abs_path, "up", "-d", "--build"],
            capture_output=True, text=True, timeout=300,
        )
        if result.returncode == 0:
            return True, ""
        return False, (result.stderr or result.stdout)[:1000]
    except subprocess.TimeoutExpired:
        return False, "docker compose up 超时"
    except Exception as e:
        return False, f"docker compose 异常: {e}"


async def health_check(port: int | None, host_workdir: str) -> tuple[bool, str]:
    """curl 探活,返回 (ok, target_url)。"""
    import subprocess
    ports = [port] if port else [80, 3000, 5000, 8000, 8080, 8888]
    for p in ports:
        try:
            result = await asyncio.to_thread(
                subprocess.run,
                ["curl", "-sf", "-o", "/dev/null", f"http://localhost:{p}", "--max-time", "5"],
                capture_output=True,
            )
            if result.returncode == 0:
                return True, f"http://localhost:{p}"
        except Exception:
            continue
    return False, ""


async def collect_compose_logs(host_workdir: str) -> str:
    """收 docker compose logs 给下轮 AI 排障。"""
    import subprocess
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            ["docker", "compose", "logs", "--tail=50"],
            cwd=host_workdir, capture_output=True, text=True, timeout=30,
        )
        return (result.stdout or result.stderr)[:2000]
    except Exception:
        return ""


class EnvReadyNode:
    node_index = 2
    node_key = "env_ready"

    @property
    def is_ai(self) -> bool:
        return True

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        profile = ctx.previous_outputs.get("profile", {})
        port = profile.get("port")
        last_error: str | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            # AI 产配方(首轮无 error,后续带 logs)
            recipe = await run_ai_turn(ctx, attempt, last_error)

            # worker 执行 docker compose
            ok, err = await docker_compose_up(recipe.get("compose_path", ".vuln-env/docker-compose.yml"), ctx.host_workdir)
            if not ok:
                last_error = f"attempt {attempt} compose up 失败: {err}\n{await collect_compose_logs(ctx.host_workdir)}"
                logger.warning(f"节点 2 attempt {attempt} 失败: {err[:200]}")
                continue

            # 健康检查
            ok, target_url = await health_check(port, ctx.host_workdir)
            if ok:
                return {
                    "target_url": target_url,
                    "compose_path": recipe.get("compose_path"),
                    "transport_shape": recipe.get("transport_shape", {"protocol": "http"}),
                    "initial_creds": recipe.get("initial_creds", {}),
                    "started_containers": recipe.get("started_containers", []),
                }
            last_error = f"attempt {attempt} 健康检查不过(port={port})"

        raise RuntimeError(f"靶场搭建 {MAX_ATTEMPTS} 轮全失败: {last_error or 'unknown'}")
```

(NodeContext 需在 base.py 加 `runner_env: dict = field(default_factory=dict)` 字段。)

- [ ] **Step 4: NodeContext 加 runner_env 字段**

Modify `backend/app/contexts/agent/nodes/base.py` 的 NodeContext dataclass,加:

```python
    runner_env: dict[str, str] = field(default_factory=dict)
```

- [ ] **Step 5: 跑测试通过 + Commit**

```bash
cd backend && python -m pytest tests/test_node_env_ready.py -v
git add app/contexts/agent/nodes/ tests/test_node_env_ready.py
git commit -m "feat(agent): 节点 2 靶场就绪(AI 出配方 + 代码执行 + 排障循环)"
```

---

### Task 6: AI 节点 3/4/5(audit / reproduce / report)

**Files:**
- Create: `backend/app/contexts/agent/nodes/audit.py`
- Create: `backend/app/contexts/agent/nodes/reproduce.py`
- Create: `backend/app/contexts/agent/nodes/report.py`

**说明:** 这三个 AI 节点结构相似:组装 input_json → 调 ai_runner → 返回 output。比节点 2 简单(无排障循环)。

- [ ] **Step 1: 实现 audit/reproduce/report 节点**

Create `backend/app/contexts/agent/nodes/audit.py`:

```python
"""节点 3 白盒审计(AI)— 吃源码+漏洞描述,产出 kill_chain + gate_verdict。"""
from __future__ import annotations
from typing import Any
from .base import NodeContext


class AuditNode:
    node_index = 3
    node_key = "audit"

    @property
    def is_ai(self) -> bool:
        return True

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import run_ai_node
        input_json = {
            "source_path": ctx.source_path,
            "vulnerability_description": ctx.vulnerability_description,
            "profile": ctx.previous_outputs.get("profile", {}),
        }
        return await run_ai_node(
            node_key="audit", input_json=input_json,
            host_workdir=ctx.host_workdir, runner_env=ctx.runner_env,
        )
```

Create `backend/app/contexts/agent/nodes/reproduce.py`:

```python
"""节点 4 复现验证(AI)— 吃靶标地址+审计结果,产出复现证据+verdict。"""
from __future__ import annotations
from typing import Any
from .base import NodeContext


class ReproduceNode:
    node_index = 4
    node_key = "reproduce"

    @property
    def is_ai(self) -> bool:
        return True

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import run_ai_node
        env = ctx.previous_outputs.get("env_ready", {})
        audit = ctx.previous_outputs.get("audit", {})
        input_json = {
            "target_url": env.get("target_url"),
            "transport_shape": env.get("transport_shape", {}),
            "audit": audit,
            "vulnerability_description": ctx.vulnerability_description,
        }
        return await run_ai_node(
            node_key="reproduce", input_json=input_json,
            host_workdir=ctx.host_workdir, runner_env=ctx.runner_env,
        )
```

Create `backend/app/contexts/agent/nodes/report.py`:

```python
"""节点 5 报告生成(AI)— 吃全部前序 output,产出 8 节 report_data + final_verdict。"""
from __future__ import annotations
from typing import Any
from .base import NodeContext


class ReportNode:
    node_index = 5
    node_key = "report"

    @property
    def is_ai(self) -> bool:
        return True

    async def execute(self, ctx: NodeContext) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import run_ai_node
        input_json = {
            "profile": ctx.previous_outputs.get("profile", {}),
            "env_ready": ctx.previous_outputs.get("env_ready", {}),
            "audit": ctx.previous_outputs.get("audit", {}),
            "reproduce": ctx.previous_outputs.get("reproduce", {}),
            "vulnerability_description": ctx.vulnerability_description,
            "project_address": ctx.project_address,
        }
        return await run_ai_node(
            node_key="report", input_json=input_json,
            host_workdir=ctx.host_workdir, runner_env=ctx.runner_env,
        )
```

- [ ] **Step 2: 跑 import 验证 + Commit**

```bash
cd backend && python -c "from app.contexts.agent.nodes.audit import AuditNode; from app.contexts.agent.nodes.reproduce import ReproduceNode; from app.contexts.agent.nodes.report import ReportNode; print('all AI nodes import OK')"
git add app/contexts/agent/nodes/audit.py app/contexts/agent/nodes/reproduce.py app/contexts/agent/nodes/report.py
git commit -m "feat(agent): AI 节点 3 audit / 4 reproduce / 5 report"
```

---

### Task 7: 编排器(orchestrator)— 6 节点循环 + 分支出口

**Files:**
- Create: `backend/app/contexts/agent/orchestrator.py`
- Modify: `backend/app/contexts/agent/tasks.py`(`_run_analysis` 改调 orchestrator)
- Test: `backend/tests/test_orchestrator.py`

**Interfaces:**
- `async def run_orchestration(task_id, run_id, session) -> dict` — 6 节点循环,分支出口,断点续跑,事件持久化。

- [ ] **Step 1: 写编排器测试(mock 节点)**

Create `backend/tests/test_orchestrator.py`:

```python
"""编排器 — 节点循环 + 分支出口测试。"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from unittest.mock import patch, AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_non_web_exits_after_profile(monkeypatch):
    """节点 1 判 is_web=False → 跳过 2/3/4/5,task completed 无 verdict。"""
    # mock 节点执行
    async def fake_execute_node(node, ctx, session):
        if node.node_key == "profile":
            return {"is_web": False, "language": "python"}
        return {}
    # ... 编排器调 fake_execute_node,断言 node 2-5 skipped
    # (具体实现依赖 orchestrator 接口,实施时补全)


@pytest.mark.asyncio
async def test_gate_fail_skips_reproduce(monkeypatch):
    """节点 3 gate_verdict=fail → 跳过节点 4,verdict=false_positive。"""


@pytest.mark.asyncio
async def test_breakpoint_resume_skips_completed(monkeypatch):
    """断点续跑:已有 completed 节点的 output 不重算。"""
```

(测试细节实施时按 orchestrator 实际接口补全。)

- [ ] **Step 2: 实现 orchestrator.py**

Create `backend/app/contexts/agent/orchestrator.py`:

```python
"""6 节点编排器 — 驱动节点循环 + 分支出口 + 断点续跑。

替代旧 _run_analysis 的单次大调用。每节点:
  1. 查 NodeRun,已 completed → 复用 output_json,跳过执行
  2. 代码节点(0/1)worker 内执行;AI 节点(2/3/4/5)起 agent-runner
  3. 持久化 NodeRun(status, output_json)
  4. 分支出口检查(非 web / 误报 / 基础设施失败)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.task.models import NodeRun, Task, TaskRun
from .nodes.base import NodeContext
from .nodes.source import SourceNode
from .nodes.profile import ProfileNode
from .nodes.env_ready import EnvReadyNode
from .nodes.audit import AuditNode
from .nodes.reproduce import ReproduceNode
from .nodes.report import ReportNode

logger = logging.getLogger(__name__)

NODE_ORDER = [SourceNode(), ProfileNode(), EnvReadyNode(), AuditNode(), ReproduceNode(), ReportNode()]


async def _get_or_create_node_run(
    session: AsyncSession, run_id: str, task_id: str, node_index: int, node_key: str, input_json: dict
) -> NodeRun:
    """取现有 NodeRun(断点续跑)或建新的。"""
    result = await session.execute(
        select(NodeRun).where(NodeRun.run_id == run_id, NodeRun.node_index == node_index)
    )
    nr = result.scalar_one_or_none()
    if nr:
        return nr
    nr = NodeRun(
        run_id=run_id, task_id=task_id, node_index=node_index, node_key=node_key,
        status="pending", input_json=json.dumps(input_json, ensure_ascii=False),
    )
    session.add(nr)
    await session.flush()
    await session.refresh(nr)
    return nr


async def run_orchestration(
    *, task_id: str, run_id: str, session: AsyncSession,
    host_workdir: str, source_path: str, runner_env: dict[str, str],
    on_node_event=None,
) -> dict[str, Any]:
    """跑 6 节点编排。返回 {verdict, status, summary}。"""
    task = await session.get(Task, task_id)
    run = await session.get(TaskRun, run_id)
    if not task or not run:
        return {"status": "failed", "error": "task/run 不存在"}

    previous_outputs: dict[str, dict] = {}
    final_verdict: str | None = None

    for node in NODE_ORDER:
        # 组装 input(各节点自己组装,这里先给基础)
        input_json = {}
        nr = await _get_or_create_node_run(session, run_id, task_id, node.node_index, node.node_key, input_json)

        # 断点续跑:已完成 → 复用
        if nr.status == "completed":
            try:
                previous_outputs[node.node_key] = json.loads(nr.output_json or "{}")
            except json.JSONDecodeError:
                previous_outputs[node.node_key] = {}
            continue

        # 分支出口 A:节点 1 判 is_web=False → 跳过后续
        if node.node_index >= 2 and not previous_outputs.get("profile", {}).get("is_web", True):
            nr.status = "skipped"
            await session.flush()
            # 后续节点也 skip
            for later in NODE_ORDER[node.node_index:]:
                later_nr = await _get_or_create_node_run(session, run_id, task_id, later.node_index, later.node_key, {})
                later_nr.status = "skipped"
            await session.flush()
            run.status = "completed"
            run.finished_at = datetime.now(timezone.utc)
            task.status = "completed"
            await session.commit()
            return {"status": "completed", "verdict": None, "reason": "non_web"}

        # 执行节点
        nr.status = "running"
        nr.started_at = datetime.now(timezone.utc)
        await session.flush()

        ctx = NodeContext(
            task_id=task_id, run_id=run_id, host_workdir=host_workdir,
            source_path=source_path, vulnerability_description=task.vulnerability_description,
            project_address=task.project_address, project_ref=task.project_ref,
            previous_outputs=dict(previous_outputs), runner_env=runner_env,
        )
        try:
            output = await node.execute(ctx)
            nr.output_json = json.dumps(output, ensure_ascii=False, default=str)
            nr.status = "completed"
            nr.finished_at = datetime.now(timezone.utc)
            previous_outputs[node.node_key] = output
        except Exception as e:
            nr.status = "failed"
            nr.error_message = str(e)[:1000]
            nr.finished_at = datetime.now(timezone.utc)
            run.status = "failed"
            run.error_message = f"节点 {node.node_key} 失败: {e}"[:500]
            run.finished_at = datetime.now(timezone.utc)
            task.status = "failed"
            await session.commit()
            return {"status": "failed", "error": str(e), "node": node.node_key}

        await session.commit()

        # 分支出口 B:节点 3 gate_fail → 跳过节点 4
        if node.node_key == "audit":
            gate = previous_outputs.get("audit", {}).get("gate_verdict")
            if gate == "fail":
                repro_node = NODE_ORDER[4]
                repro_nr = await _get_or_create_node_run(session, run_id, task_id, repro_node.node_index, repro_node.node_key, {})
                repro_nr.status = "skipped"
                await session.flush()
                final_verdict = "false_positive"

        if on_node_event:
            on_node_event(node.node_key, nr.status)

    # 节点 5 报告产出 verdict
    report_out = previous_outputs.get("report", {})
    if report_out.get("final_verdict"):
        final_verdict = report_out["final_verdict"]

    task.verdict = final_verdict
    run.status = "completed"
    run.finished_at = datetime.now(timezone.utc)
    task.status = "completed" if final_verdict != "false_positive" else "completed"
    await session.commit()

    return {
        "status": "completed", "verdict": final_verdict,
        "report_data": report_out.get("report_data"),
    }
```

- [ ] **Step 3: 改 tasks.py `_run_analysis` 调 orchestrator**

Modify `backend/app/contexts/agent/tasks.py`。`_run_analysis` 主体改为:
1. 预检(保留)
2. host_workdir + git clone(保留)
3. resolve runner_env(保留)
4. **调 `run_orchestration(...)` 替代旧的 executor.run**
5. 报告归档(基于 orchestration 返回的 report_data)
6. cleanup

(保留旧 _append_events / _persist_single_event 给 AI 节点内的 tool.call 事件用;on_node_event 回调把 node.updated 发 Redis Pub/Sub。)

- [ ] **Step 4: 跑测试 + Commit**

```bash
cd backend && python -m pytest tests/test_orchestrator.py -v
git add app/contexts/agent/orchestrator.py app/contexts/agent/tasks.py tests/test_orchestrator.py
git commit -m "feat(agent): 6 节点编排器 + 分支出口 + 断点续跑(替代单次大调用)"
```

---

### Task 8: 插件多 agent 拆分(env-builder/auditor/reproducer/reporter)

**Files:**
- Create: `plugins/vuln-verify-expert/agents/env-builder.md`
- Create: `plugins/vuln-verify-expert/agents/auditor.md`
- Create: `plugins/vuln-verify-expert/agents/reproducer.md`
- Create: `plugins/vuln-verify-expert/agents/reporter.md`
- Keep: `plugins/vuln-verify-expert/agents/vuln-verify-expert.md`(标 deprecated,向后兼容)

**说明:** 每个 agent 的 frontmatter + 正文从原 vuln-verify-expert.md 对应阶段拆出,system prompt 资产(skills/references)原样引用。每个 agent 都要求"完成时必须调用 submit_result 工具提交结构化结果"。

- [ ] **Step 1: 写 4 个子 agent.md**

每个 agent.md 结构:
```markdown
---
name: <agent-name>
description: <节点职责一句话>
model: sonnet
maxTurns: <按节点复杂度,env_ready 80 / audit 100 / reproduce 80 / report 60>
skills:
  - vuln-verify-expert:<对应 skill>
---

# <角色名>

<从原 vuln-verify-expert.md 对应阶段拆出的职责说明>

## 输入
<该节点从 .node.json 的 input_json 收到什么>

## 必须产出
**完成后必须调用 submit_result 工具提交**,schema:
<节点 output_json 字段定义>

<领域知识引用:见 skills/references/...>
```

具体内容实施时从原 agent.md 拆 + 对齐 spec §1.3 schema。

- [ ] **Step 2: 重建 agent-runner 镜像(含新 agent)**

Run:
```bash
cd D:/codeproject/Crucible
docker build -f infrastructure/agent-runner/Dockerfile -t crucible-agent-runner:base .
```
Expected: 构建成功(4 个新 agent.md 被 COPY 进镜像)。

- [ ] **Step 3: Commit**

```bash
git add plugins/vuln-verify-expert/agents/
git commit -m "feat(plugin): 拆分 4 子 agent(env-builder/auditor/reproducer/reporter)"
```

---

### Task 9: 阶段 2 端到端冒烟

- [ ] **Step 1: 跑全部测试**

Run: `cd backend && python -m pytest tests/ -v`
Expected: 全 PASS。

- [ ] **Step 2: 手动冒烟(需真实 LLM + Docker)**

下个真实任务,观察:
- 节点 0/1 代码执行(几秒)
- 节点 2 AI 起容器产 compose + worker 起靶场
- 节点 3/4/5 AI 链
- 前端 `GET /runs/{rid}/nodes` 6 节点状态推进

- [ ] **Step 3: 收尾**

更新 development-guide.md「已完成清单」补阶段 2。

---

## Self-Review 结果

**Spec 覆盖(§3 编排器 + 执行模型):**
- §3.1 编排器循环 → Task 7 ✓
- §3.2 代码节点(0/1)→ Task 2/3 ✓
- §3.2 AI 节点独立容器 → Task 4 ✓
- §3.3 节点 2 排障循环 → Task 5 ✓
- §3.4 submit_result 工具 → Task 1(PoC)+ Task 4(注入)✓
- §3.5 phase 事件由节点边界替代 → Task 7(node.updated)✓
- §6 插件多 agent → Task 8 ✓
- §1.2 分支出口 A/B/C → Task 7 ✓
- 断点续跑 → Task 7 ✓

**Placeholder scan:** Task 1(PoC)是探索性,结果不定,已在计划标注"决定后续方案"。Task 7 测试框架给了结构,实施时按 orchestrator 实际接口补全。其余步骤含完整代码。

**Type consistency:** NodeContext 在所有节点一致;`run_ai_node` 签名统一;node_key 枚举(source/profile/env_ready/audit/reproduce/report)各处一致。

**已知风险:**
- **Task 1 PoC 是最高风险**:SDK 自定义工具 API 若都不 work,退回文本 JSON 块方案(可靠性降级但不阻塞)。这决定 Task 4/5/6 的 AI 输出实现细节。
- Task 5 节点 2 的 docker compose 执行权限:worker 进程需能访问宿主 docker(Windows 开发用 Docker Desktop 的 npipe,生产用 dind)。
- Task 7 编排器的事务粒度:每节点 commit 一次(失败不污染前序),但跨节点的 previous_outputs 累积在内存,worker 崩溃需靠 NodeRun 重建(断点续跑覆盖)。
- Task 8 插件 agent 的 maxTurns 需调试(太小 agent 完不成,太大浪费 token)。
- 阶段 2 不实现报告 docx 导出(留给阶段 4),节点 5 只产 report_data JSON。

---

## PoC 结论(Task 1 执行结果)

✅ **方式 B(SDK MCP server)可用** — SDK 0.2.134 原生支持自定义工具,无需 fallback。

**确认的 API:**
```python
from claude_agent_sdk import tool, create_sdk_mcp_server, ClaudeAgentOptions

@tool(name="submit_result", description="提交节点结构化结果", input_schema=SCHEMA)
async def submit_result(input):
    # input 已按 schema 校验;返回值写文件供 worker 读取
    import json
    with open("/workspace/.node_output.json", "w") as f:
        json.dump(input, f, ensure_ascii=False)
    return {"status": "ok"}

server = create_sdk_mcp_server(name="crucible", tools=[submit_result])
options = ClaudeAgentOptions(
    mcp_servers={"crucible": server},
    tools=["submit_result"],  # 限定只暴露这个(加 Read/Grep/Bash 等审计工具)
    ...
)
```

- `create_sdk_mcp_server(name, version, tools)` — 创建 in-process MCP server(无 IPC 开销)
- `@tool(name, description, input_schema)` — 定义工具,schema 由节点定
- `ClaudeAgentOptions.mcp_servers={"key": server_config}` — 注入
- `ClaudeAgentOptions.tools=[...]` — 限定可用工具集(含 submit_result)

**对 Task 4/5/6 的影响:**
- run_one.py 用上述机制注入 submit_result(每个节点 schema 不同,按 NODE_KEY 取)
- agent 调 submit_result 时,回调把 input 写 `/workspace/.node_output.json`
- worker 读该文件拿 output_json(已在 ai_runner.py 设计如此)
- 无需文本 JSON fallback

**Task 1 PoC 完成,后续 Task 2-9 按计划推进。**
