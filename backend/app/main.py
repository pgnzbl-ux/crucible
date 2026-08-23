"""
Crucible — AI-powered vulnerability verification platform.

入口文件保持精简（目标 <100 行），职责：
1. 创建 FastAPI 实例
2. 注册中间件（CORS / GZip / 错误处理）
3. 挂载 Context 路由
4. 配置 Prometheus 指标
5. 生命周期管理（启动 → 初始化 DB → 关闭）
"""

from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from prometheus_fastapi_instrumentator import Instrumentator

from app.core.config import get_settings
from app.core.database import close_db, init_db


settings = get_settings()


# ── 生命周期 ──

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    # 启动：开发环境按当前 ORM 建表（SQLite / PostgreSQL 均可）
    if settings.environment == "development":
        await init_db()
    yield
    # 关闭：释放数据库连接
    await close_db()


# ── 应用工厂 ──

def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url="/redoc" if settings.environment != "production" else None,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # GZip 压缩
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # Prometheus 指标（排除健康检查）
    Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=True,
        excluded_handlers=["/health", "/metrics"],
    ).instrument(app).expose(app, endpoint="/metrics")

    # ── 挂载 Context 路由 ──
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

    # ── 健康检查 ──
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": settings.app_version}

    return app


app = create_app()


# ── 直接运行入口 ──
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8010, reload=True)
