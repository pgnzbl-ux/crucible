"""source_artifacts 表：按 owner + host + project_key + ref 查找。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.shared.base import Base


@pytest_asyncio.fixture
async def session_factory():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        from app.contexts.identity.models import User  # noqa: F401
        from app.contexts.lab.models import Lab  # noqa: F401
        from app.contexts.project.models import Project, SourceArtifact  # noqa: F401
        from app.contexts.task.models import Task, TaskRun, NodeRun, AgentEvent  # noqa: F401
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest.mark.asyncio
async def test_find_and_upsert_source_artifact(session_factory):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_acquire import SourceAcquireResult

    async with session_factory() as session:
        svc = ProjectService(ProjectRepository(session))
        url = "https://github.com/siteboon/claudecodeui.git"
        assert await svc.find_cached_source(url, "main", owner_id="u1") is None

        result = SourceAcquireResult(
            ok=True,
            origin="git",
            git_url_normalized="https://github.com/siteboon/claudecodeui",
            git_host="github.com",
            project_key="siteboon/claudecodeui",
            repo_dirname="claudecodeui",
            ref_type="branch",
            ref_name="main",
            commit_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            object_key="source/siteboon/claudecodeui/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.tar.gz",
            object_url="http://localhost:9000/crucible-durable/source/siteboon/claudecodeui/aaa.tar.gz",
        )
        await svc.record_source_artifact(result, owner_id="u1")
        await session.commit()

        cached = await svc.find_cached_source(url, "main", owner_id="u1")
        assert cached is not None
        assert cached.project_key == "siteboon/claudecodeui"
        assert cached.repo_dirname == "claudecodeui"
        assert cached.ref_type == "branch"
        assert cached.ref_name == "main"
        assert cached.object_key == result.object_key

        result.commit_sha = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
        result.object_key = "source/siteboon/claudecodeui/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb.tar.gz"
        await svc.record_source_artifact(result, owner_id="u1")
        await session.commit()
        cached2 = await svc.find_cached_source(url, "main", owner_id="u1")
        assert cached2.commit_sha.startswith("bbbb")


@pytest.mark.asyncio
async def test_find_by_commit_prefix(session_factory):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_acquire import SourceAcquireResult

    sha = "cccccccccccccccccccccccccccccccccccccccc"
    async with session_factory() as session:
        svc = ProjectService(ProjectRepository(session))
        await svc.record_source_artifact(SourceAcquireResult(
            ok=True,
            git_url_normalized="https://github.com/siteboon/claudecodeui",
            git_host="github.com",
            project_key="siteboon/claudecodeui",
            repo_dirname="claudecodeui",
            ref_type="commit",
            ref_name=sha,
            commit_sha=sha,
            object_key=f"source/github.com/siteboon/claudecodeui/{sha}.tar.gz",
            object_url="http://localhost:9000/crucible-durable/x",
        ), owner_id="u1")
        await session.commit()
        cached = await svc.find_cached_source(
            "https://github.com/siteboon/claudecodeui.git", sha[:8], owner_id="u1"
        )
        assert cached is not None
        assert cached.commit_sha == sha


@pytest.mark.asyncio
async def test_list_artifacts_for_project(session_factory):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_acquire import SourceAcquireResult

    async with session_factory() as session:
        svc = ProjectService(ProjectRepository(session))
        project = await svc.upsert_by_git_url(
            git_url="https://github.com/siteboon/claudecodeui.git",
            owner_id="u1",
        )
        await svc.record_source_artifact(SourceAcquireResult(
            ok=True,
            git_url_normalized="https://github.com/siteboon/claudecodeui",
            git_host="github.com",
            project_key="siteboon/claudecodeui",
            repo_dirname="claudecodeui",
            ref_type="branch",
            ref_name="main",
            commit_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            object_key="source/github.com/siteboon/claudecodeui/aaa.tar.gz",
            object_url="http://localhost:9000/crucible-durable/aaa.tar.gz",
        ), owner_id="u1")
        await session.commit()

        items = await svc.list_artifacts(project.id, "u1")
        assert items is not None
        assert len(items) == 1
        assert items[0].project_key == "siteboon/claudecodeui"
        assert items[0].ref_name == "main"
        assert items[0].object_url.endswith("aaa.tar.gz")

        assert await svc.list_artifacts(project.id, "other") is None
        assert await svc.list_artifacts("missing", "u1") is None


@pytest.mark.asyncio
async def test_source_cache_not_shared_across_owners(session_factory):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_acquire import SourceAcquireResult

    async with session_factory() as session:
        svc = ProjectService(ProjectRepository(session))
        result = SourceAcquireResult(
            ok=True,
            git_url_normalized="https://github.com/acme/secret",
            git_host="github.com",
            project_key="acme/secret",
            repo_dirname="secret",
            ref_type="branch",
            ref_name="main",
            commit_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            object_key="source/github.com/acme/secret/aaa.tar.gz",
            object_url="http://localhost:9000/crucible-durable/aaa.tar.gz",
        )
        await svc.record_source_artifact(result, owner_id="owner-a")
        await session.commit()

        url = "https://github.com/acme/secret.git"
        assert await svc.find_cached_source(url, "main", owner_id="owner-a") is not None
        assert await svc.find_cached_source(url, "main", owner_id="owner-b") is None


@pytest.mark.asyncio
async def test_source_cache_not_shared_across_hosts(session_factory):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_acquire import SourceAcquireResult

    async with session_factory() as session:
        svc = ProjectService(ProjectRepository(session))
        await svc.record_source_artifact(SourceAcquireResult(
            ok=True,
            git_url_normalized="https://github.com/acme/app",
            git_host="github.com",
            project_key="acme/app",
            repo_dirname="app",
            ref_type="branch",
            ref_name="main",
            commit_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            object_key="source/github.com/acme/app/aaa.tar.gz",
            object_url="http://localhost:9000/crucible-durable/aaa.tar.gz",
        ), owner_id="u1")
        await session.commit()

        assert await svc.find_cached_source(
            "https://github.com/acme/app.git", "main", owner_id="u1"
        ) is not None
        assert await svc.find_cached_source(
            "https://gitlab.com/acme/app.git", "main", owner_id="u1"
        ) is None


def _artifact_result(**overrides):
    from app.contexts.project.source_acquire import SourceAcquireResult

    data = dict(
        ok=True,
        git_url_normalized="https://github.com/acme/app",
        git_host="github.com",
        project_key="acme/app",
        repo_dirname="app",
        ref_type="branch",
        ref_name="main",
        commit_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        object_key="source/github.com/acme/app/aaa.tar.gz",
        object_url="http://localhost:9000/crucible-durable/aaa.tar.gz",
    )
    data.update(overrides)
    return SourceAcquireResult(**data)


@pytest.mark.asyncio
async def test_profile_bound_to_sha_and_cleared_when_sha_changes(session_factory):
    """同 SHA 可复用画像；branch 推进后必须丢掉旧画像。"""
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    sha_a = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    sha_b = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    profile = {"is_web": True, "language": "python", "framework": "fastapi"}

    async with session_factory() as session:
        svc = ProjectService(ProjectRepository(session))
        await svc.record_source_artifact(_artifact_result(commit_sha=sha_a), owner_id="u1")
        await svc.save_source_profile(owner_id="u1", commit_sha=sha_a, profile=profile)
        await session.commit()

        hit = await svc.find_cached_profile(owner_id="u1", commit_sha=sha_a)
        assert hit == profile
        assert await svc.find_cached_profile(owner_id="u1", commit_sha=sha_b) is None
        assert await svc.find_cached_profile(owner_id="other", commit_sha=sha_a) is None

        await svc.record_source_artifact(_artifact_result(commit_sha=sha_b), owner_id="u1")
        await session.commit()
        assert await svc.find_cached_profile(owner_id="u1", commit_sha=sha_a) is None
        assert await svc.find_cached_profile(owner_id="u1", commit_sha=sha_b) is None


@pytest.mark.asyncio
async def test_same_sha_keeps_profile_on_reclone(session_factory):
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    sha = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    profile = {"is_web": False, "language": "python", "non_web_reason": "CLI"}

    async with session_factory() as session:
        svc = ProjectService(ProjectRepository(session))
        await svc.record_source_artifact(_artifact_result(commit_sha=sha), owner_id="u1")
        await svc.save_source_profile(owner_id="u1", commit_sha=sha, profile=profile)
        await svc.record_source_artifact(_artifact_result(commit_sha=sha), owner_id="u1")
        await session.commit()
        assert await svc.find_cached_profile(owner_id="u1", commit_sha=sha) == profile


@pytest.mark.asyncio
async def test_delete_artifact_removes_exclusive_object(session_factory):
    """独享 object_key：删行且删 MinIO。"""
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_cache import MemorySourceStore

    key = "source/github.com/acme/app/aaa.tar.gz"
    store = MemorySourceStore()
    store._data[key] = ("aaa", b"tar-bytes")

    async with session_factory() as session:
        svc = ProjectService(ProjectRepository(session))
        p = await svc.upsert_by_git_url(
            git_url="https://github.com/acme/app.git", owner_id="u1", name="app"
        )
        await svc.record_source_artifact(_artifact_result(), owner_id="u1")
        items = await svc.list_artifacts(p.id, "u1")
        assert items is not None and len(items) == 1

        assert await svc.delete_artifact(p.id, items[0].id, "u2", store=store) is False
        assert store.get_bytes(key) == b"tar-bytes"

        assert await svc.delete_artifact(p.id, items[0].id, "u1", store=store) is True
        assert await svc.list_artifacts(p.id, "u1") == []
        assert store.get_bytes(key) is None
        assert await svc.find_cached_source(
            "https://github.com/acme/app.git", "main", owner_id="u1"
        ) is None


@pytest.mark.asyncio
async def test_delete_artifact_keeps_shared_object_key(session_factory):
    """同一 object_key 被 branch/tag 两行共用时，只删本行、对象保留。"""
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_cache import MemorySourceStore

    key = "source/github.com/acme/app/aaa.tar.gz"
    store = MemorySourceStore()
    store._data[key] = ("aaa", b"tar-bytes")

    async with session_factory() as session:
        svc = ProjectService(ProjectRepository(session))
        p = await svc.upsert_by_git_url(
            git_url="https://github.com/acme/app.git", owner_id="u1", name="app"
        )
        await svc.record_source_artifact(_artifact_result(ref_type="branch", ref_name="main"), owner_id="u1")
        await svc.record_source_artifact(
            _artifact_result(ref_type="tag", ref_name="v1.0.0"), owner_id="u1"
        )
        items = await svc.list_artifacts(p.id, "u1")
        assert items is not None and len(items) == 2
        branch = next(i for i in items if i.ref_type == "branch")

        assert await svc.delete_artifact(p.id, branch.id, "u1", store=store) is True
        left = await svc.list_artifacts(p.id, "u1")
        assert left is not None
        assert [(i.ref_type, i.ref_name) for i in left] == [("tag", "v1.0.0")]
        assert store.get_bytes(key) == b"tar-bytes"


@pytest.mark.asyncio
async def test_delete_project_purges_exclusive_artifacts(session_factory):
    """删项目时清该仓库制品；独享对象从 MinIO 去掉。"""
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService
    from app.contexts.project.source_cache import MemorySourceStore

    key = "source/github.com/acme/app/aaa.tar.gz"
    store = MemorySourceStore()
    store._data[key] = ("aaa", b"tar-bytes")

    async with session_factory() as session:
        svc = ProjectService(ProjectRepository(session))
        p = await svc.upsert_by_git_url(
            git_url="https://github.com/acme/app.git", owner_id="u1", name="app"
        )
        await svc.record_source_artifact(_artifact_result(), owner_id="u1")
        assert await svc.delete_project(p.id, "u1", store=store) is True
        assert await svc.get_project(p.id, "u1") is None
        assert await svc.find_cached_source(
            "https://github.com/acme/app.git", "main", owner_id="u1"
        ) is None
        assert store.get_bytes(key) is None
