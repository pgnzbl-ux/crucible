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
        from app.contexts.project.models import Project  # noqa: F401
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
    assert created.id
    assert created.name == "x"

    ok = await svc.delete_project(created.id, owner_id="u1")
    assert ok is True
    assert await svc.get_project(created.id, owner_id="u1") is None
