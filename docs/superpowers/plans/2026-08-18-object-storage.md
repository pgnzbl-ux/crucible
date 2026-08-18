# 平台对象存储（3 桶 + kind）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 平台文件只经 `shared/object_store.py` 写入 3 个物理桶；节点失败时上传 `node_run` 包并建索引。

**Architecture:** kind 注册表决定桶与 key；Context 只 pack/unpack 并调 object_store。开发阶段干净切换：旧 4 桶名退出代码，不做双读。`node_run` 的 put + `node_run_failures` 由 TaskService 一次完成。

**Tech Stack:** Python 3.11 / FastAPI / SQLAlchemy 2.0 / MinIO Python SDK / pytest

**Spec:** `docs/superpowers/specs/2026-08-18-object-storage-design.md`（v1.1 已确认，干净切换）

## Global Constraints

- 提交：`type(scope):` 英文，描述主体简体中文；**用户未明确要求则跳过每个 Task 的 Commit 步**
- Windows 提交用 `git commit -m "subject" -m "body"`，不用 HEREDOC
- 后端测试：`d:\codeproject\Crucible\backend\.venv\Scripts\python.exe -m pytest <args> -v`（cwd `backend`）
- 物理桶只有 `crucible-durable` / `crucible-task` / `crucible-public`；禁止 Context 里 `Minio()` / `make_bucket`
- 禁止 kind `secret`/`credential`/`env`；禁止把 `.secrets/` 打进对象
- 不做旧桶双读、不做搬迁任务、v1 无对象管理 HTTP、avatar 不上传
- 不改编排出口、env_ready 5 轮、探活 90s
- 唯一 Alembic 基线：新表靠 ORM `create_all` / `init_db`，**不**新增第二份 migration 文件（`test_single_alembic_baseline` 要求恰好 1 个版本）
- 叠加上面改，不要还原无关 diff

---

## File Structure

| 文件 | 改动 | 责任 |
|---|---|---|
| `backend/app/shared/object_store.py` | 新建 | 注册表、key、Memory/Minio 客户端、`ensure_buckets` |
| `backend/tests/test_object_store.py` | 新建 | 注册表 / Memory 往返 / 黑名单 / 路径安全 / createbuckets 清单 |
| `infrastructure/docker-compose.yml` | 改 | createbuckets 只 `mb` 3 桶 |
| `backend/app/contexts/project/source_cache.py` | 改 | 去掉 `Minio()`；pack 仍在此；读写走 object_store |
| `backend/app/contexts/project/source_acquire.py` | 改 | 新 key 含 `owner_id` |
| `backend/app/contexts/lab/recipe_store.py` | 改 | 去掉 `Minio()` / `make_bucket`；只读 durable |
| `backend/app/contexts/report/service.py` | 改 | 证据/报告 body 走 object_store |
| `backend/app/contexts/report/storage.py` | 删或抽空 | 不再握客户端 |
| `backend/app/contexts/agent/tasks.py` | 改 | 归档改 `ReportService.attach_evidence` |
| `backend/app/contexts/agent/node_failure.py` | 新建 | `classify_node_error` + 快照/打包 |
| `backend/app/contexts/task/models.py` | 改 | `NodeRunFailure` |
| `backend/app/contexts/task/service.py` | 改 | `record_node_run_failure` |
| `backend/app/contexts/agent/orchestrator.py` | 改 | 节点 failed 调 TaskService（Mock 跳过） |
| `backend/app/contexts/agent/nodes/env_ready.py` | 改 | 每轮失败立刻快照 attempt |
| 权威文档 | 改 | development-guide、api-contract、agent-workflow、backend.md、配方 spec 桶名 |

---

### Task 1: 注册表 + Memory store + createbuckets

**Files:**
- Create: `backend/app/shared/object_store.py`
- Create: `backend/tests/test_object_store.py`
- Modify: `infrastructure/docker-compose.yml`（createbuckets entrypoint）
- Modify: `backend/tests/test_config_ssot.py`

**Interfaces:**
- Produces:
  - `BUCKET_DURABLE = "crucible-durable"`
  - `BUCKET_TASK = "crucible-task"`
  - `BUCKET_PUBLIC = "crucible-public"`
  - `PHYSICAL_BUCKETS: tuple[str, ...] = (BUCKET_DURABLE, BUCKET_TASK, BUCKET_PUBLIC)`
  - `KIND_REGISTRY: dict[str, KindSpec]` 含 6 kind
  - `class ObjectRef`：`kind: str`, `bucket: str`, `key: str`
  - `class ObjectStoreError(Exception)`
  - `class UnknownKindError(ObjectStoreError)`
  - `class ForbiddenKindError(ObjectStoreError)`
  - `class UnsafeKeyError(ObjectStoreError)`
  - `class ObjectNotFoundError(ObjectStoreError)`
  - `class KindNotWritableError(ObjectStoreError)`
  - `def build_ref(kind: str, owner_id: str, **parts) -> ObjectRef`
  - `class MemoryObjectStore`：`put` / `get` / `exists` / `delete` / `presign` / `put_file` / `get_file`
  - `put(kind, owner_id, data: bytes, *, content_type: str, **parts) -> ObjectRef`
  - `presign`：注册表 `presign=False` 的 kind 抛 `ObjectStoreError`（消息含 `不允许预签名`）
  - `avatar` put 抛 `KindNotWritableError`
  - `secret`/`credential`/`env` put 抛 `ForbiddenKindError`
  - `file_name` 含 `/` 或 `..` → `UnsafeKeyError`

- [ ] **Step 1: 写失败测试** `backend/tests/test_object_store.py`

```python
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

COMPOSE = Path(__file__).resolve().parents[2] / "infrastructure" / "docker-compose.yml"


def test_physical_buckets_are_three():
    from app.shared.object_store import PHYSICAL_BUCKETS

    assert PHYSICAL_BUCKETS == ("crucible-durable", "crucible-task", "crucible-public")


def test_kind_registry_has_six_kinds():
    from app.shared.object_store import KIND_REGISTRY

    assert set(KIND_REGISTRY) == {
        "source", "recipe", "evidence", "report", "node_run", "avatar",
    }
    assert KIND_REGISTRY["source"].bucket == "crucible-durable"
    assert KIND_REGISTRY["recipe"].bucket == "crucible-durable"
    assert KIND_REGISTRY["evidence"].bucket == "crucible-task"
    assert KIND_REGISTRY["report"].bucket == "crucible-task"
    assert KIND_REGISTRY["node_run"].bucket == "crucible-task"
    assert KIND_REGISTRY["avatar"].bucket == "crucible-public"
    assert KIND_REGISTRY["avatar"].writable is False
    assert KIND_REGISTRY["source"].presign is False
    assert KIND_REGISTRY["evidence"].presign is True


def test_createbuckets_only_mentions_three_current_buckets():
    text = COMPOSE.read_text(encoding="utf-8")
    assert "local/crucible-durable" in text
    assert "local/crucible-task" in text
    assert "local/crucible-public" in text
    for old in (
        "crucible-artifacts", "crucible-evidence", "crucible-source",
        "crucible-lab-recipe", "crucible-node-failure",
    ):
        assert f"local/{old}" not in text


def test_memory_put_get_roundtrip():
    from app.shared.object_store import MemoryObjectStore

    store = MemoryObjectStore()
    ref = store.put(
        "recipe", "u1", b"tar-bytes",
        content_type="application/gzip",
        project_id="p1",
        sha="a" * 40,
    )
    assert ref.bucket == "crucible-durable"
    assert ref.key == f"recipe/u1/p1/{'a' * 40}.tar.gz"
    assert store.get(ref) == b"tar-bytes"


def test_forbidden_kinds_rejected():
    from app.shared.object_store import ForbiddenKindError, MemoryObjectStore

    store = MemoryObjectStore()
    for kind in ("secret", "credential", "env"):
        try:
            store.put(kind, "u1", b"x", content_type="text/plain")
            raise AssertionError(f"{kind} 应被拒绝")
        except ForbiddenKindError:
            pass


def test_unknown_kind_rejected():
    from app.shared.object_store import MemoryObjectStore, UnknownKindError

    store = MemoryObjectStore()
    try:
        store.put("logs", "u1", b"x", content_type="text/plain")
        raise AssertionError("未知 kind 应被拒绝")
    except UnknownKindError:
        pass


def test_avatar_not_writable():
    from app.shared.object_store import KindNotWritableError, MemoryObjectStore

    store = MemoryObjectStore()
    try:
        store.put("avatar", "u1", b"img", content_type="image/png")
        raise AssertionError("avatar 不应写入")
    except KindNotWritableError:
        pass


def test_file_name_path_traversal_rejected():
    from app.shared.object_store import MemoryObjectStore, UnsafeKeyError

    store = MemoryObjectStore()
    try:
        store.put(
            "evidence", "u1", b"x",
            content_type="text/plain",
            task_id="t1",
            evidence_id="e1",
            file_name="../etc/passwd",
        )
        raise AssertionError("应拒绝 ..")
    except UnsafeKeyError:
        pass
```

- [ ] **Step 2: 跑测试确认失败**

Run: `backend\.venv\Scripts\python.exe -m pytest tests/test_object_store.py -v`
Expected: FAIL（模块不存在）

- [ ] **Step 3: 最小实现** `object_store.py` + 改 compose

`build_ref` 模板：

| kind | key |
|---|---|
| source | `source/{owner_id}/{git_host}/{project_key}/{sha}.tar.gz` |
| recipe | `recipe/{owner_id}/{project_id}/{sha}.tar.gz` |
| evidence | `evidence/{owner_id}/{task_id}/{evidence_id}/{file_name}` |
| report | `report/{owner_id}/{task_id}/{report_id}/body.json` |
| node_run | `node_run/{owner_id}/{task_id}/{run_id}/{node_key}.tar.gz` |
| avatar | `avatar/{owner_id}/profile` |

`project_key` 允许一个 `/`（`org/repo`），禁止 `..`。`file_name` 只留 basename。

compose `createbuckets` entrypoint 改为只 mb 三个现行桶。

`test_config_ssot.py` 的 `test_storage_buckets_are_platform_constants` 改为断言 `PHYSICAL_BUCKETS` 与 6 kind，不再 import `ARTIFACTS_BUCKET`。

- [ ] **Step 4: 跑测试确认通过**

Run: `backend\.venv\Scripts\python.exe -m pytest tests/test_object_store.py tests/test_config_ssot.py -v`
Expected: PASS

- [ ] **Step 5: Commit**（用户未要求则跳过）

---

### Task 2: MinioObjectStore + ensure_buckets

**Files:**
- Modify: `backend/app/shared/object_store.py`
- Modify: `backend/tests/test_object_store.py`

**Interfaces:**
- Produces: `class MinioObjectStore`（内部唯一 `Minio()`）；`ensure_buckets()` 只循环 `PHYSICAL_BUCKETS`；`get_object_store()` / `set_object_store_for_tests(store)`；`presign(ref, expires_seconds=3600)` 仅 `presign=True` 的 kind
- 业务 Context **禁止** `from minio import Minio`

- [ ] **Step 1: 写失败测试**

```python
def test_ensure_buckets_only_creates_physical_three():
    from app.shared.object_store import MinioObjectStore, PHYSICAL_BUCKETS

    created: list[str] = []

    class FakeClient:
        def bucket_exists(self, name):
            return False
        def make_bucket(self, name):
            created.append(name)

    store = MinioObjectStore.__new__(MinioObjectStore)
    store._client = FakeClient()
    store.ensure_buckets()
    assert created == list(PHYSICAL_BUCKETS)


def test_source_presign_rejected():
    from app.shared.object_store import MemoryObjectStore, ObjectStoreError, build_ref

    store = MemoryObjectStore()
    ref = build_ref("source", "u1", git_host="github.com", project_key="a/b", sha="c" * 40)
    try:
        store.presign(ref)
        raise AssertionError("source 不应预签名")
    except ObjectStoreError as e:
        assert "不允许预签名" in str(e)
```

- [ ] **Step 2: 跑测确认失败** → Step 3 实现 `MinioObjectStore`（`put_object`/`get_object`/`presigned_get_object`/`remove_object`/`fput`/`fget`）→ Step 4 PASS → Commit 跳过

实现要点：`NoSuchKey`/`NoSuchBucket` → `ObjectNotFoundError`。`ensure_buckets` 禁止给 kind 名建桶。

---

### Task 3: 源码写入切到 durable + owner key

**Files:**
- Modify: `backend/app/contexts/project/source_cache.py`
- Modify: `backend/app/contexts/project/source_acquire.py`（`acquire_source(..., owner_id=...)`，`_upload_cache` 用新 key）
- Modify: `backend/app/contexts/agent/nodes/source.py`（把 `owner_id` 传给 `acquire_source`）
- Modify: `backend/app/contexts/project/service.py`（`record_source_artifact` 的 bucket 用 `KIND_REGISTRY["source"].bucket`）
- Modify: `backend/app/contexts/project/models.py`（`SourceArtifact.bucket` default 改为 `crucible-durable`）
- Modify: `backend/tests/test_source_cache.py`、`backend/tests/test_source_acquire.py`、`backend/tests/test_source_artifact.py`

**Interfaces:**
- `source_object_key(owner_id, git_host, project_key, commit_sha) -> str` 等于 `build_ref("source", ...).key`
- `SOURCE_BUCKET` 改为从注册表取（值 `crucible-durable`），禁止再出现 `crucible-source` 字符串（测试 fixture 的旧 URL 改成 durable）
- `MinioSourceStore` 无 `Minio()`：`upload`/`download` 调 `get_object_store()`
- 下载按调用方给的 `object_key` + kind 桶（库里旧 key 未命中则 `FileNotFoundError`，acquire 回退 clone——现有行为）

- [ ] **Step 1: 改 `test_source_cache.py` 为 owner key + durable 桶，确认失败**
- [ ] **Step 2–4: 改实现与 acquire 测试中的 `object_url`/`object_key`；`grep Minio(` 在 source_cache 为零**
- [ ] **Step 5: Commit 跳过**

Run: `backend\.venv\Scripts\python.exe -m pytest tests/test_source_cache.py tests/test_source_acquire.py tests/test_source_artifact.py -v`

---

### Task 4: 配方只走 durable（无双读）

**Files:**
- Modify: `backend/app/contexts/lab/recipe_store.py`
- Modify: `backend/tests/test_lab_recipe_store.py`
- Modify: `docs/superpowers/specs/2026-08-14-env-ready-recipe-loop-design.md`（Bucket 行改为 `crucible-durable`）

**Interfaces:**
- `RECIPE_BUCKET == "crucible-durable"`
- `recipe_object_key` 形状不变：`recipe/{owner}/{project}/{sha}.tar.gz`
- `MinioRecipeStore` 无 `ensure_recipe_bucket` / `Minio()`
- 未命中 → `FileNotFoundError`（LabService 已当 None）

- [ ] 改测试断言桶名 → 实现去掉懒建桶 → `pytest tests/test_lab_recipe_store.py tests/test_env_ready_lab_reuse.py tests/test_lab_api.py -v`

---

### Task 5: 证据/报告走 object_store；agent 不再 import report.storage

**Files:**
- Modify: `backend/app/contexts/report/service.py`
- Modify: `backend/app/contexts/report/models.py`（Evidence.bucket default `crucible-task`）
- Delete or gut: `backend/app/contexts/report/storage.py`
- Modify: `backend/app/contexts/agent/tasks.py`（`_archive_artifacts` / `_scan_and_upload` 改走 `ReportService.attach_evidence`）
- Modify: `backend/tests/test_report_list.py`
- Create: `backend/tests/test_object_store_boundaries.py`（agent/tasks.py 源码不含 `report.storage` / `Minio(`）

**Interfaces:**
- `attach_evidence`：先 `Evidence(id=uuid)` 再 `put("evidence", owner_id, data, task_id=, evidence_id=, file_name=)`，bucket=`crucible-task`，key 以 `evidence/{owner_id}/` 开头
- `generate_from_agent` 的 JSON body：`put("report", owner_id, ..., task_id=, report_id=)`，失败仍不阻断报告行
- `_to_evidence_detail` 预签名：`get_object_store().presign(ObjectRef(kind="evidence", bucket=ev.bucket, key=ev.object_key))`；若 kind 对不上（旧数据）用 `presign_raw` 仅当 bucket 在 `PHYSICAL_BUCKETS`，否则 URL 空。开发阶段旧桶直接失败即可（列表不阻塞）
- `_scan_and_upload` 删除；worker 对每个文件调 `ReportService.attach_evidence(..., owner_id=report.owner_id)`

- [ ] **失败测试**

```python
def test_attach_evidence_writes_task_bucket_and_owner_key(session):
    # Memory store + attach → evidence.bucket == crucible-task
    # evidence.object_key.startswith("evidence/u1/")

def test_agent_tasks_does_not_import_report_storage():
    text = Path("app/contexts/agent/tasks.py").read_text(encoding="utf-8")
    assert "from app.contexts.report import storage" not in text
    assert "app.contexts.report.storage" not in text
```

`test_attach_evidence_rejects_unknown_kind` 改为 patch `app.shared.object_store.get_object_store` 或注入 Memory store，确认未 put。

- [ ] grep 全 backend `Minio(` 只剩 `object_store.py`
- [ ] `pytest tests/test_report_list.py tests/test_object_store_boundaries.py -v`

---

### Task 6: `classify_node_error` + 打包（不含上传）

**Files:**
- Create: `backend/app/contexts/agent/node_failure.py`
- Create: `backend/tests/test_node_failure.py`

**Interfaces:**
- `ERROR_CLASSES` 12 个：`recipe_validation` `port_conflict` `compose_up.copy` `compose_up.transfer` `compose_up.build` `compose_up.runtime` `compose_up.policy` `health_check` `runner.no_submit` `runner.timeout` `docker.unavailable` `unknown`
- `classify_node_error(*, failed_stage: str | None, error_text: str) -> str`
- `snapshot_attempt(host_workdir, node_key, attempt, *, previous_error, platform_error, submit=None) -> None` 写到 `{host_workdir}/.node-failure/{node_key}/attempts/{N}/`；拷贝本轮 `.claude/**/*.jsonl`（单文件 >5MB 截尾并写 `session_truncated`）；env_ready 再拷 `.vuln-env`（跳过 `.secrets`）
- `pack_node_run_bundle(host_workdir, node_key, manifest: dict) -> bytes`：tar.gz 含 `manifest.json` + `attempts/`；包内文本对 `ANTHROPIC_` / `sk-` 掩码
- 表格驱动分类（必须覆盖）：

| failed_stage / 文本 | class |
|---|---|
| recipe_validation | recipe_validation |
| port_conflict | port_conflict |
| compose_up + `COPY` | compose_up.copy |
| compose_up + `Could not transfer` | compose_up.transfer |
| compose_up + `failed to build` | compose_up.build |
| compose_up + `policy` | compose_up.policy |
| compose_up 其它 | compose_up.runtime |
| health_check | health_check |
| 未产出 submit_result / `.node_output.json` | runner.no_submit |
| timeout / Timeout | runner.timeout |
| docker daemon / Cannot connect to Docker | docker.unavailable |
| 空 | unknown |

- [ ] 另测：两轮 snapshot 后 pack，`attempts/1/.vuln-env` 不被第 2 轮覆盖；包内无 `.secrets/`

---

### Task 7: `node_run_failures` + TaskService.record

**Files:**
- Modify: `backend/app/contexts/task/models.py`
- Modify: `backend/app/shared/models.py`（已 import task.models，确认 `NodeRunFailure` 被 import）
- Modify: `backend/app/contexts/task/service.py`
- Modify: `backend/app/contexts/task/repository.py`（hard delete 时先删 `node_run_failures`）
- Modify: `backend/tests/test_schema_baseline.py`（`create_all` 后表存在；仍只有 1 个 alembic 文件）
- Create: `backend/tests/test_node_run_failure_index.py`

**Interfaces:**
- 表 `node_run_failures` 列：`owner_id, task_id, run_id, node_run_id, node_key, error_class, failed_stage, language, attempt_count, bundle_key, bucket`；unique `(run_id, node_key)`
- `TaskService.record_node_run_failure(*, owner_id, task_id, run_id, node_run_id, node_key, error_class, failed_stage, language, attempt_count, bundle: bytes) -> None`
  - `put("node_run", owner_id, bundle, content_type="application/gzip", task_id=, run_id=, node_key=)`
  - 写索引行 `bundle_key=ref.key` `bucket=ref.bucket`
  - put 抛错：记 warning，**不**改 NodeRun.status / error_message，不抛给调用方
- v1 无 HTTP

- [ ] 测试：Memory store put 一次 + 索引有 bundle_key；store.put 抛错后节点 error_message 不变（由调用测试传入假 nr）

---

### Task 8: 编排采集：最终 failed 才上传；Mock 不传

**Files:**
- Modify: `backend/app/contexts/agent/orchestrator.py`
- Modify: `backend/app/contexts/agent/nodes/env_ready.py`（每轮 `continue` 前 `snapshot_attempt`）
- Modify: `backend/app/contexts/agent/ai_runner.py` 或各 AI 节点：非 env_ready 的 AI 失败也 snapshot（至少 jsonl + platform_error）
- Modify: `backend/tests/test_orchestrator.py`

**Interfaces:**
- 节点 `except` 在 `nr.status = "failed"` **之后**、`return` 之前：若 `claude_agent_sdk_enabled` 则 `pack` + `TaskService.record_node_run_failure`
- `needs_review` / `cancelled` / `completed` / `skipped` 不调用
- SDK 关闭：不 pack、不 put（可用 patch `get_object_store` 断言未 put）
- 上传失败不改变已写入的 `nr.error_message`
- report 节点失败但任务因 B/D 保留 completed/needs_review：report NodeRun 仍是 failed → **要**上传该节点包

最小 orchestrator 测试：Mock 设置 `claude_agent_sdk_enabled=False`，节点抛错，Memory store 仍为空。另测 enabled=True 时 put 被调用一次。

---

### Task 9: 权威文档

**Files:**
- `docs/development-guide.md`（树状 `storage.py` 改为 `shared/object_store.py`；配置表 bucket 写死 3 名）
- `.claude/api-contract.md` 证据段：桶 `crucible-task`，key `evidence/{owner_id}/...`
- `docs/agent-workflow.md`：失败节点有 `node_run` 包
- `.claude/rules/backend.md`：源码桶 `crucible-durable`
- `docs/superpowers/specs/2026-08-12-platform-node-orchestration-design.md` 源码桶/key 一句
- `backend/app/core/config.py` 注释改指向 `shared/object_store.py`
- `backend/pyproject.toml` 注释

不改前端路由。无新 API。

- [ ] 文档与 spec §10 清单逐项核对；`rg "crucible-source|crucible-lab-recipe|crucible-artifacts|crucible-evidence" --glob '!docs/superpowers/specs/2026-08-18-object-storage-design.md' --glob '!**/plans/**'` 业务代码与现行权威文档零命中（spec §1「问题」表格可保留旧名作为现状描述）

---

## Spec coverage（自检）

| spec | task |
|---|---|
| 3 桶 + createbuckets 只这 3 个 | 1 |
| 唯一客户端 / 禁 Context Minio | 2–5 |
| 6 kind + 黑名单 + avatar 不写 | 1 |
| 干净切换无双读 | 3–4 |
| 证据/报告新 key + 预签名 | 5 |
| agent 不 import report.storage | 5 |
| node_run 包布局/分类/索引/Mock/上传失败 | 6–8 |
| 文档 | 9 |
| 探活 90s / 编排出口 | 不改 |
