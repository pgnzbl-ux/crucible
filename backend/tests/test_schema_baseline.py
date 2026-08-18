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


def test_single_alembic_baseline():
    versions = [
        p for p in (Path(__file__).resolve().parents[1] / "alembic" / "versions").glob("*.py")
        if p.name != "__init__.py"
    ]
    assert len(versions) == 1, [p.name for p in versions]
    source = versions[0].read_text(encoding="utf-8")
    assert 'revision: str = "c18a0e9b4d21"' in source
    assert "down_revision: Union[str, None] = None" in source


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
            task_indexes = {i["name"] for i in insp.get_indexes("tasks")}
            assert "ix_tasks_lab_id" in task_indexes
            uniques = {
                tuple(u["column_names"])
                for u in insp.get_unique_constraints("source_artifacts")
            }
            assert ("owner_id", "git_host", "project_key", "ref_type", "ref_name") in uniques
            fks = {(tuple(f["constrained_columns"]), f["referred_table"]) for f in insp.get_foreign_keys("tasks")}
            assert (("lab_id",), "labs") in fks

        await conn.run_sync(_check)
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
