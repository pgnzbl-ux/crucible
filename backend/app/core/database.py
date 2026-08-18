from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from .config import get_settings

settings = get_settings()


def _engine_kwargs(url: str) -> dict:
    if "sqlite" in url:
        return {
            "connect_args": {"check_same_thread": False},
            "poolclass": StaticPool,
        }
    return {"pool_size": 5, "max_overflow": 10}


engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    **_engine_kwargs(settings.database_url),
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db_session() -> AsyncSession:
    """FastAPI 依赖注入：获取一个数据库会话"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _alembic_head() -> str:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    backend_root = Path(__file__).resolve().parents[2]
    cfg = Config(str(backend_root / "alembic.ini"))
    cfg.set_main_option("script_location", str(backend_root / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if not head:
        raise RuntimeError("Alembic 没有 head revision")
    return head


def _create_missing_indexes(connection) -> None:
    """create_all 不给已存在的表补索引；按当前 metadata 幂等补齐。"""
    from app.shared.base import Base

    for table in Base.metadata.sorted_tables:
        for index in table.indexes:
            index.create(connection, checkfirst=True)


def _align_alembic_version(connection) -> None:
    """create_all 之后把 alembic_version 钉到当前唯一基线，避免旧多 head 链残留。"""
    head = _alembic_head()
    inspector = inspect(connection)
    if "alembic_version" not in inspector.get_table_names():
        connection.execute(
            text(
                "CREATE TABLE alembic_version ("
                "version_num VARCHAR(32) NOT NULL PRIMARY KEY"
                ")"
            )
        )
    rows = list(connection.execute(text("SELECT version_num FROM alembic_version")))
    current = {row[0] for row in rows}
    if current == {head}:
        return
    connection.execute(text("DELETE FROM alembic_version"))
    connection.execute(
        text("INSERT INTO alembic_version (version_num) VALUES (:v)"),
        {"v": head},
    )


async def init_db() -> None:
    """按当前 ORM 建表，并与唯一 Alembic 基线对齐。

    运行时开发/生产都是 PostgreSQL（`.env` 的 DATABASE_URL）。
    pytest 由 `tests/conftest.py` 覆盖为 sqlite。生产迁移用 `alembic upgrade head`。
    """
    from app.shared.base import Base
    from app.shared.models import register_models

    register_models()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_create_missing_indexes)
        await conn.run_sync(_align_alembic_version)


async def close_db() -> None:
    await engine.dispose()
