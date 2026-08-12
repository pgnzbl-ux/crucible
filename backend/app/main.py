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
    # 启动：开发环境自动建表
    if settings.environment == "development" and "sqlite" in settings.database_url:
        await init_db()
    # 启动：环境变量 LLM 配置 → DB 种子迁移（幂等，仅在 DB 无 Provider 时执行）
    from app.core.database import async_session_factory
    from app.contexts.settings.seed import seed_llm_provider_from_env

    try:
        async with async_session_factory() as session:
            await seed_llm_provider_from_env(session)
            await session.commit()
    except Exception:
        pass  # 种子失败不阻塞启动
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
    from app.contexts.identity.api import router as auth_router
    from app.contexts.task.api import router as task_router
    from app.contexts.report.api import router as report_router
    from app.contexts.settings.api import router as settings_router
    from app.contexts.project.api import router as project_router

    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(task_router, prefix="/api/v1")
    app.include_router(report_router, prefix="/api/v1")
    app.include_router(settings_router, prefix="/api/v1")
    app.include_router(project_router, prefix="/api/v1")

    # ── 健康检查 ──
    @app.get("/health")
    async def health():
        return {"status": "ok", "version": settings.app_version}

    # ── 全局错误处理 ──
    @app.exception_handler(ValueError)
    async def value_error_handler(request, exc: ValueError):
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    return app


app = create_app()


# ── 直接运行入口 ──
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8010, reload=True)
