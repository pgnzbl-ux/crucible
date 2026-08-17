"""开发 SQLite：旧 source_artifacts UNIQUE 必须升级到 owner+host 隔离。"""
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_ensure_dev_columns_upgrades_source_artifacts_unique(tmp_path):
    """旧 UNIQUE(project_key,ref_type,ref_name) 会挡住不同 owner 写入同一 ref。"""
    from app.core.database import _ensure_dev_columns

    db = tmp_path / "legacy.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE source_artifacts (
                    git_url VARCHAR(1024) NOT NULL,
                    git_host VARCHAR(255) NOT NULL,
                    project_key VARCHAR(512) NOT NULL,
                    repo_dirname VARCHAR(255) NOT NULL,
                    ref_type VARCHAR(16) NOT NULL,
                    ref_name VARCHAR(255) NOT NULL,
                    commit_sha VARCHAR(40) NOT NULL,
                    bucket VARCHAR(64) NOT NULL,
                    object_key VARCHAR(512) NOT NULL,
                    object_url VARCHAR(1024) NOT NULL,
                    size_bytes INTEGER,
                    id VARCHAR(36) NOT NULL,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP NOT NULL,
                    owner_id VARCHAR(36) NOT NULL DEFAULT '',
                    profile_json TEXT,
                    PRIMARY KEY (id),
                    CONSTRAINT uq_source_artifacts_key_ref
                        UNIQUE (project_key, ref_type, ref_name)
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                INSERT INTO source_artifacts (
                    id, git_url, git_host, project_key, repo_dirname,
                    ref_type, ref_name, commit_sha, bucket, object_key, object_url, owner_id
                ) VALUES (
                    'a1', 'https://github.com/acme/app', 'github.com', 'acme/app', 'app',
                    'branch', 'main', 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                    'crucible-source', 'k', 'http://x', ''
                )
                """
            )
        )
        await _ensure_dev_columns(conn)

        sql = (
            await conn.execute(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type='table' AND name='source_artifacts'"
                )
            )
        ).fetchone()[0]
        assert "uq_source_artifacts_owner_host_ref" in sql
        assert "uq_source_artifacts_key_ref" not in sql

        # 不同 owner 写同一 project_key+ref 必须成功
        await conn.execute(
            text(
                """
                INSERT INTO source_artifacts (
                    id, git_url, git_host, project_key, repo_dirname,
                    ref_type, ref_name, commit_sha, bucket, object_key, object_url, owner_id
                ) VALUES (
                    'a2', 'https://github.com/acme/app', 'github.com', 'acme/app', 'app',
                    'branch', 'main', 'bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb',
                    'crucible-source', 'k2', 'http://x2', 'user-2'
                )
                """
            )
        )

    await engine.dispose()
