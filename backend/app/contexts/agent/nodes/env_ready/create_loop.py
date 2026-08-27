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

        from app.contexts.agent.contracts import node_by_key
        from app.contexts.task.models import NodeRun

        env_ready_index = node_by_key("env_ready").index
        await ctx.db_session.execute(
            update(NodeRun)
            .where(
                NodeRun.run_id == ctx.run_id,
                NodeRun.node_index == env_ready_index,
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
        if not await svc.heartbeat_creation(result.lab_id, ctx.task_id):
            raise RuntimeError("靶场创建权已转移，当前任务不能继续建场")
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
            if not await svc.heartbeat_creation(result.lab_id, ctx.task_id):
                raise RuntimeError("靶场创建权已转移，当前任务停止建场")
            await events.raise_if_cancelled(ctx)
            if attempt > 1:
                await _bump_node_attempt(ctx, attempt)
            occupied = ports.list_docker_occupied_host_ports(exclude_project=exclude_project)
            events._emit(ctx, f"第 {attempt}/{MAX_ATTEMPTS} 轮：AI 分析并写 Dockerfile/compose")
            try:
                recipe = await ai_recipe.run_ai_turn(
                    ctx,
                    attempt,
                    last_error,
                    failed_stage=failed_stage,
                    occupied_host_ports=sorted(occupied),
                )
            except Exception as ai_exc:  # noqa: BLE001 — AI 未提交/网关抖动：回喂下一轮
                from app.core.agent_runner import AgentRunnerError

                if not isinstance(ai_exc, AgentRunnerError):
                    raise
                last_error = f"attempt {attempt} AI 配方失败: {ai_exc}"
                failed_stage = "ai_submit"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage)
                events._emit(
                    ctx,
                    f"AI 未产出配方，回喂重试（{attempt}/{MAX_ATTEMPTS}）",
                )
                continue
            await cache_recipe._require_creation_owner(ctx, svc, result.lab_id)

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
            if not ports.load_compose_declares_web_port(abs_compose):
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
                stage = compose_host.classify_compose_failure_stage(err)
                if stage == "docker_unavailable":
                    last_error = f"attempt {attempt} Docker 平台不可用: {err}"
                    failed_stage = stage
                    _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                    raise RuntimeError(f"failed_stage={failed_stage}\n{last_error}")
                logs = await compose_host.collect_compose_logs(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                label = {
                    "compose_build": "镜像构建失败",
                    "container_start": "容器启动失败",
                    "container_healthcheck": "Docker healthcheck 失败",
                    "compose_policy": "Compose 安全策略拒绝",
                    "compose_timeout": "Compose 执行超时",
                    "port_conflict": "宿主端口冲突",
                }.get(stage, "compose up 失败")
                last_error = (
                    f"attempt {attempt} {label}: {err}\n"
                    f"--- diagnostics ---\n{compose_host.summarize_compose_failure(logs)}"
                )
                failed_stage = stage
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                logger.warning(
                    "env_ready attempt %s stage=%s 失败: %s",
                    attempt,
                    stage,
                    err[:200],
                )
                events._emit(ctx, f"启动失败，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                await cache_recipe._require_creation_owner(ctx, svc, result.lab_id)
                await compose_host.docker_compose_down(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                continue

            await cache_recipe._require_creation_owner(ctx, svc, result.lab_id)

            advertise = target_url.host_advertise_ip()
            try:
                runtime_bindings = await ports.load_runtime_web_bindings(
                    result.compose_project
                )
            except Exception as exc:  # noqa: BLE001
                runtime_bindings = []
                runtime_binding_error = f"读取 Docker 实际发布端口失败: {exc}"
            else:
                runtime_binding_error = ""
            usable_bindings = ports.publishable_runtime_bindings(
                runtime_bindings,
                advertise,
            )
            if not usable_bindings:
                logs = await compose_host.collect_compose_logs(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                last_error = (
                    f"attempt {attempt} 无可供复现容器访问的 TCP Web 绑定。"
                    f"{runtime_binding_error or '请勿只绑定 127.0.0.1/::1，也不要只发布 UDP。'}\n"
                    f"--- logs ---\n{compose_host.summarize_compose_failure(logs)}"
                )
                failed_stage = "health_check"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                events._emit(ctx, f"发布端口不可达，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                await cache_recipe._require_creation_owner(ctx, svc, result.lab_id)
                await compose_host.docker_compose_down(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                continue

            raw_url = recipe.get("target_url") or ""
            usable_bindings, recipe_port_err = ports.filter_bindings_for_recipe(
                usable_bindings,
                target_url=str(raw_url) if raw_url else None,
            )
            if recipe_port_err or not usable_bindings:
                logs = await compose_host.collect_compose_logs(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                last_error = (
                    f"attempt {attempt} {recipe_port_err or '配方声明入口无可用绑定'}\n"
                    f"--- logs ---\n{compose_host.summarize_compose_failure(logs)}"
                )
                failed_stage = "health_check"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                events._emit(
                    ctx,
                    f"配方入口未发布，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）",
                )
                await cache_recipe._require_creation_owner(ctx, svc, result.lab_id)
                await compose_host.docker_compose_down(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                continue

            runtime_host_ports = [int(item["host_port"]) for item in usable_bindings]
            events._emit(
                ctx,
                f"正在探活实际发布端口 {runtime_host_ports}"
            )
            parsed = urlparse(
                str(raw_url) if "://" in str(raw_url) else f"http://{raw_url}"
            ) if raw_url else None
            transport = recipe.get("transport_shape")
            transport_protocol = (
                str(transport.get("protocol") or "")
                if isinstance(transport, dict)
                else ""
            )
            preferred_scheme = (
                parsed.scheme if parsed and parsed.scheme in {"http", "https"}
                else transport_protocol
            )
            probe_path = parsed.path if parsed and parsed.path else "/"
            if parsed and parsed.query:
                probe_path += f"?{parsed.query}"
            health_result = await health.health_check(
                runtime_host_ports,
                container_ports=[int(item["container_port"]) for item in usable_bindings],
                host_ips=[str(item["probe_host"]) for item in usable_bindings],
                preferred_scheme=preferred_scheme,
                probe_path=probe_path,
                compose_project=result.compose_project,
                cancel_check=events.cancel_check(ctx),
            )
            # 探活因取消提前返回时不能当健康失败回喂 AI（那会拆场进下一轮）
            await events.raise_if_cancelled(ctx)
            ok, live_port, scheme = health_result
            if not ok or live_port is None:
                logs = await compose_host.collect_compose_logs(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                last_error = (
                    f"attempt {attempt} 健康检查不过(mapped_ports={runtime_host_ports})\n"
                    f"{health.failure_reason(health_result)}\n"
                    f"--- diagnostics ---\n{compose_host.summarize_compose_failure(logs)}"
                )
                failed_stage = "health_check"
                _snapshot_failed_attempt(ctx, attempt, last_error, failed_stage, recipe)
                events._emit(ctx, f"探活失败，回喂 AI 回溯（{attempt}/{MAX_ATTEMPTS}）")
                await cache_recipe._require_creation_owner(ctx, svc, result.lab_id)
                await compose_host.docker_compose_down(
                    ctx.host_workdir, compose_rel, repo, lab_id=result.lab_id
                )
                continue

            live_binding = next(
                item for item in usable_bindings if int(item["host_port"]) == live_port
            )
            target_url_str = publish_target_url(
                live_port,
                str(live_binding["public_host"]),
                scheme=scheme,
            )
            if parsed:
                suffix = parsed.path or ""
                if parsed.query:
                    suffix += f"?{parsed.query}"
                if suffix and suffix != "/":
                    target_url_str = f"{target_url_str.rstrip('/')}{suffix}"

            events._emit(ctx, f"靶场就绪：{target_url_str}")
            transport_shape = (
                dict(transport) if isinstance(transport, dict) else {}
            )
            transport_shape["protocol"] = scheme
            output = {
                "target_url": target_url_str,
                "compose_path": compose_rel,
                "transport_shape": transport_shape,
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

        raise RuntimeError(
            f"靶场搭建 {MAX_ATTEMPTS} 轮全失败: "
            f"failed_stage={failed_stage or 'unknown'}\n"
            f"{(last_error or 'unknown')[:500]}"
        )
    except Exception as e:
        # 异常本体优先：last_error 是上一轮排障的旧账，拿它掩盖本轮异常
        # 会误导排障（attempt≥2 的 AI 异常曾被旧错误顶替）
        detail = str(e).strip() or last_error or "unknown"
        await svc.mark_failed(
            result.lab_id,
            detail[:500],
            expected_statuses={"creating"},
            expected_creator_task_id=ctx.task_id,
        )
        raise
