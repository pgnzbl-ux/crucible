"""节点 0 源码 + 节点 1 画像测试。"""
import sys
import os
from unittest.mock import AsyncMock, MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from app.contexts.agent.nodes.base import NodeContext
from app.contexts.agent.nodes.profile import ProfileNode, merge_profile
from app.contexts.agent.nodes.source import SourceNode
from app.contexts.project.source_acquire import SourceAcquireResult


def _ctx(tmp_path, **kwargs) -> NodeContext:
    return NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address=kwargs.get("project_address", "https://github.com/siteboon/claudecodeui.git"),
        project_ref=kwargs.get("project_ref", "main"),
        db_session=kwargs.get("db_session"),
    )


@pytest.mark.asyncio
async def test_source_node_lands_in_repo_dirname(tmp_path):
    dest = tmp_path / "claudecodeui"
    dest.mkdir()
    (dest / "README.md").write_text("# demo\n")

    result = SourceAcquireResult(
        ok=True,
        origin="git",
        git_url_original="https://github.com/siteboon/claudecodeui.git",
        git_url_normalized="https://github.com/siteboon/claudecodeui",
        project_key="siteboon/claudecodeui",
        git_host="github.com",
        repo_dirname="claudecodeui",
        ref_type="branch",
        ref_name="main",
        commit_sha="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        project_path=str(dest),
        top_level=["README.md"],
        file_count=1,
    )

    with patch(
        "app.contexts.project.source_acquire.acquire_source",
        return_value=result,
    ):
        out = await SourceNode().execute(_ctx(tmp_path))
    assert out["repo_dirname"] == "claudecodeui"
    assert out["project_path"] == str(dest)
    assert out["source_path"] == str(dest)
    assert out["workspace_path"] == "/workspace/claudecodeui"
    assert "README.md" in out["top_level"]
    assert not (tmp_path / "project").exists()


@pytest.mark.asyncio
async def test_source_node_fails_on_network_error(tmp_path):
    result = SourceAcquireResult(
        ok=False,
        error="源码克隆失败: 网络错误（无法解析主机）: Could not resolve host: github.com",
        repo_dirname="claudecodeui",
    )
    with patch(
        "app.contexts.project.source_acquire.acquire_source",
        return_value=result,
    ):
        with pytest.raises(RuntimeError, match="网络错误"):
            await SourceNode().execute(_ctx(tmp_path))


def test_profile_node_is_ai():
    assert ProfileNode().is_ai is True


def test_merge_profile_ai_wins_hints_fill_gaps():
    merged = merge_profile(
        {"is_web": True, "language": "python", "summary": "FastAPI 服务"},
        {
            "is_web": False,
            "language": "nodejs",
            "framework": "fastapi",
            "port": 8000,
            "has_dockerfile": True,
            "has_compose": False,
            "detected_services": [],
        },
    )
    assert merged["is_web"] is True
    assert merged["language"] == "python"
    assert merged["framework"] == "fastapi"
    assert merged["port"] == 8000
    assert merged["has_dockerfile"] is True
    assert merged["summary"] == "FastAPI 服务"


def test_merge_profile_rejects_string_is_web():
    """LLM 若提交 \"false\" 字符串，不得被 bool() 变成 True。"""
    merged = merge_profile(
        {"is_web": "false", "language": "python"},
        {"is_web": False, "language": "python"},
    )
    assert merged["is_web"] is False


def test_sanitize_profile_keeps_facts_drops_essay():
    from app.contexts.agent.nodes.profile import sanitize_profile

    out = sanitize_profile(
        {
            "is_web": True,
            "language": "nodejs",
            "framework": "express",
            "port": 3001,
            "detected_services": ["sqlite"],
            "start_command": "npm start",
            "summary": "CloudCLI v1.37.0 — 基于 Web 的长文介绍，不应作为节点结果落库。",
            "kill_chain": "不该出现",
        }
    )
    assert out == {
        "is_web": True,
        "language": "nodejs",
        "framework": "express",
        "port": 3001,
        "detected_services": ["sqlite"],
        "start_command": "npm start",
    }


@pytest.mark.asyncio
async def test_profile_node_sdk_off_uses_detector(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x","dependencies":{"express":"^4"}}')
    (tmp_path / ".env").write_text("PORT=3000\n")

    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"source": {"project_path": str(tmp_path), "repo_dirname": "x"}},
    )
    fake_settings = MagicMock(claude_agent_sdk_enabled=False)
    with patch("app.core.config.get_settings", return_value=fake_settings):
        out = await ProfileNode().execute(ctx)
    assert out["is_web"] is True
    assert out["language"] == "nodejs"
    assert out["framework"] == "express"
    assert out["port"] == 3000


@pytest.mark.asyncio
async def test_profile_node_sdk_on_skips_ai_when_confident(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={
            "source": {
                "project_path": str(tmp_path),
                "repo_dirname": "demo",
                "workspace_path": "/workspace/demo",
                "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            }
        },
    )
    fake_settings = MagicMock(claude_agent_sdk_enabled=True)
    with (
        patch("app.core.config.get_settings", return_value=fake_settings),
        patch(
            "app.contexts.agent.ai_runner.run_ai_node",
            new_callable=AsyncMock,
        ) as mocked,
    ):
        out = await ProfileNode().execute(ctx)
    mocked.assert_not_awaited()
    assert out["is_web"] is True
    assert out["language"] == "python"
    assert out["framework"] == "fastapi"


@pytest.mark.asyncio
async def test_profile_node_sdk_on_calls_ai_when_ambiguous(tmp_path):
    (tmp_path / "README.md").write_text("# notes\n")
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={
            "source": {
                "project_path": str(tmp_path),
                "repo_dirname": "demo",
                "workspace_path": "/workspace/demo",
            }
        },
    )
    fake_settings = MagicMock(claude_agent_sdk_enabled=True)
    ai_out = {
        "is_web": False,
        "language": "other",
        "non_web_reason": "文档仓库",
    }
    with (
        patch("app.core.config.get_settings", return_value=fake_settings),
        patch(
            "app.contexts.agent.ai_runner.run_ai_node",
            new_callable=AsyncMock,
            return_value=ai_out,
        ) as mocked,
    ):
        out = await ProfileNode().execute(ctx)
    mocked.assert_awaited_once()
    assert out["is_web"] is False
    assert out["non_web_reason"] == "文档仓库"


@pytest.mark.asyncio
async def test_profile_node_sdk_on_asks_ai_when_language_uncertain(tmp_path):
    (tmp_path / "go.mod").write_text("module example.com/app\ngo 1.22\n")
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={
            "source": {
                "project_path": str(tmp_path),
                "repo_dirname": "demo",
                "workspace_path": "/workspace/demo",
            }
        },
    )
    fake_settings = MagicMock(claude_agent_sdk_enabled=True)
    with (
        patch("app.core.config.get_settings", return_value=fake_settings),
        patch(
            "app.contexts.agent.ai_runner.run_ai_node",
            new_callable=AsyncMock,
            return_value={"is_web": True, "language": "go", "framework": "net/http", "port": 8080},
        ) as mocked,
    ):
        out = await ProfileNode().execute(ctx)
    mocked.assert_awaited_once()
    assert out["is_web"] is True
    assert out["language"] == "go"


@pytest.mark.asyncio
async def test_profile_node_reuses_cached_profile_for_sha(tmp_path):
    cached = {
        "is_web": True,
        "language": "go",
        "framework": "gin",
        "port": 8080,
    }
    svc = MagicMock()
    svc.find_cached_profile = AsyncMock(return_value=cached)
    svc.save_source_profile = AsyncMock()
    svc.update_profile = AsyncMock()
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir=str(tmp_path),
        source_path=str(tmp_path), vulnerability_description="d",
        project_address="https://github.com/acme/app.git",
        project_ref="main",
        owner_id="u1",
        project_id="p1",
        db_session=object(),
        previous_outputs={
            "source": {
                "project_path": str(tmp_path),
                "commit_sha": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            }
        },
    )
    fake_settings = MagicMock(claude_agent_sdk_enabled=True)
    with (
        patch("app.core.config.get_settings", return_value=fake_settings),
        patch(
            "app.contexts.project.service.ProjectService",
            return_value=svc,
        ),
        patch("app.contexts.project.repository.ProjectRepository"),
        patch(
            "app.contexts.agent.ai_runner.run_ai_node",
            new_callable=AsyncMock,
        ) as mocked,
    ):
        out = await ProfileNode().execute(ctx)
    mocked.assert_not_awaited()
    assert out == cached
    svc.find_cached_profile.assert_awaited_once()


def test_node_context_carries_previous_outputs():
    ctx = NodeContext(
        task_id="t1", run_id="r1", host_workdir="/tmp",
        source_path="/tmp", vulnerability_description="d",
        project_address="x", project_ref=None,
        previous_outputs={"source": {"commit": "abc"}},
    )
    assert ctx.previous_outputs["source"]["commit"] == "abc"
