"""env_ready 时序/重试限制：platform_settings 快照解析。

单次节点执行读一次快照，循环内不重复读；任何读取失败都退化为模块默认值
（与"读设置失败不阻断"的全局约定一致）。默认值与迁移 server_default 同源，
也是 compose_host / health 模块常量的语义镜像。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..base import NodeContext


@dataclass(frozen=True)
class EnvReadyLimits:
    """靶场搭建一轮循环的时序/重试上限（秒 / 次）。"""

    max_attempts: int = 5
    compose_up_timeout: int = 600
    compose_wait: int = 300
    lab_wait_timeout: int = 1860
    probe_window: int = 90


DEFAULT_LIMITS = EnvReadyLimits()


def probe_attempts(limits: EnvReadyLimits = DEFAULT_LIMITS) -> int:
    """探活窗口(秒)换算轮数；重试间隔 3s 属实现细节，不进设置。"""
    from .health import HEALTH_RETRY_SECONDS

    return max(1, limits.probe_window // HEALTH_RETRY_SECONDS)


async def resolve_limits(ctx: NodeContext) -> EnvReadyLimits:
    """读平台设置快照；读失败/字段缺失退化默认，不阻断靶场搭建。

    收敛规则与 service._runtime_response 对齐：正数下限 + wait ≤ up。
    """
    runtime = None
    try:
        from app.contexts.settings.repository import SettingsRepository
        from app.contexts.settings.service import SettingsService

        if ctx.session_factory is not None:
            async with ctx.session_factory() as session:
                runtime = await SettingsService(
                    SettingsRepository(session)
                ).get_runtime_settings()
        elif ctx.db_session is not None:
            runtime = await SettingsService(
                SettingsRepository(ctx.db_session)
            ).get_runtime_settings()
    except Exception:  # noqa: BLE001 — 设置不可读时按默认值继续建场
        runtime = None
    if runtime is None:
        return DEFAULT_LIMITS

    up_timeout = max(
        60,
        int(
            getattr(
                runtime,
                "env_ready_compose_up_timeout_seconds",
                DEFAULT_LIMITS.compose_up_timeout,
            )
        ),
    )
    return EnvReadyLimits(
        max_attempts=max(
            1,
            int(
                getattr(
                    runtime,
                    "env_ready_max_attempts",
                    DEFAULT_LIMITS.max_attempts,
                )
            ),
        ),
        compose_up_timeout=up_timeout,
        compose_wait=min(
            max(
                30,
                int(
                    getattr(
                        runtime,
                        "env_ready_compose_wait_seconds",
                        DEFAULT_LIMITS.compose_wait,
                    )
                ),
            ),
            up_timeout,
        ),
        lab_wait_timeout=max(
            60,
            int(
                getattr(
                    runtime,
                    "env_ready_lab_wait_timeout_seconds",
                    DEFAULT_LIMITS.lab_wait_timeout,
                )
            ),
        ),
        probe_window=max(
            30,
            int(
                getattr(
                    runtime,
                    "env_ready_probe_window_seconds",
                    DEFAULT_LIMITS.probe_window,
                )
            ),
        ),
    )
