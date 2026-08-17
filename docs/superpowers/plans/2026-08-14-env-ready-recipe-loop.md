# 靶场配方闭环与 MinIO 复用 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 创建者先用 MinIO 已验证配方 `compose up`；失败立刻回喂 AI 改配方；成功后把 `.vuln-env/` 存 MinIO。AI 禁止在 runner 里装依赖或起靶场。

**Architecture:** Lab Context 新增 `recipe_store`（bucket `crucible-lab-recipe`，key 由 owner+project+sha 推导）。`EnvReadyNode` 在 `role==create` 时先 `download_recipe`；命中则平台改占用宿主口后 up 一次，失败再进现有 5 轮 AI 循环。`LabService.upload_recipe` 在探活成功、`mark_ready` 前覆盖上传。`run_one.py` 仅对 `env_ready` 加 npm/pip/docker 黑名单。

**Tech Stack:** Python 3.11 / FastAPI / MinIO / Docker Compose / pytest

**Spec:** `docs/superpowers/specs/2026-08-14-env-ready-recipe-loop-design.md`

## Global Constraints

- 提交：`type(scope):` 英文，描述主体简体中文；**用户未明确要求则跳过每个 Task 的 Commit 步**（仓库 git-workflow）
- Windows 提交用 `git commit -m "subject" -m "body"`，不用 HEREDOC
- 跨 Context 只走 Service；agent/task **禁止** import `lab.recipe_store` 的 MinIO 客户端，只调 `LabService`
- Mock（`claude_agent_sdk_enabled=false`）不写 labs、不碰 Docker、不调配方 up/download
- MinIO **只存配方文件**，不存镜像 tar
- 配方复用键：`owner_id + project_id + commit_sha`，不跨用户
- `compose up`/探活失败立刻进 AI，不拿同一稿再 up；docker 守护进程不可用则节点失败、不进 AI
- 路径正斜杠；测试种子可复制 `backend/tests/test_lab_acquire.py` 的 `seed`/`SHA`，不要 import 测试模块
- 后端测试：`d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest ...`
- 当前分支若已有 lab 缺口修复（stale runtime 等），叠加上面改，不要还原无关 diff

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `backend/app/contexts/lab/recipe_store.py` | 新建 | key / 打包解压 / Memory+MinIO store |
| `backend/app/contexts/lab/service.py` | 改 | `download_recipe` / `upload_recipe`；rebuild 缺文件时拉 MinIO |
| `backend/app/contexts/agent/nodes/env_ready.py` | 改 | 创建者配方短路、宿主口改写、docker 不可用 fail-fast、成功上传 |
| `infrastructure/agent-runner/runner/run_one.py` | 改 | `env_ready` Bash 拒绝 npm/pip/docker |
| `plugins/vuln-verify-expert/agents/env-builder.md` | 改 | runner ≠ 靶场 |
| `plugins/vuln-verify-expert/skills/run-project-env/SKILL.md` | 改 | 平台专章 |
| `plugins/vuln-verify-expert/skills/run-project-env/references/isolation.md` | 改 | 平台语境 |
| 权威文档 | 改 | lab-lifecycle §2、编排 §3.3、api-contract rebuild、development-guide |

---

### Task 1: 配方打包与 LabService 存取

**Files:**
- Create: `backend/app/contexts/lab/recipe_store.py`
- Modify: `backend/app/contexts/lab/service.py`（构造可注入 store；新增 upload/download）
- Test: `backend/tests/test_lab_recipe_store.py`

**Interfaces:**
- Produces:
  - `RECIPE_BUCKET = "crucible-lab-recipe"`
  - `recipe_object_key(owner_id: str, project_id: str, commit_sha: str) -> str` → `recipe/{owner_id}/{project_id}/{commit_sha}.tar.gz`
  - `pack_recipe(lab_workdir: str, archive_path: str, meta: dict) -> None`
  - `extract_recipe(archive_path: str, dest_workdir: str) -> dict`（返回 meta；缺文件则 `{}`）
  - `MemoryRecipeStore`：`upload(object_key, archive_path)` / `download(object_key, dest_path)`；缺失 raise `FileNotFoundError`
  - `LabService.download_recipe(*, owner_id, project_id, commit_sha, dest_workdir) -> dict | None`：命中返回 `{compose_path, transport_shape, initial_creds, started_containers}` 且磁盘有 `{dest}/.vuln-env/`；未命中或解压失败返回 `None`（打 warning，不抛）
  - `LabService.upload_recipe(*, owner_id, project_id, commit_sha, lab_workdir, compose_path, transport_shape, initial_creds, started_containers=None) -> None`：打包 lab_workdir 的 `.vuln-env/` + `recipe-meta.json`；失败只打 error 日志

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_lab_recipe_store.py`:

```python
"""Lab 配方 MinIO 打包 / 解压 / Service 存取。"""
import os
import sys
import tarfile
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

SHA = "a" * 40


def test_recipe_object_key_and_bucket():
    from app.contexts.lab.recipe_store import RECIPE_BUCKET, recipe_object_key

    assert RECIPE_BUCKET == "crucible-lab-recipe"
    assert recipe_object_key("u1", "p1", SHA) == f"recipe/u1/p1/{SHA}.tar.gz"


def test_pack_and_extract_roundtrip(tmp_path):
    from app.contexts.lab.recipe_store import extract_recipe, pack_recipe

    work = tmp_path / "lab"
    (work / ".vuln-env").mkdir(parents=True)
    (work / ".vuln-env" / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    archive = tmp_path / "r.tar.gz"
    meta = {
        "compose_path": ".vuln-env/docker-compose.yml",
        "transport_shape": {"protocol": "http"},
        "initial_creds": {"user": "a"},
        "started_containers": ["web"],
    }
    pack_recipe(str(work), str(archive), meta)
    names = tarfile.open(archive, "r:gz").getnames()
    assert any(n.endswith(".vuln-env/docker-compose.yml") or n == ".vuln-env/docker-compose.yml" for n in names)
    assert any(n.endswith("recipe-meta.json") or n == "recipe-meta.json" for n in names)

    dest = tmp_path / "out"
    dest.mkdir()
    got = extract_recipe(str(archive), str(dest))
    assert got["initial_creds"] == {"user": "a"}
    assert (dest / ".vuln-env" / "docker-compose.yml").is_file()


@pytest.mark.asyncio
async def test_download_missing_returns_none(tmp_path):
    from app.contexts.lab.recipe_store import MemoryRecipeStore
    from app.contexts.lab.service import LabService

    svc = LabService(MagicMock(), recipe_store=MemoryRecipeStore())
    assert await svc.download_recipe(
        owner_id="u1", project_id="p1", commit_sha=SHA, dest_workdir=str(tmp_path)
    ) is None


@pytest.mark.asyncio
async def test_upload_then_download_via_service(tmp_path):
    from app.contexts.lab.recipe_store import MemoryRecipeStore
    from app.contexts.lab.service import LabService

    store = MemoryRecipeStore()
    svc = LabService(MagicMock(), recipe_store=store)
    work = tmp_path / "lab"
    (work / ".vuln-env").mkdir(parents=True)
    (work / ".vuln-env" / "docker-compose.yml").write_text("services:\n  web: {}\n", encoding="utf-8")
    await svc.upload_recipe(
        owner_id="u1",
        project_id="p1",
        commit_sha=SHA,
        lab_workdir=str(work),
        compose_path=".vuln-env/docker-compose.yml",
        transport_shape={"protocol": "http"},
        initial_creds={},
        started_containers=["web"],
    )
    dest = tmp_path / "dest"
    dest.mkdir()
    hit = await svc.download_recipe(
        owner_id="u1", project_id="p1", commit_sha=SHA, dest_workdir=str(dest)
    )
    assert hit is not None
    assert hit["compose_path"] == ".vuln-env/docker-compose.yml"
    assert (dest / ".vuln-env" / "docker-compose.yml").is_file()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_lab_recipe_store.py -q`
Expected: FAIL（`recipe_store` 不存在或 `LabService` 不接受 `recipe_store`）

- [ ] **Step 3: 实现**

`recipe_store.py` 对齐 `project/source_cache.py`：`MemoryRecipeStore` 用 dict 存 bytes；`MinioRecipeStore` 用同一套 s3 settings，bucket `crucible-lab-recipe`。`pack_recipe` 把 `{lab_workdir}/.vuln-env` 打进 tar 的 `.vuln-env/`，并把 `recipe-meta.json` 放 tar 根。`extract_recipe` 解到 `dest_workdir`（使 `.vuln-env` 出现在 dest 下），读 meta；非法 JSON 当 `{}`。

`LabService.__init__(self, session, recipe_store=None)`：`self.recipe_store = recipe_store or default_recipe_store()`。默认 MinIO store。`download_recipe`：`FileNotFoundError` 或任意解压/下载异常 → `None` + warning。无 compose 文件（默认路径或不完整）→ `None`。meta 缺字段用 spec 默认。`upload_recipe`：无 `.vuln-env` 目录则 warning 并 return；store 异常 catch 打 error，不抛。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_lab_recipe_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**（仅用户要求时）

```
test(lab): 配方 MinIO 存取与打包解压

同 SHA 下次创建要跳过 AI，先把配方对象的 key 与字节契约锁死。
```

---

### Task 2: 宿主口改写与 docker 不可用判定

**Files:**
- Modify: `backend/app/contexts/agent/nodes/env_ready.py`（纯函数即可，先不接线）
- Test: `backend/tests/test_compose_path.py`（已有 compose 解析测试，追加）或 `backend/tests/test_recipe_port_rewrite.py`

**Interfaces:**
- Produces:
  - `rewrite_compose_host_ports(text: str, occupied: set[int]) -> str | None`：冲突的 **host** 口改为未被占用的口（从原口 +1 起扫描）；只改 `"HOST:CONTAINER"` 短语法和 `published:` 长语法的宿主侧；改不了返回 `None`。无冲突返回原文。
  - `is_docker_unavailable(err: str) -> bool`：`Cannot connect to the Docker daemon`、`docker compose 异常:` → True；Unix ENOENT 仅当指向 docker 可执行文件或 `docker.sock`（构建日志里的 `docker.io` + `no such file or directory` 必须为 False）

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_recipe_port_rewrite.py`:

```python
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.nodes.env_ready import (
    is_docker_unavailable,
    rewrite_compose_host_ports,
)


def test_rewrite_skips_when_free():
    text = 'services:\n  web:\n    ports:\n      - "3001:3000"\n'
    assert rewrite_compose_host_ports(text, set()) == text


def test_rewrite_host_side_only():
    text = 'services:\n  web:\n    ports:\n      - "3001:3000"\n'
    out = rewrite_compose_host_ports(text, {3001})
    assert out is not None
    assert "3000" in out
    assert "3001:3000" not in out.replace(" ", "")


def test_rewrite_returns_none_when_no_ports():
    assert rewrite_compose_host_ports("services: {}\n", {80}) is None


def test_docker_unavailable_detects_daemon():
    assert is_docker_unavailable("Cannot connect to the Docker daemon at unix:///var/run/docker.sock")
    assert is_docker_unavailable("docker compose 异常: [WinError 2] ...")
    assert not is_docker_unavailable("failed to build: npm ci exited 1")
```

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_recipe_port_rewrite.py -q`
Expected: FAIL（函数未定义）

- [ ] **Step 3: 实现最小函数**

在 `env_ready.py` 用已有 `parse_compose_port_mappings` 找冲突 host 口；替换短语法 `(\d+):(\d+)` 中第一段；长语法只改 `published:` 行。选口：从 `host+1` 递增直到不在 `occupied ∪ 已新分配`。没有任何 Web 映射可改时返回 `None`。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_recipe_port_rewrite.py tests/test_compose_path.py -q`
Expected: PASS

- [ ] **Step 5: Commit**（仅用户要求时）

```
feat(agent): 配方命中后由平台改宿主端口

避免仅因口占用就重新跑 AI 分析。
```

---

### Task 3: 创建者 MinIO 短路 + 失败进 AI + 成功上传

**Files:**
- Modify: `backend/app/contexts/agent/nodes/env_ready.py`（`_create_lab`）
- Modify: `backend/tests/test_env_ready_lab_reuse.py`（已有 create 测试必须 stub `download_recipe=None`，否则 MagicMock 当真命中）
- Test: 同文件追加用例

**Interfaces:**
- Consumes: Task 1 的 `download_recipe` / `upload_recipe`；Task 2 的改口与 `is_docker_unavailable`
- Produces: `_create_lab` 行为按 spec §3

现有 create 测试（`test_env_ready_create_compose_up_uses_lab_id`、`test_env_ready_create_strips_workspace_compose_path`）在 mock `LabService` 时补：

```python
LS.return_value.download_recipe = AsyncMock(return_value=None)
LS.return_value.upload_recipe = AsyncMock()
```

成功路径在 `mark_ready` 之后（或之前，与 spec「即将 mark_ready 之前」一致：先 `upload_recipe` 再 `mark_ready`；upload 失败不阻断）调用：

```python
await svc.upload_recipe(
    owner_id=ctx.owner_id,
    project_id=ctx.project_id or "",
    commit_sha=sha_from_source,
    lab_workdir=result.workdir,
    compose_path=lab_compose,
    transport_shape=output["transport_shape"],
    initial_creds=output["initial_creds"],
    started_containers=output.get("started_containers") or [],
)
```

`_try_cached_recipe(...)` 建议抽函数以免 `_create_lab` 更膨胀：返回 `dict | None`（成功产出）或抛 `DockerUnavailableError`；失败返回 `None` 并设置 `last_error` 给后续 AI 循环。

- [ ] **Step 1: 写失败测试**

Append to `backend/tests/test_env_ready_lab_reuse.py`:

```python
@pytest.mark.asyncio
async def test_create_recipe_hit_skips_ai(tmp_path):
    lab_dir = tmp_path / "lab"
    (lab_dir / ".vuln-env").mkdir(parents=True)
    (lab_dir / ".vuln-env" / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab = SimpleNamespace(
        lab_id="lab1", role="create", status="creating", reused=False,
        workdir=str(lab_dir), compose_project="crucible-lab-lab1",
        target_url=None, compose_path=".vuln-env/docker-compose.yml",
        transport_shape={}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    hit = {
        "compose_path": ".vuln-env/docker-compose.yml",
        "transport_shape": {"protocol": "http"},
        "initial_creds": {},
        "started_containers": ["web"],
    }
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.nodes.env_ready.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.list_docker_occupied_host_ports", return_value=set()):
        gs.return_value.claude_agent_sdk_enabled = True
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=hit)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock()
        up.return_value = (True, "")
        hc.return_value = (True, 3001)
        out = await EnvReadyNode().execute(ctx)
    ai.assert_not_awaited()
    up.assert_awaited_once()
    assert out["reused"] is True
    assert out["target_url"] == "http://10.0.0.8:3001"
    LS.return_value.upload_recipe.assert_awaited()


@pytest.mark.asyncio
async def test_create_recipe_hit_up_fail_goes_ai_once(tmp_path):
    repo = tmp_path / "b" / ".vuln-env"
    repo.mkdir(parents=True)
    (repo / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab_dir = tmp_path / "lab"
    (lab_dir / ".vuln-env").mkdir(parents=True)
    (lab_dir / ".vuln-env" / "docker-compose.yml").write_text(
        'services:\n  web:\n    image: x\n    ports:\n      - "3001:3000"\n',
        encoding="utf-8",
    )
    lab = SimpleNamespace(
        lab_id="lab1", role="create", status="creating", reused=False,
        workdir=str(lab_dir), compose_project="crucible-lab-lab1",
        target_url=None, compose_path=".vuln-env/docker-compose.yml",
        transport_shape={}, initial_creds={},
    )
    ctx = _ctx(tmp_path)
    hit = {
        "compose_path": ".vuln-env/docker-compose.yml",
        "transport_shape": {"protocol": "http"},
        "initial_creds": {},
        "started_containers": ["web"],
    }
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS, \
         patch("app.contexts.agent.nodes.env_ready.run_ai_turn", new_callable=AsyncMock) as ai, \
         patch("app.contexts.agent.nodes.env_ready.docker_compose_up", new_callable=AsyncMock) as up, \
         patch("app.contexts.agent.nodes.env_ready.docker_compose_down", new_callable=AsyncMock), \
         patch("app.contexts.agent.nodes.env_ready.health_check", new_callable=AsyncMock) as hc, \
         patch("app.contexts.agent.nodes.env_ready.host_advertise_ip", return_value="10.0.0.8"), \
         patch("app.contexts.agent.nodes.env_ready.list_docker_occupied_host_ports", return_value=set()), \
         patch("app.contexts.agent.nodes.env_ready.collect_compose_logs", new_callable=AsyncMock, return_value="boom"):
        gs.return_value.claude_agent_sdk_enabled = True
        LS.return_value.acquire = AsyncMock(return_value=lab)
        LS.return_value.download_recipe = AsyncMock(return_value=hit)
        LS.return_value.upload_recipe = AsyncMock()
        LS.return_value.mark_ready = AsyncMock()
        LS.return_value.mark_failed = AsyncMock()
        up.side_effect = [(False, "build failed"), (True, "")]
        hc.return_value = (True, 3001)
        ai.return_value = {
            "compose_path": ".vuln-env/docker-compose.yml",
            "target_url": "http://localhost:3001",
        }
        await EnvReadyNode().execute(ctx)
    assert up.await_count == 2
    assert ai.await_count == 1
    assert ai.await_args.args[1] == 1
    assert "build failed" in (ai.await_args.args[2] or "")


@pytest.mark.asyncio
async def test_mock_skips_recipe_store(tmp_path):
    ctx = _ctx(tmp_path)
    with patch("app.core.config.get_settings") as gs, \
         patch("app.contexts.lab.service.LabService") as LS:
        gs.return_value.claude_agent_sdk_enabled = False
        await EnvReadyNode().execute(ctx)
    LS.assert_not_called()
```

把已有 `test_mock_sdk_skips_lab_acquire` 与新 mock 用例合并检查即可，避免重复断言冲突。

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_env_ready_lab_reuse.py -q`
Expected: 新用例 FAIL（命中后仍调 AI）

- [ ] **Step 3: 实现 `_create_lab` 短路**

伪代码：

```python
hit = await svc.download_recipe(...)
if hit:
    # 改口（写回 lab compose 文件）→ up 一次
    # unavailable → mark_failed + raise
    # ok+health → mark_ready + upload + return {..., reused: True}
    # fail → down，last_error = logs，fall through
# 现有 for attempt in 1..5
# 成功分支加 upload_recipe
```

`ctx.project_id` 在 execute 里已 `_resolve_project_id`，传入 `_create_lab`（给 upload/download 用）。不要在 mock 路径调用 LabService。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_env_ready_lab_reuse.py tests/test_node_env_ready.py -q`
Expected: PASS

- [ ] **Step 5: Commit**（仅用户要求时）

```
feat(agent): 创建者优先复用 MinIO 配方

TTL 后同 SHA 不应再分析一遍；up 失败才回喂 AI。
```

---

### Task 4: rebuild 缺本地文件时从 MinIO 拉回

**Files:**
- Modify: `backend/app/contexts/lab/service.py`（`rebuild_lab`）
- Test: `backend/tests/test_lab_api.py`

**Interfaces:**
- Consumes: `download_recipe`
- Produces: 本地 compose 缺失时 `download_recipe(owner, project, sha, lab.workdir)`；仍无文件则 `ValueError("缺少配方，请从验证任务重新创建")`

- [ ] **Step 1: 写失败测试**

Append to `test_lab_api.py`（沿用 `ready_lab` / `SHA`）：

```python
@pytest.mark.asyncio
async def test_rebuild_downloads_recipe_when_file_missing(session, tmp_path):
    svc, result, task = await ready_lab(session)
    task.status = "completed"
    lab = await session.get(__import__("app.contexts.lab.models", fromlist=["Lab"]).Lab, result.lab_id)
    lab.workdir = str(tmp_path).replace("\\", "/")
    await session.commit()

    async def fake_download(**kwargs):
        dest = kwargs["dest_workdir"]
        os.makedirs(os.path.join(dest, ".vuln-env"), exist_ok=True)
        path = os.path.join(dest, ".vuln-env", "docker-compose.yml")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("services: {}\n")
        return {"compose_path": ".vuln-env/docker-compose.yml"}

    svc.download_recipe = fake_download
    with patch("app.contexts.lab.docker_ops.compose_up_build", new_callable=AsyncMock) as up:
        status = await svc.rebuild_lab(result.lab_id, owner_id="u1")
    assert status == "ready"
    up.assert_awaited()
```

`test_rebuild_without_recipe_has_exact_message` 保持：download 返回 None 仍 400。给该测试里的 svc 确保默认 Memory 空 store 或 patch `download_recipe` 返回 None（若默认改成打真实 MinIO 会联网失败——Task 1 默认 MinIO，单测 `ready_lab` 用的 LabService 无注入。rebuild 缺文件时调用 download：应用 try/except 已吞成 None，但不要真连 MinIO。

**实现约束：** `download_recipe` 已把异常收成 None，单测不注入 store 时不要在 rebuild 测试里打到真实网络。给 `LabService` 默认 store 在 `ENVIRONMENT=test` 或 `recipe_store is None` 时用 `MemoryRecipeStore()`。更干净：`default_recipe_store()` 若 settings 无 s3 则 Memory。**本任务规定：** 单测继续 `LabService(session)`；`download_recipe` 在 store.download 抛错时返回 None。给 `test_rebuild_without_recipe` 显式：

```python
svc.download_recipe = AsyncMock(return_value=None)
```

否则空 Memory 即 None，与现行为一致。

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_lab_api.py::test_rebuild_downloads_recipe_when_file_missing tests/test_lab_api.py::test_rebuild_without_recipe_has_exact_message -q`
Expected: 第一条 FAIL（仍 400）

- [ ] **Step 3: 改 `rebuild_lab`**

在 `os.path.isfile(compose_file)` 为假时调用 `await self.download_recipe(...)`，再查一次文件；仍无则原来的 ValueError。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_lab_api.py -q`
Expected: PASS

- [ ] **Step 5: Commit**（仅用户要求时）

```
feat(lab): rebuild 缺失配方时从 MinIO 恢复

管理页重建不应因 TTL 清盘后本地文件消失而 400。
```

---

### Task 5: env_ready Bash 拒绝装依赖与 docker

**Files:**
- Modify: `infrastructure/agent-runner/runner/run_one.py`（`_classify_bash` 增加 `node_key`；hook 读 `NODE_KEY`）
- Test: `backend/tests/test_run_one_node_prompt.py`（已能 import runner）追加或 `backend/tests/test_run_one_bash_policy.py`

**Interfaces:**
- Produces: `NODE_KEY=env_ready` 时 deny 命令词：`npm` `npx` `yarn` `pnpm` `pip` `pip3` `apt` `apt-get` `apk` `yum` `dnf` `docker`；`node -e '1'` allow。其它 node_key 不启用这份名单。

- [ ] **Step 1: 写失败测试**

Create `backend/tests/test_run_one_bash_policy.py`（与 `test_run_one_node_prompt.py` 同样 mock `claude_agent_sdk`、把 `infrastructure/agent-runner` 插入 `sys.path`）：

```python
import pytest
from runner.run_one import _classify_bash


@pytest.mark.parametrize(
    "cmd",
    ["npm install", "npx eslint", "pip install flask", "apt-get update", "docker compose up"],
)
def test_env_ready_denies_install_and_docker(cmd):
    decision, reason = _classify_bash(cmd, node_key="env_ready")
    assert decision == "deny"
    assert reason and "blocked by policy" in reason


def test_env_ready_allows_node_eval():
    decision, _ = _classify_bash("node -e \"console.log(1)\"", node_key="env_ready")
    assert decision == "allow"


def test_audit_still_allows_npm():
    decision, _ = _classify_bash("npm install", node_key="audit")
    assert decision == "allow"
```

（若现有黑名单已 deny 某条，以「env_ready deny、audit 对 npm allow」为准。）

- [ ] **Step 2: 跑测试确认失败**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_run_one_bash_policy.py -q`
Expected: FAIL（`_classify_bash` 无 `node_key` 或不 deny npm）

- [ ] **Step 3: 实现**

```python
ENV_READY_DENY_RES = [
    (re.compile(r"\bnpm\b"), "npm"),
    (re.compile(r"\bnpx\b"), "npx"),
    (re.compile(r"\byarn\b"), "yarn"),
    (re.compile(r"\bpnpm\b"), "pnpm"),
    (re.compile(r"\bpip3?\b"), "pip"),
    (re.compile(r"\bapt(-get)?\b"), "apt"),
    (re.compile(r"\bapk\b"), "apk"),
    (re.compile(r"\b(yum|dnf)\b"), "yum"),
    (re.compile(r"\bdocker\b"), "docker"),
]

def _classify_bash(cmd: str, node_key: str | None = None) -> tuple[str, str | None]:
    # 先现有 BLACKLIST_RES
    if node_key == "env_ready":
        for pat, name in ENV_READY_DENY_RES:
            if pat.search(cmd):
                return ("deny", f"blocked by policy: {name}")
    return ("allow", None)
```

`_pre_tool_use_hook`：`_classify_bash(cmd, os.environ.get("NODE_KEY"))`。

- [ ] **Step 4: 跑测试确认通过**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_run_one_bash_policy.py tests/test_run_one_node_prompt.py -q`
Expected: PASS

- [ ] **Step 5: Commit**（仅用户要求时）

```
fix(agent-runner): env_ready 禁止在 runner 里装依赖

runner 不是靶场，npm/pip/docker 只能写进 Dockerfile 由宿主机构建。
```

---

### Task 6: 插件文案与权威文档同步

**Files:**
- Modify: `plugins/vuln-verify-expert/agents/env-builder.md`
- Modify: `plugins/vuln-verify-expert/skills/run-project-env/SKILL.md`（文首加「Crucible 平台」专章，不要删本地 7 步）
- Modify: `plugins/vuln-verify-expert/skills/run-project-env/references/isolation.md`（平台：agent-runner ≠ 靶场）
- Modify: `docs/superpowers/specs/2026-08-14-lab-lifecycle-design.md` §2 创建者
- Modify: `docs/superpowers/specs/2026-08-12-platform-node-orchestration-design.md` §3.3
- Modify: `.claude/api-contract.md` rebuild 一行
- Modify: `docs/development-guide.md` §3 已完成清单加一条配方 MinIO
- Modify: `docs/superpowers/specs/2026-08-14-env-ready-recipe-loop-design.md`（状态已是已评审则不动）

**Interfaces:** 无代码接口。一致性：创建者先 MinIO 再 AI；失败立即回喂；rebuild 可从 MinIO 恢复。

- [ ] **Step 1: 改插件**

`env-builder.md` 关键约束增加：

```
- agent-runner 不是靶场。禁止 npm/pip/apt/docker。依赖写进 Dockerfile 的 RUN 或 compose 的 image。
- 平台会优先用 MinIO 已验证配方启动；只有未命中或启动失败才会进本 agent。
```

`SKILL.md` 在「标准工作流程」之前插入：

```
## Crucible 平台

由 env-builder 调用时：只做第 1、3、4 步（分析 / 现成镜像判断 / 写配方）以及根据 `previous_error` 改配方。
第 2 步 web 门禁由节点 profile 完成。第 5–7 步（compose up、排障循环驱动、对人写 RUN_ENV）由平台 worker 执行。
不要在 runner 里安装依赖或 docker compose。
```

`isolation.md` 增加：在 Crucible 平台，`npm install` 只允许出现在 Dockerfile 构建层，不允许在 agent-runner 容器的 Bash 里执行。

- [ ] **Step 2: 改权威文档**

lab-lifecycle §2 创建者改为：先 `download_recipe`；命中则改口 + 一次 up；失败或未命中再 AI 循环；成功 `upload_recipe`。

编排 §3.3 循环前增加配方命中短路；注明构建/探活失败立即回喂，docker 不可用则节点失败。

api-contract rebuild：`本地缺 compose 时先从 MinIO 拉配方，仍无则 400。`

development-guide §3 表格追加一行「靶场配方 MinIO 复用」。

- [ ] **Step 3: 回归测试**

Run: `d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest tests/test_lab_recipe_store.py tests/test_recipe_port_rewrite.py tests/test_env_ready_lab_reuse.py tests/test_lab_api.py tests/test_run_one_bash_policy.py tests/test_node_env_ready.py tests/test_lab_acquire.py -q`
Expected: PASS

- [ ] **Step 4: Commit**（仅用户要求时）

```
docs: 同步配方闭环与平台 skill 切分

实现改了创建者路径和 rebuild，权威文档与插件必须同频。
```

---

## Spec coverage

| Spec | Task |
|---|---|
| MinIO bucket/key/只存文件 | 1 |
| download 未命中/异常 → None | 1 |
| upload 失败不阻断 ready | 1 |
| 命中改宿主口 | 2、3 |
| 命中 up 成功跳过 AI、reused | 3 |
| up 失败立即 AI，不二次同一稿 | 3 |
| docker 不可用 fail-fast | 2、3 |
| Mock 不碰配方 | 3 |
| rebuild MinIO 回源 | 4 |
| env_ready Bash deny | 5 |
| skill / env-builder / 权威文档 | 6 |

无 TBD。类型名：`download_recipe` 返回 `dict | None`（不用单独 dataclass，避免扩散）；key 函数名 `recipe_object_key`。
