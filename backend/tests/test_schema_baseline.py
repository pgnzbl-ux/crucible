"""ORM create_all 与唯一 Alembic 基线对齐。"""

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.shared.base import Base
from app.shared.models import register_models


def test_alembic_chain_from_baseline():
    versions = Path(__file__).resolve().parents[1] / "alembic" / "versions"
    baseline = (versions / "c18a0e9b4d21_baseline.py").read_text(encoding="utf-8")
    assert 'revision: str = "c18a0e9b4d21"' in baseline
    assert "down_revision: Union[str, None] = None" in baseline
    incremental = (versions / "b7e4c2a19f08_unique_event_seq_report_run.py").read_text(encoding="utf-8")
    assert 'revision: str = "b7e4c2a19f08"' in incremental
    assert 'down_revision: Union[str, None] = "c18a0e9b4d21"' in incremental
    platform = (versions / "e8c3a1b047d2_add_platform_settings.py").read_text(encoding="utf-8")
    assert 'revision: str = "e8c3a1b047d2"' in platform
    assert 'down_revision: Union[str, None] = "b7e4c2a19f08"' in platform
    timestamptz = (versions / "a1b8c3d049e4_timestamptz_business_columns.py").read_text(encoding="utf-8")
    assert 'revision: str = "a1b8c3d049e4"' in timestamptz
    assert 'down_revision: Union[str, None] = "e8c3a1b047d2"' in timestamptz
    git_ref = (versions / "f3a9c2d18e04_task_git_ref_type_clone_depth.py").read_text(encoding="utf-8")
    assert 'revision: str = "f3a9c2d18e04"' in git_ref
    upload = (versions / "d4b7e1c08a92_project_source_type_upload.py").read_text(encoding="utf-8")
    assert 'revision: str = "d4b7e1c08a92"' in upload
    assert 'down_revision: Union[str, None] = "f3a9c2d18e04"' in upload
    lab_sha = (versions / "e7d2b4a10c95_lab_commit_sha_length.py").read_text(encoding="utf-8")
    assert 'revision: str = "e7d2b4a10c95"' in lab_sha
    assert 'down_revision: Union[str, None] = "d4b7e1c08a92"' in lab_sha
    ref_type = (versions / "f8c2a1b03d14_project_default_ref_type.py").read_text(encoding="utf-8")
    assert 'revision: str = "f8c2a1b03d14"' in ref_type
    assert 'down_revision: Union[str, None] = "e7d2b4a10c95"' in ref_type
    llm_compat = (versions / "g7b3e9a02c15_llm_provider_drop_openai_compat.py").read_text(encoding="utf-8")
    assert 'revision: str = "g7b3e9a02c15"' in llm_compat
    assert 'down_revision: Union[str, None] = "f8c2a1b03d14"' in llm_compat
    comments = (versions / "h1c4d8e05f26_sync_orm_column_comments.py").read_text(encoding="utf-8")
    assert 'revision: str = "h1c4d8e05f26"' in comments
    assert 'down_revision: Union[str, None] = "g7b3e9a02c15"' in comments
    discovery = (versions / "i2d5f6a07b31_discovery_wp1_tables.py").read_text(encoding="utf-8")
    assert 'revision: str = "i2d5f6a07b31"' in discovery
    assert 'down_revision: Union[str, None] = "h1c4d8e05f26"' in discovery
    lead_runs = (versions / "j3e6a7b18c42_lead_runs_table.py").read_text(encoding="utf-8")
    assert 'revision: str = "j3e6a7b18c42"' in lead_runs
    assert 'down_revision: Union[str, None] = "i2d5f6a07b31"' in lead_runs
    runtime_budget = (versions / "k4f7b8c29d53_runtime_concurrency_budget.py").read_text(encoding="utf-8")
    assert 'revision: str = "k4f7b8c29d53"' in runtime_budget
    assert 'down_revision: Union[str, None] = "j3e6a7b18c42"' in runtime_budget
    triage_provenance = (versions / "l5f8d2c31a70_triage_verdict_provenance.py").read_text(encoding="utf-8")
    assert 'revision: str = "l5f8d2c31a70"' in triage_provenance
    assert 'down_revision: Union[str, None] = "k4f7b8c29d53"' in triage_provenance
    budget_ledger = (versions / "m6e0b3c42d81_task_token_budget_ledger.py").read_text(encoding="utf-8")
    assert 'revision: str = "m6e0b3c42d81"' in budget_ledger
    assert 'down_revision: Union[str, None] = "l5f8d2c31a70"' in budget_ledger
    llm_advanced = (versions / "n7a1c4e53f92_llm_provider_advanced_settings.py").read_text(encoding="utf-8")
    assert 'revision: str = "n7a1c4e53f92"' in llm_advanced
    assert 'down_revision: Union[str, None] = "m6e0b3c42d81"' in llm_advanced
    from app.core.database import _alembic_head

    auth_mode = (versions / "o8b2d5c64a03_llm_provider_auth_mode.py").read_text(encoding="utf-8")
    assert 'revision: str = "o8b2d5c64a03"' in auth_mode
    assert 'down_revision: Union[str, None] = "n7a1c4e53f92"' in auth_mode
    assert _alembic_head() == "o8b2d5c64a03"


@pytest.mark.asyncio
async def test_create_all_schema_matches_models():
    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

        def _check(sync_conn):
            insp = inspect(sync_conn)
            provider_cols = {c["name"] for c in insp.get_columns("llm_providers")}
            assert "enabled" not in provider_cols
            assert "is_default" in provider_cols
            assert {"temperature", "max_context_tokens", "effort", "auth_mode"} <= provider_cols
            task_cols = {c["name"] for c in insp.get_columns("tasks")}
            assert "lab_id" in task_cols
            project_cols = {c["name"] for c in insp.get_columns("projects")}
            assert "source_type" in project_cols
            assert "default_ref_type" in project_cols
            lab_sha = next(c for c in insp.get_columns("labs") if c["name"] == "commit_sha")
            assert lab_sha["type"].length == 64
            task_indexes = {i["name"] for i in insp.get_indexes("tasks")}
            assert "ix_tasks_lab_id" in task_indexes
            uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("source_artifacts")}
            assert ("owner_id", "git_host", "project_key", "ref_type", "ref_name") in uniques
            fks = {(tuple(f["constrained_columns"]), f["referred_table"]) for f in insp.get_foreign_keys("tasks")}
            assert (("lab_id",), "labs") in fks
            assert "node_run_failures" in insp.get_table_names()
            fail_cols = {c["name"] for c in insp.get_columns("node_run_failures")}
            assert {
                "owner_id",
                "task_id",
                "run_id",
                "node_run_id",
                "node_key",
                "error_class",
                "bundle_key",
                "bucket",
            } <= fail_cols
            assert "platform_settings" in insp.get_table_names()
            runtime_cols = {c["name"] for c in insp.get_columns("platform_settings")}
            assert {
                "singleton_key",
                "max_concurrent_tasks",
                "max_concurrent_agent_runners",
                "lead_verify_per_task",
                "reproduce_per_lab",
            } <= runtime_cols

        await conn.run_sync(_check)
    await engine.dispose()


@pytest.mark.asyncio
async def test_align_datetime_timezone_skips_sqlite():
    from app.core.database import _align_datetime_timezone

    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_align_datetime_timezone)
    await engine.dispose()


@pytest.mark.asyncio
async def test_align_string_column_lengths_skips_sqlite():
    from app.core.database import _align_string_column_lengths

    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_align_string_column_lengths)
    await engine.dispose()


@pytest.mark.asyncio
async def test_align_missing_columns_skips_sqlite():
    from app.core.database import _align_missing_columns

    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_align_missing_columns)
    await engine.dispose()


@pytest.mark.asyncio
async def test_align_column_comments_skips_sqlite():
    from app.core.database import _align_column_comments

    register_models()
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_align_column_comments)
    await engine.dispose()


@pytest.mark.asyncio
async def test_align_alembic_version_stamps_head(tmp_path):
    from app.core.database import _alembic_head, _align_alembic_version

    db = tmp_path / "align.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    async with engine.begin() as conn:
        await conn.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        await conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('a9c2d4e18b07')"))
        await conn.execute(text("INSERT INTO alembic_version (version_num) VALUES ('9f2d6c8a4b17')"))
        await conn.run_sync(_align_alembic_version)
        rows = (await conn.execute(text("SELECT version_num FROM alembic_version"))).fetchall()
    await engine.dispose()
    assert [r[0] for r in rows] == [_alembic_head()]


@pytest.mark.asyncio
async def test_alembic_upgrade_head_sqlite(tmp_path, monkeypatch):
    """在临时 SQLite 上真实执行 alembic upgrade head，断言最终 schema 与 ORM 一致。

    之前迁移链只在字符串层面被校验，从未真正跑过 upgrade；本测试回归
    “基线 create_all 重复建表导致增量迁移冲突”这类问题。
    """
    from app.core.database import _alembic_head

    backend_root = Path(__file__).resolve().parents[1]
    db_path = tmp_path / "migrate.sqlite"
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{db_path.as_posix()}")

    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=str(backend_root),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"alembic upgrade head 失败:\n--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )

    register_models()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with engine.begin() as conn:

        def _check(sync_conn):
            insp = inspect(sync_conn)
            # alembic_version 应被钉到当前 head
            rows = sync_conn.execute(text("SELECT version_num FROM alembic_version")).fetchall()
            assert {r[0] for r in rows} == {_alembic_head()}
            # 全部 ORM 表都已建出
            registered = {t.name for t in Base.metadata.sorted_tables}
            assert registered <= set(insp.get_table_names())
            # 增量迁移新增的列
            task_cols = {c["name"] for c in insp.get_columns("tasks")}
            assert {
                "project_ref_type",
                "clone_depth",
                "task_type",
                "source_alert_group_id",
            } <= task_cols
            project_cols = {c["name"] for c in insp.get_columns("projects")}
            assert {"source_type", "default_ref_type"} <= project_cols
            provider_cols = {c["name"] for c in insp.get_columns("llm_providers")}
            assert "role" in provider_cols
            assert {"temperature", "max_context_tokens", "effort", "auth_mode"} <= provider_cols
            # 增量迁移新增的表
            for table in (
                "platform_settings",
                "scan_runs",
                "raw_findings",
                "alert_groups",
                "adjudications",
                "review_actions",
            ):
                assert table in insp.get_table_names(), table
            # 关键唯一约束
            uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("source_artifacts")}
            assert ("owner_id", "git_host", "project_key", "ref_type", "ref_name") in uniques

        await conn.run_sync(_check)
    await engine.dispose()
