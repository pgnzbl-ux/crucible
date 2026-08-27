"""Project service 测试(upsert 复用 + CRUD)。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        # 触发全部 model 注册(FK 链完整)
        from app.contexts.identity.models import User  # noqa: F401
        from app.contexts.lab.models import Lab  # noqa: F401
        from app.contexts.project.models import Project, SourceArtifact  # noqa: F401
        from app.contexts.task.models import Task, TaskRun, NodeRun, AgentEvent  # noqa: F401
        from app.contexts.report.models import Report  # noqa: F401
        from app.contexts.settings.models import LlmProvider  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_upsert_by_git_url_reuses_existing(session):
    """同 git_url + owner 第二次 upsert 复用,不新建。"""
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    p1 = await svc.upsert_by_git_url(
        git_url="https://github.com/a/b.git", owner_id="u1", name="b"
    )
    p2 = await svc.upsert_by_git_url(
        git_url="https://github.com/a/b.git", owner_id="u1"
    )
    assert p1.id == p2.id, "同 git_url 复用同一 project"


@pytest.mark.asyncio
async def test_upsert_strips_git_suffix_as_same_project(session):
    """带不带 .git 视为同一仓库，不建第二行。"""
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    p1 = await svc.upsert_by_git_url(
        git_url="https://github.com/siteboon/claudecodeui.git", owner_id="u1"
    )
    p2 = await svc.upsert_by_git_url(
        git_url="https://github.com/siteboon/claudecodeui", owner_id="u1"
    )
    assert p1.id == p2.id
    assert p1.git_url == p2.git_url
    assert not p2.git_url.endswith(".git")


@pytest.mark.asyncio
async def test_upsert_name_fallback_from_url(session):
    """未提供 name 时,从 git_url 末段推断。"""
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    p = await svc.upsert_by_git_url(
        git_url="https://github.com/acme/awesome-app.git", owner_id="u1"
    )
    assert p.name == "awesome-app"


@pytest.mark.asyncio
async def test_upsert_by_git_url_rejects_name_used_by_upload(session):
    """Git 自动建项与已登记上传项目不能同名。"""
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_cache import MemorySourceStore
    from app.shared.exceptions import ConflictError

    svc = ProjectService(ProjectRepository(session))
    store = MemorySourceStore()
    await svc.ingest_uploaded_source(
        owner_id="u1",
        filename="demo.zip",
        data=_zip_bytes({"awesome-app/main.py": "print(1)\n"}),
        name="awesome-app",
        store=store,
    )
    with pytest.raises(ConflictError, match="项目名称已存在"):
        await svc.upsert_by_git_url(
            git_url="https://github.com/acme/awesome-app.git",
            owner_id="u1",
        )


@pytest.mark.asyncio
async def test_update_profile_backfills(session):
    """update_profile 回填画像字段(供编排器节点 1 调用)。"""
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    p = await svc.upsert_by_git_url(
        git_url="https://github.com/a/b.git", owner_id="u1"
    )
    await svc.update_profile(p.id, language="python", framework="fastapi", is_web=True)

    fetched = await svc.get_project(p.id, owner_id="u1")
    assert fetched.detected_language == "python"
    assert fetched.detected_framework == "fastapi"
    assert fetched.is_web is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "language,framework,want_lang,want_fw",
    [
        ("python", "fastapi", "python", "fastapi"),
        ("x" * 80, "y" * 150, "x" * 50, "y" * 100),
        (
            ["PHP", "JavaScript", "HTML"],
            ["zentao", "jquery"],
            "PHP, JavaScript, HTML",
            "zentao, jquery",
        ),
    ],
)
async def test_update_profile_snapshot_fits_columns(
    session, language, framework, want_lang, want_fw
):
    """列表快照必须能进 detected_language(50)/detected_framework(100)，否则 PG 截断失败会卡死节点。"""
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    p = await svc.upsert_by_git_url(
        git_url="https://github.com/easysoft/zentaopms.git", owner_id="u1"
    )
    await svc.update_profile(p.id, language=language, framework=framework, is_web=True)

    fetched = await svc.get_project(p.id, owner_id="u1")
    assert fetched.detected_language == want_lang
    assert fetched.detected_framework == want_fw
    assert len(fetched.detected_language or "") <= 50
    assert len(fetched.detected_framework or "") <= 100


@pytest.mark.asyncio
async def test_get_update_delete_require_owner(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.schemas import ProjectUpdateRequest
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    p = await svc.upsert_by_git_url(
        git_url="https://github.com/a/b.git", owner_id="u1"
    )
    assert await svc.get_project(p.id, owner_id="u1") is not None
    assert await svc.get_project(p.id, owner_id="u2") is None
    assert await svc.update_project(
        p.id, ProjectUpdateRequest(name="stolen"), owner_id="u2"
    ) is None
    assert await svc.delete_project(p.id, owner_id="u2") is False
    assert await svc.get_project(p.id, owner_id="u1") is not None
    assert await svc.delete_project(p.id, owner_id="u1") is True


@pytest.mark.asyncio
async def test_upsert_strips_https_userinfo(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    p = await svc.upsert_by_git_url(
        git_url="https://user:ghp_secret@github.com/acme/app.git",
        owner_id="u1",
    )
    assert "ghp_secret" not in p.git_url
    assert p.git_url == "https://github.com/acme/app"


@pytest.mark.asyncio
async def test_create_and_delete(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.schemas import ProjectCreateRequest
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    created = await svc.create_project(
        ProjectCreateRequest(name="x", git_url="https://github.com/a/c.git"),
        owner_id="u1",
    )
    assert created.source_type == "git"
    assert created.git_url == "https://github.com/a/c"

    ok = await svc.delete_project(created.id, owner_id="u1")
    assert ok is True
    assert await svc.get_project(created.id, owner_id="u1") is None


@pytest.mark.asyncio
async def test_create_project_rejects_invalid_git_url(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.schemas import ProjectCreateRequest
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    with pytest.raises(ValueError, match="Git"):
        await svc.create_project(
            ProjectCreateRequest(name="bad", git_url="not-a-git-url"),
            owner_id="u1",
        )


@pytest.mark.asyncio
async def test_create_project_rejects_duplicate_name(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.schemas import ProjectCreateRequest
    from app.contexts.project.service import ProjectService
    from app.shared.exceptions import ConflictError

    svc = ProjectService(ProjectRepository(session))
    await svc.create_project(
        ProjectCreateRequest(name="same", git_url="https://github.com/a/one.git"),
        owner_id="u1",
    )
    with pytest.raises(ConflictError, match="项目名称已存在"):
        await svc.create_project(
            ProjectCreateRequest(name="same", git_url="https://github.com/a/two.git"),
            owner_id="u1",
        )


def _zip_bytes(files: dict[str, str]) -> bytes:
    import io
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_ingest_upload_rejects_duplicate_name(session):
    from sqlalchemy import select

    from app.contexts.project.models import Project
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_cache import MemorySourceStore
    from app.shared.exceptions import ConflictError

    svc = ProjectService(ProjectRepository(session))
    store = MemorySourceStore()
    data = _zip_bytes({"demo/app.py": "print(1)\n"})
    await svc.ingest_uploaded_source(
        owner_id="u1",
        filename="demo.zip",
        data=data,
        name="demo-app",
        store=store,
    )
    with pytest.raises(ConflictError, match="项目名称已存在"):
        await svc.ingest_uploaded_source(
            owner_id="u1",
            filename="other.zip",
            data=_zip_bytes({"other/app.py": "print(2)\n"}),
            name="demo-app",
            store=store,
        )
    rows = list((await session.execute(select(Project))).scalars().all())
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_ingest_upload_allows_same_content_different_names(session):
    from sqlalchemy import select

    from app.contexts.project.models import Project, SourceArtifact
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_cache import MemorySourceStore
    from app.contexts.task.models import Task

    svc = ProjectService(ProjectRepository(session))
    store = MemorySourceStore()
    data = _zip_bytes({"demo/app.py": "print(1)\n"})
    p1, r1 = await svc.ingest_uploaded_source(
        owner_id="u1", filename="demo.zip", data=data, name="alpha", store=store,
    )
    p2, r2 = await svc.ingest_uploaded_source(
        owner_id="u1", filename="demo.zip", data=data, name="beta", store=store,
    )
    assert p1.id != p2.id
    assert r1.object_key != r2.object_key
    assert r1.object_key.endswith("/original.tar.gz")
    assert p1.id in r1.object_key
    assert p1.git_url == f"upload://local/{p1.id}"
    projects = list((await session.execute(select(Project))).scalars().all())
    assert len(projects) == 2
    artifacts = list((await session.execute(select(SourceArtifact))).scalars().all())
    assert len(artifacts) == 2
    tasks = list((await session.execute(select(Task))).scalars().all())
    assert tasks == []
    assert store.get_bytes(r1.object_key)
    assert store.get_bytes(r2.object_key)


def _artifact_result(*, ref_type: str, ref_name: str, sha: str):
    from app.contexts.project.source_acquire import SourceAcquireResult

    return SourceAcquireResult(
        ok=True,
        origin="git",
        git_url_normalized="https://github.com/siteboon/claudecodeui",
        git_host="github.com",
        project_key="siteboon/claudecodeui",
        repo_dirname="claudecodeui",
        ref_type=ref_type,
        ref_name=ref_name,
        commit_sha=sha,
        object_key=f"source/siteboon/claudecodeui/{sha}.tar.gz",
        object_url=f"http://localhost:9000/crucible-durable/source/{sha}.tar.gz",
    )


@pytest.mark.asyncio
async def test_list_projects_uses_default_ref_when_no_artifacts(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.schemas import ProjectCreateRequest
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    await svc.create_project(
        ProjectCreateRequest(
            name="禅道",
            git_url="https://github.com/easysoft/zentaopms.git",
            default_ref="zentaopms_22.4_20260730",
        ),
        owner_id="u1",
    )
    listed = await svc.list_projects("u1")
    assert listed.total == 1
    refs = [(r.ref_type, r.ref_name) for r in listed.items[0].source_refs]
    assert refs == [("tag", "zentaopms_22.4_20260730")]


@pytest.mark.asyncio
async def test_list_projects_source_refs_from_cached_artifacts(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    await svc.upsert_by_git_url(
        git_url="https://github.com/siteboon/claudecodeui.git",
        owner_id="u1",
        name="claudecodeui",
    )
    await svc.record_source_artifact(
        _artifact_result(
            ref_type="branch",
            ref_name="main",
            sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
        owner_id="u1",
    )
    await svc.record_source_artifact(
        _artifact_result(
            ref_type="tag",
            ref_name="v1.2.0",
            sha="bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        ),
        owner_id="u1",
    )
    listed = await svc.list_projects("u1")
    refs = [(r.ref_type, r.ref_name) for r in listed.items[0].source_refs]
    assert ("branch", "main") in refs
    assert ("tag", "v1.2.0") in refs


@pytest.mark.asyncio
async def test_list_projects_upload_has_empty_source_refs(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_cache import MemorySourceStore

    svc = ProjectService(ProjectRepository(session))
    await svc.ingest_uploaded_source(
        owner_id="u1",
        filename="demo.zip",
        data=_zip_bytes({"demo/app.py": "print(1)\n"}),
        name="local-demo",
        store=MemorySourceStore(),
    )
    listed = await svc.list_projects("u1")
    assert listed.items[0].source_type == "local_upload"
    assert listed.items[0].source_refs == []


@pytest.mark.asyncio
async def test_create_project_persists_default_ref_type(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.schemas import ProjectCreateRequest
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    created = await svc.create_project(
        ProjectCreateRequest(
            name="feature-branch",
            git_url="https://github.com/acme/app.git",
            default_ref="release-2.0",
            default_ref_type="branch",
        ),
        owner_id="u1",
    )
    assert created.default_ref_type == "branch"
    refs = [(r.ref_type, r.ref_name) for r in created.source_refs]
    assert refs == [("branch", "release-2.0")]


@pytest.mark.asyncio
async def test_update_project_returns_artifact_source_refs(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.schemas import ProjectUpdateRequest
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    p = await svc.upsert_by_git_url(
        git_url="https://github.com/siteboon/claudecodeui.git",
        owner_id="u1",
        name="claudecodeui",
    )
    await svc.record_source_artifact(
        _artifact_result(
            ref_type="branch",
            ref_name="main",
            sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        ),
        owner_id="u1",
    )
    updated = await svc.update_project(
        p.id, ProjectUpdateRequest(description="updated"), owner_id="u1"
    )
    assert updated is not None
    refs = [(r.ref_type, r.ref_name) for r in updated.source_refs]
    assert ("branch", "main") in refs


@pytest.mark.asyncio
async def test_update_project_fields(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.schemas import ProjectCreateRequest, ProjectUpdateRequest
    from app.contexts.project.service import ProjectService

    svc = ProjectService(ProjectRepository(session))
    created = await svc.create_project(
        ProjectCreateRequest(
            name="old-name",
            git_url="https://github.com/acme/app.git",
            default_ref="main",
            default_ref_type="branch",
            description="old",
        ),
        owner_id="u1",
    )
    updated = await svc.update_project(
        created.id,
        ProjectUpdateRequest(
            name="new-name",
            default_ref="v2.0.0",
            default_ref_type="tag",
            description="备注",
        ),
        owner_id="u1",
    )
    assert updated is not None
    assert updated.name == "new-name"
    assert updated.default_ref == "v2.0.0"
    assert updated.default_ref_type == "tag"
    assert updated.description == "备注"
    assert updated.git_url == created.git_url


@pytest.mark.asyncio
async def test_update_project_rejects_duplicate_name(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.schemas import ProjectCreateRequest, ProjectUpdateRequest
    from app.contexts.project.service import ProjectService
    from app.shared.exceptions import ConflictError

    svc = ProjectService(ProjectRepository(session))
    await svc.create_project(
        ProjectCreateRequest(name="taken", git_url="https://github.com/a/one.git"),
        owner_id="u1",
    )
    other = await svc.create_project(
        ProjectCreateRequest(name="free", git_url="https://github.com/a/two.git"),
        owner_id="u1",
    )
    with pytest.raises(ConflictError, match="项目名称已存在"):
        await svc.update_project(
            other.id, ProjectUpdateRequest(name="taken"), owner_id="u1"
        )


@pytest.mark.asyncio
async def test_delete_upload_artifact_removes_original_package(session):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_cache import MemorySourceStore

    svc = ProjectService(ProjectRepository(session))
    store = MemorySourceStore()
    project, result = await svc.ingest_uploaded_source(
        owner_id="u1",
        filename="demo.zip",
        data=_zip_bytes({"demo/app.py": "print(1)\n"}),
        name="local-demo",
        store=store,
    )
    assert store.get_bytes(result.object_key)
    items = await svc.list_artifacts(project.id, "u1")
    assert items is not None and len(items) == 1

    assert await svc.delete_artifact(project.id, items[0].id, "u1", store=store) is True
    assert await svc.list_artifacts(project.id, "u1") == []
    assert store.get_bytes(result.object_key) is None
    assert await svc.get_project(project.id, "u1") is not None
