from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings

settings = get_settings()

# 异步引擎 — SQLite 需要 aiosqlite，PostgreSQL 需要 asyncpg
_connect_args = {}
if "sqlite" in settings.database_url:
    _connect_args["check_same_thread"] = False

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    connect_args=_connect_args,
    pool_size=5,
    max_overflow=10,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    """所有 ORM 模型的基类"""
    pass


async def get_db_session() -> AsyncSession:
    """FastAPI 依赖注入：获取一个数据库会话"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """创建所有表（开发环境用，生产用 Alembic）。

    create_all 只建不存在的表，不会给已存在的表加新列。开发环境 SQLite 叠加
    _ensure_dev_columns 做幂等迁移（如 tasks.credential_refs）；生产用 Alembic。
    """
    async with engine.begin() as conn:
        from app.shared.base import Base

        await conn.run_sync(Base.metadata.create_all)
        if "sqlite" in settings.database_url:
            await _ensure_dev_columns(conn)


async def _ensure_dev_columns(conn) -> None:
    """SQLite 开发环境：给已存在的表补缺失列（create_all 补丁）。

    每次新增列在此登记一条 ALTER（IF NOT EXISTS 语义用 PRAGMA table_info 判断）。
    生产环境用 Alembic，不走这里。
    """
    from sqlalchemy import text

    # tasks.credential_refs（P1-6 Credential Proxy）
    cols = (await conn.execute(text("PRAGMA table_info(tasks)"))).fetchall()
    col_names = {c[1] for c in cols}
    if cols and "credential_refs" not in col_names:
        await conn.execute(
            text("ALTER TABLE tasks ADD COLUMN credential_refs TEXT NOT NULL DEFAULT '[]'")
        )
    if cols and "lab_id" not in col_names:
        await conn.execute(text("ALTER TABLE tasks ADD COLUMN lab_id VARCHAR(36)"))

    artifact_cols = (await conn.execute(text("PRAGMA table_info(source_artifacts)"))).fetchall()
    artifact_names = {c[1] for c in artifact_cols}
    if artifact_cols and "owner_id" not in artifact_names:
        await conn.execute(
            text(
                "ALTER TABLE source_artifacts "
                "ADD COLUMN owner_id VARCHAR(36) NOT NULL DEFAULT ''"
            )
        )
    if artifact_cols and "profile_json" not in artifact_names:
        await conn.execute(text("ALTER TABLE source_artifacts ADD COLUMN profile_json TEXT"))
    if artifact_cols:
        await _ensure_source_artifacts_unique(conn)


async def _ensure_source_artifacts_unique(conn) -> None:
    """SQLite：旧 UNIQUE(project_key,ref_type,ref_name) → owner+host 隔离键。

    create_all / ALTER COLUMN 不会改已有 UNIQUE；列补丁后若仍留旧约束，
    upsert 按 owner 查找 miss 后 INSERT 会撞 UNIQUE（本会话连带 PendingRollbackError）。
    """
    from sqlalchemy import text

    row = (
        await conn.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='table' AND name='source_artifacts'"
            )
        )
    ).fetchone()
    if not row or not row[0]:
        return
    table_sql = row[0]
    if "uq_source_artifacts_owner_host_ref" in table_sql:
        return
    if "uq_source_artifacts_key_ref" not in table_sql and "UNIQUE (project_key, ref_type, ref_name)" not in table_sql:
        return

    await conn.execute(text("PRAGMA foreign_keys=OFF"))
    await conn.execute(
        text(
            """
            CREATE TABLE source_artifacts__new (
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
                CONSTRAINT uq_source_artifacts_owner_host_ref
                    UNIQUE (owner_id, git_host, project_key, ref_type, ref_name)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            INSERT INTO source_artifacts__new (
                git_url, git_host, project_key, repo_dirname, ref_type, ref_name,
                commit_sha, bucket, object_key, object_url, size_bytes,
                id, created_at, updated_at, owner_id, profile_json
            )
            SELECT
                git_url, git_host, project_key, repo_dirname, ref_type, ref_name,
                commit_sha, bucket, object_key, object_url, size_bytes,
                id, created_at, updated_at, owner_id, profile_json
            FROM source_artifacts
            """
        )
    )
    await conn.execute(text("DROP TABLE source_artifacts"))
    await conn.execute(text("ALTER TABLE source_artifacts__new RENAME TO source_artifacts"))
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_source_artifacts_project_key ON source_artifacts (project_key)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_source_artifacts_commit_sha ON source_artifacts (commit_sha)")
    )
    await conn.execute(
        text("CREATE INDEX IF NOT EXISTS ix_source_artifacts_owner_id ON source_artifacts (owner_id)")
    )
    await conn.execute(text("PRAGMA foreign_keys=ON"))


async def close_db() -> None:
    await engine.dispose()
