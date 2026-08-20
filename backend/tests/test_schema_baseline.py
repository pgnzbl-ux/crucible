"""ORM create_all 与唯一 Alembic 基线对齐。"""
import os
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
    incremental = (versions / "b7e4c2a19f08_unique_event_seq_report_run.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "b7e4c2a19f08"' in incremental
    assert 'down_revision: Union[str, None] = "c18a0e9b4d21"' in incremental
    platform = (versions / "e8c3a1b047d2_add_platform_settings.py").read_text(encoding="utf-8")
    assert 'revision: str = "e8c3a1b047d2"' in platform
    assert 'down_revision: Union[str, None] = "b7e4c2a19f08"' in platform
    timestamptz = (versions / "a1b8c3d049e4_timestamptz_business_columns.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "a1b8c3d049e4"' in timestamptz
    assert 'down_revision: Union[str, None] = "e8c3a1b047d2"' in timestamptz
    git_ref = (versions / "f3a9c2d18e04_task_git_ref_type_clone_depth.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "f3a9c2d18e04"' in git_ref
    upload = (versions / "d4b7e1c08a92_project_source_type_upload.py").read_text(
        encoding="utf-8"
    )
    assert 'revision: str = "d4b7e1c08a92"' in upload
    assert 'down_revision: Union[str, None] = "f3a9c2d18e04"' in upload
    from app.core.database import _alembic_head

    assert _alembic_head() == "d4b7e1c08a92"


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
            task_cols = {c["name"] for c in insp.get_columns("tasks")}
            assert "lab_id" in task_cols
            project_cols = {c["name"] for c in insp.get_columns("projects")}
            assert "source_type" in project_cols
            task_indexes = {i["name"] for i in insp.get_indexes("tasks")}
            assert "ix_tasks_lab_id" in task_indexes
            uniques = {
                tuple(u["column_names"])
                for u in insp.get_unique_constraints("source_artifacts")
            }
            assert ("owner_id", "git_host", "project_key", "ref_type", "ref_name") in uniques
            fks = {(tuple(f["constrained_columns"]), f["referred_table"]) for f in insp.get_foreign_keys("tasks")}
            assert (("lab_id",), "labs") in fks
            assert "node_run_failures" in insp.get_table_names()
            fail_cols = {c["name"] for c in insp.get_columns("node_run_failures")}
            assert {"owner_id", "task_id", "run_id", "node_run_id", "node_key", "error_class", "bundle_key", "bucket"} <= fail_cols
            assert "platform_settings" in insp.get_table_names()
            runtime_cols = {c["name"] for c in insp.get_columns("platform_settings")}
            assert {"singleton_key", "max_concurrent_tasks"} <= runtime_cols

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
async def test_align_alembic_version_stamps_head(tmp_path):
    from app.core.database import _align_alembic_version, _alembic_head

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
