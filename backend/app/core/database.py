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


async def close_db() -> None:
    await engine.dispose()
