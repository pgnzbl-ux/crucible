"""
Crucible — AI-powered vulnerability verification platform.

入口文件保持精简，职责：
1. 创建 FastAPI 实例
2. 注册中间件（CORS / GZip / 错误处理）
3. 挂载 Context 路由
4. 配置 Prometheus 指标（受 METRICS_TOKEN 保护）
5. 生命周期管理（启动 → 初始化 DB → 关闭）
"""

from contextlib import asynccontextmanager
from typing import Annotated, AsyncGenerator

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import get_settings
from app.core.database import close_db, init_db


settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    if settings.environment == "development":
        await init_db()
    yield
    await close_db()


def create_app() -> FastAPI:
    is_prod = settings.environment == "production"
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None if is_prod else "/redoc",
        openapi_url=None if is_prod else "/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Confirm", "Last-Event-ID"],
    )

    app.add_middleware(GZipMiddleware, minimum_size=1000)

    Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/health/ready", "/metrics"],
    ).instrument(app)

    from app.contexts.finding.api import router as finding_router
    from app.contexts.identity.api import router as auth_router
    from app.contexts.lab.api import router as lab_router
    from app.contexts.project.api import router as project_router
    from app.contexts.report.api import router as report_router
    from app.contexts.settings.api import router as settings_router
    from app.contexts.task.api import router as task_router

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(finding_router, prefix="/api/v1")
    app.include_router(task_router, prefix="/api/v1")
    app.include_router(report_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")
    app.include_router(project_router, prefix="/api/v1")
    app.include_router(lab_router, prefix="/api/v1")

    from app.shared.exception_handlers import register_exception_handlers

    register_exception_handlers(app)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": settings.app_version}

    @app.get("/health/ready")
    async def health_ready():
        """就绪探针：Postgres + Redis。失败 503。"""
        errors: list[str] = []
        try:
            from sqlalchemy import text

            from app.core.database import async_session_factory

            async with async_session_factory() as session:
                await session.execute(text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"database: {type(exc).__name__}")
        try:
            import redis.asyncio as redis_async

            client = redis_async.from_url(settings.redis_url, decode_responses=True)
            try:
                await client.ping()
            finally:
                await client.aclose()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"redis: {type(exc).__name__}")
        if errors:
            raise HTTPException(503, detail={"status": "not_ready", "errors": errors})
        return {"status": "ready", "version": settings.app_version}

    @app.get("/metrics")
    async def metrics(
        authorization: Annotated[str | None, Header()] = None,
    ):
        """Prometheus。配置了 METRICS_TOKEN 时需 Bearer；生产启动时强制有 token。"""
        expected = (settings.metrics_token or "").strip()
        if expected:
            got = ""
            if authorization and authorization.lower().startswith("bearer "):
                got = authorization[7:].strip()
            if got != expected:
                raise HTTPException(401, "metrics 需要有效 Bearer token")
        return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8010, reload=True)
