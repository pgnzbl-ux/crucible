"""Agent Runner HTTP/SSE 守护服务 — 主运行入口（镜像默认 CMD）。

协议（容器内 :8000）：
- GET  /health      就绪探活（无敏感信息；无鉴权，供容器探针）
- POST /v1/execute  Bearer 鉴权；body=AgentSpec；SSE 流式返回 AgentEventEnvelope
- POST /v1/cancel   Bearer 鉴权；软取消（事件边界收尾）+ 硬取消（cancel 当前流任务）

安全模型：
- 容器与不受信靶场同网段，因此 /v1/* 一律要求 Bearer RUNNER_AUTH_TOKEN
  （worker 每容器随机生成并经 docker env 注入）；token 未配置时 fail-closed。
- 单容器单任务（409 冲突）。
- Provider 凭据只经容器 env 注入：启动时快照，执行前恢复、执行后清除，
  空闲期进程内不残留凭据。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
from typing import AsyncIterator

from fastapi import FastAPI, Header, HTTPException, status
from fastapi.responses import StreamingResponse

from .gateway import run_spec
from .schemas import AgentSpec, encode_envelope

logger = logging.getLogger("agent-runner.server")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Crucible Agent Runner Service", version="3.0.0")

AUTH_TOKEN_ENV = "RUNNER_AUTH_TOKEN"
# Provider / SDK 相关 env：启动快照，执行期恢复、空闲期清除（防凭据跨任务残留）
_PROVIDER_ENV_RE = re.compile(r"^(ANTHROPIC_|CLAUDE_CODE_|API_TIMEOUT_MS)")


def _snapshot_provider_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if _PROVIDER_ENV_RE.match(k)}


_PROVIDER_ENV_SNAPSHOT = _snapshot_provider_env()


def _apply_provider_env() -> None:
    os.environ.update(_PROVIDER_ENV_SNAPSHOT)


def _clear_provider_env() -> None:
    for key in list(os.environ):
        if _PROVIDER_ENV_RE.match(key):
            os.environ.pop(key, None)


def _check_auth(authorization: str | None) -> None:
    expected = os.environ.get(AUTH_TOKEN_ENV, "")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"runner 未配置 {AUTH_TOKEN_ENV}，拒绝执行（fail-closed）",
        )
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="无效的鉴权令牌")


# 运行时全局状态（单容器单任务）
_current_task: asyncio.Task | None = None
_cancel_event = asyncio.Event()
_is_running = False


@app.get("/health")
async def health_check():
    """容器就绪探活端点（不暴露版本等敏感信息）。"""
    return {
        "status": "ready",
        "running": _is_running,
        "protocol_version": "3.0",
    }


@app.post("/v1/cancel")
async def cancel_execution(authorization: str | None = Header(default=None)):
    """中断当前任务：先软取消（事件边界收尾），再硬取消（cancel 当前流任务）。"""
    global _current_task
    _check_auth(authorization)
    if not _is_running:
        return {"status": "idle", "message": "当前无运行中的任务"}
    _cancel_event.set()
    task = _current_task
    if task is not None and not task.done():
        task.cancel()
    return {"status": "cancelling", "message": "已发送取消信号"}


@app.post("/v1/execute")
async def execute_agent(spec: AgentSpec, authorization: str | None = Header(default=None)):
    """执行 AgentSpec，SSE 流式返回事件信封（含 runner.exit 终帧）。"""
    global _is_running, _cancel_event, _current_task
    _check_auth(authorization)
    if _is_running:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="容器当前已有运行中的任务（单容器单任务原则）",
        )

    _is_running = True
    _cancel_event = asyncio.Event()
    _apply_provider_env()

    async def event_generator() -> AsyncIterator[str]:
        global _is_running, _current_task
        _current_task = asyncio.current_task()
        try:
            async for flat in run_spec(spec, _cancel_event):
                yield f"event: agent_event\ndata: {json.dumps(encode_envelope(flat), ensure_ascii=False)}\n\n"
        except asyncio.CancelledError:
            _current_task = None
            yield (
                "event: agent_event\ndata: "
                + json.dumps(
                    encode_envelope(
                        {
                            "type": "agent.failed",
                            "error": "任务被外部主动取消",
                            "phase": "cancelled",
                        }
                    ),
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            raise
        except Exception as exc:  # noqa: BLE001 — 兜底转失败帧，不让流悬死
            logger.exception("Agent 执行未捕获异常: %s", exc)
            yield (
                "event: agent_event\ndata: "
                + json.dumps(
                    encode_envelope(
                        {
                            "type": "agent.failed",
                            "error": f"Runner 服务异常: {exc}",
                            "phase": "runner_error",
                        }
                    ),
                    ensure_ascii=False,
                )
                + "\n\n"
            )
        finally:
            _current_task = None
            _is_running = False
            _clear_provider_env()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
