"""env_ready 创建路径：缓存尝试 + AI 五轮主循环。"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

from app.contexts.agent import target_url
from app.contexts.agent.target_url import publish_target_url

from ..base import NodeContext
from . import ai_recipe, cache_recipe, compose_host, events, health, ports, reuse

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 5


def _exclude_compose_project(lab_id: str | None) -> str | None:
    """与 lab_project_name 同形，避免 import runtime_cleanup 拉起 Docker client。"""
    if not lab_id:
        return None
    return f"crucible-lab-{str(lab_id).lower()}"


def _snapshot_failed_attempt(
    ctx: NodeContext,
    attempt: int,
    last_error: str | None,
    failed_stage: str | None,
    recipe: dict[str, Any] | None = None,
) -> None:
    try:
        from app.contexts.agent.node_failure import snapshot_attempt

        snapshot_attempt(
            ctx.host_workdir,
            "env_ready",
            attempt,
            previous_error=last_error,
            platform_error=f"failed_stage={failed_stage or 'unknown'}\n{last_error or ''}",
            submit=recipe,
            copy_vuln_env=True,
        )
    except Exception:
        logger.warning("env_ready 失败快照失败 attempt=%s", attempt, exc_info=True)


async def _bump_node_attempt(ctx: NodeContext, attempt: int) -> None:
    """把排障轮次写进 NodeRun.attempt（表上可见真实轮次）。best-effort。"""
    try:
        from sqlalchemy import update

        from app.contexts.task.models import NodeRun

        await ctx.db_session.execute(
            update(NodeRun)
            .where(
                NodeRun.run_id == ctx.run_id,
                NodeRun.node_index == 2,
            )
            .values(attempt=attempt)
        )
        await ctx.db_session.commit()
    except Exception:  # noqa: BLE001
        logger.warning("更新 NodeRun.attempt 失败 attempt=%s", attempt, exc_info=True)


async def _create_lab(ctx: NodeContext, result: Any) -> dict[str, Any]:
    from app.contexts.lab.service import LabService

    svc = LabService(ctx.db_session)
    last_error: str | None = None
    failed_stage: str | None = None
    inp = ctx.node_input
    if inp is None:
        raise RuntimeError("env_ready._create_lab 缺少 ctx.node_input（EnvReadyInput）")
    repo = (inp.source.repo_dirname or "").strip() or None
    exclude_project = _exclude_compose_project(result.lab_id)
    commit_sha = str(inp.source.commit_sha or "")

    try:
        cached, last_error = await cache_recipe._try_cached_recipe(
            ctx,
            svc,
            result,
            commit_sha=commit_sha,
            exclude_project=exclude_project,
            repo=repo,
        )
        if cached is not None:
            return cached
        if last_error:
            failed_stage = "cached_recipe"

        for attempt in range(1, MAX_ATTEMPTS + 1):
            if attempt > 1:
                await _bump_node_attempt(ctx, attempt)
            occupied = ports.list_docker_occupied_host_ports(exclude_project=exclude_project)
            events._emit(ctx, f"第 {attempt}/{MAX_ATTEMPTS} 轮：AI 分析并写 Dockerfile/compose")
            recipe = await ai_recipe.run_ai_turn(
                ctx,
                attempt,
                last_error,
                failed_stage=failed_stage,
                occupied_host_ports=sorted(occupied),
            )

            from app.contexts.agent.ai_runner import validate_initial_creds

            creds_ok, creds_err = validate_initial_creds(recipe.get("initial_creds"))
            if not creds_ok:
                last_error = f"attempt {attempt} {creds_err}"
                failed_stage = "recipe_validation"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                events._emit(
                    ctx,
                    f"initial_creds 无效，回喂 AI 补查（{attempt}/{MAX_ATTEMPTS}）",
                )
                continue

            compose_path = recipe.get("compose_path", ".vuln-env/docker-compose.yml")
            abs_compose = compose_host.resolve_compose_host_path(
                compose_path, ctx.host_workdir, repo
            )
            web_ports = ports.load_web_host_ports(abs_compose)
            if not web_ports:
                last_error = (
                    f"attempt {attempt} compose 未把 Web 端口映射到宿主机。"
                    "只映射浏览器访问的入口（host:container），"
                    "postgres/redis/mysql 不要写 ports 到宿主。"
                )
                failed_stage = "recipe_validation"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                events._emit(ctx, f"缺少 Web 端口映射，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                continue

            occupied = ports.list_docker_occupied_host_ports(exclude_project=exclude_project)
            conflicts = [p for p in web_ports if p in occupied]
            if conflicts:
                last_error = (
                    f"attempt {attempt} 宿主端口已被其他容器占用: {conflicts}。"
                    f"docker 当前已占用: {sorted(occupied)}。"
                    "只改 compose 的 host 侧映射口（例如 3001:3000 改成 3011:3000），"
                    "不要改容器内监听口，不要映射已占用端口。"
                )
                failed_stage = "port_conflict"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                events._emit(
                    ctx,
                    f"端口 {conflicts} 已被占用，回喂 AI 改映射（{attempt}/{MAX_ATTEMPTS}）",
                )
                continue

            compose_rel = compose_host.repo_compose_rel(compose_path)
            events._emit(
                ctx, f"第 {attempt}/{MAX_ATTEMPTS} 轮：平台启动靶场（docker compose up -d --build）"
            )
            ok, err = await compose_host.docker_compose_up(
                compose_rel,
                ctx.host_workdir,
                repo,
                lab_id=result.lab_id,
                on_progress=lambda line: events._emit(ctx, line),
            )
            if not ok:
                logs = await compose_host.collect_compose_logs(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                last_error = (
                    f"attempt {attempt} compose up 失败: {err}\n"
                    f"--- logs ---\n{compose_host.summarize_compose_failure(logs)}"
                )
                failed_stage = "compose_up"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                logger.warning(f"节点 2 attempt {attempt} 失败: {err[:200]}")
                events._emit(ctx, f"启动失败，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                await compose_host.docker_compose_down(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                continue

            events._emit(
                ctx,
                f"正在探活 127.0.0.1:{web_ports[0]}"
                + (f" 等 {len(web_ports)} 个映射口" if len(web_ports) > 1 else ""),
            )
            ok, live_port, scheme = await health.health_check(
                web_ports, container_ports=ports.load_web_container_ports(str(abs_compose))
            )
            if not ok or live_port is None:
                logs = await compose_host.collect_compose_logs(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                last_error = (
                    f"attempt {attempt} 健康检查不过(mapped_ports={web_ports})\n"
                    f"{health._health_fail_detail()}\n"
                    f"--- logs ---\n{compose_host.summarize_compose_failure(logs)}"
                )
                failed_stage = "health_check"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                events._emit(ctx, f"探活失败，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                await compose_host.docker_compose_down(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                continue

            advertise = target_url.host_advertise_ip()
            target_url_str = publish_target_url(live_port, advertise, scheme=scheme)
            raw_url = recipe.get("target_url") or ""
            if raw_url:
                parsed = urlparse(str(raw_url) if "://" in str(raw_url) else f"http://{raw_url}")
                suffix = parsed.path or ""
                if parsed.query:
                    suffix += f"?{parsed.query}"
                if suffix and suffix != "/":
                    target_url_str = f"{target_url_str.rstrip('/')}{suffix}"

            events._emit(ctx, f"靶场就绪：{target_url_str}")
            output = {
                "target_url": target_url_str,
                "compose_path": compose_rel,
                "transport_shape": recipe.get("transport_shape", {"protocol": "http"}),
                "initial_creds": recipe["initial_creds"],
                "started_containers": recipe.get("started_containers", []),
            }
            output["started_containers"] = await reuse._live_started_containers(
                getattr(result, "compose_project", None),
                output.get("started_containers"),
            )
            await cache_recipe._upload_then_mark_ready(
                ctx,
                svc,
                result,
                commit_sha=commit_sha,
                lab_compose=compose_rel,
                output=output,
                repo=repo,
            )
            return output

        raise RuntimeError(f"靶场搭建 {MAX_ATTEMPTS} 轮全失败: {(last_error or 'unknown')[:500]}")
    except Exception as e:
        # 异常本体优先：last_error 是上一轮排障的旧账，拿它掩盖本轮异常
        # 会误导排障（attempt≥2 的 AI 异常曾被旧错误顶替）
        detail = str(e).strip() or last_error or "unknown"
        await svc.mark_failed(result.lab_id, detail[:500])
        raise
