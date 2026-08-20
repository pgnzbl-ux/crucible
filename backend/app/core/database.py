import subprocess
import sys
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.schema import CreateColumn
from sqlalchemy.pool import StaticPool

from .config import get_settings

settings = get_settings()
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


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

    cfg = Config(str(_BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(_BACKEND_ROOT / "alembic"))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    if not head:
        raise RuntimeError("Alembic 没有 head revision")
    return head


def _run_alembic_upgrade_head() -> None:
    """子进程跑 upgrade，避免在已有 asyncio loop 里嵌套 alembic 的 asyncio.run。"""
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=_BACKEND_ROOT,
        check=True,
    )


def _create_missing_indexes(connection) -> None:
    """create_all 不给已存在的表补索引；按当前 metadata 幂等补齐。

    同名索引若 unique 标志与模型不一致（例如旧的非唯一 idx_agent_events_run_seq），
    先 drop 再按 metadata 重建。
    """
    from app.shared.base import Base

    inspector = inspect(connection)
    for table in Base.metadata.sorted_tables:
        existing = {ix["name"]: ix for ix in inspector.get_indexes(table.name)}
        for index in table.indexes:
            current = existing.get(index.name) if index.name else None
            if current is not None and bool(current.get("unique")) != bool(index.unique):
                index.drop(connection)
                index.create(connection)
                continue
            index.create(connection, checkfirst=True)


def _align_datetime_timezone(connection) -> None:
    """create_all 不改已有列类型。把残留的 timestamp without time zone 升成 timestamptz。"""
    if connection.dialect.name != "postgresql":
        return
    rows = connection.execute(
        text(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND data_type = 'timestamp without time zone'
            """
        )
    ).fetchall()
    for table, column in rows:
        if not str(table).isidentifier() or not str(column).isidentifier():
            continue
        connection.execute(
            text(
                f'ALTER TABLE {table} ALTER COLUMN {column} '
                f"TYPE TIMESTAMP WITH TIME ZONE USING {column} AT TIME ZONE 'UTC'"
            )
        )


def _align_string_column_lengths(connection) -> None:
    """create_all 不改已有列长度。把短于当前模型的 varchar 加宽（只加长，不缩短）。"""
    if connection.dialect.name != "postgresql":
        return
    from sqlalchemy import String

    from app.shared.base import Base

    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables or not str(table.name).isidentifier():
            continue
        existing = {c["name"]: c for c in inspector.get_columns(table.name)}
        for column in table.columns:
            wanted = getattr(column.type, "length", None)
            if wanted is None or not isinstance(column.type, String):
                continue
            current = existing.get(column.name)
            if current is None or not str(column.name).isidentifier():
                continue
            actual = getattr(current.get("type"), "length", None)
            if actual is None or actual >= wanted:
                continue
            connection.execute(
                text(
                    f"ALTER TABLE {table.name} ALTER COLUMN {column.name} "
                    f"TYPE VARCHAR({int(wanted)})"
                )
            )


def _align_missing_columns(connection) -> None:
    """create_all 不给已存在的表补列；按 metadata 幂等 ADD COLUMN。

    防 init_db 曾 stamp head 却未跑增量 migration 时漏列。
    """
    if connection.dialect.name != "postgresql":
        return
    from app.shared.base import Base

    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables or not str(table.name).isidentifier():
            continue
        existing = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing or not str(column.name).isidentifier():
                continue
            col_ddl = str(CreateColumn(column).compile(dialect=connection.dialect))
            connection.execute(text(f"ALTER TABLE {table.name} ADD COLUMN {col_ddl}"))


def _align_column_comments(connection) -> None:
    """把 ORM comment 同步到 PostgreSQL（幂等）。"""
    if connection.dialect.name != "postgresql":
        return
    from app.shared.base import Base

    inspector = inspect(connection)
    existing_tables = set(inspector.get_table_names())
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables or not str(table.name).isidentifier():
            continue
        db_comments = {
            c["name"]: c.get("comment") for c in inspector.get_columns(table.name)
        }
        for column in table.columns:
            if column.comment is None or not str(column.name).isidentifier():
                continue
            if db_comments.get(column.name) == column.comment:
                continue
            escaped = column.comment.replace("'", "''")
            connection.execute(
                text(f"COMMENT ON COLUMN {table.name}.{column.name} IS '{escaped}'")
            )


def _align_alembic_version(connection) -> None:
    """create_all / upgrade 之后把 alembic_version 钉到当前 head，避免旧多 head 链残留。"""
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
    """对齐 schema：PostgreSQL 先 upgrade head，再按 ORM 补齐缺口并 stamp。

    运行时开发/生产都是 PostgreSQL（`.env` 的 DATABASE_URL）。
    pytest 由 `tests/conftest.py` 覆盖为 sqlite（跳过 alembic CLI）。
    """
    from app.shared.base import Base
    from app.shared.models import register_models

    register_models()
    if engine.dialect.name == "postgresql":
        _run_alembic_upgrade_head()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_align_missing_columns)
        await conn.run_sync(_create_missing_indexes)
        await conn.run_sync(_align_datetime_timezone)
        await conn.run_sync(_align_string_column_lengths)
        await conn.run_sync(_align_column_comments)
        await conn.run_sync(_align_alembic_version)


async def close_db() -> None:
    await engine.dispose()
