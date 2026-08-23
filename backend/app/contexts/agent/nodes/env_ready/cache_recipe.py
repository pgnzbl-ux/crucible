"""env_ready MinIO 配方缓存短路与上传落库。"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.contexts.agent import target_url
from app.contexts.agent.target_url import publish_target_url

from ..base import NodeContext
from . import ai_recipe, compose_host, events, health, ports, reuse

logger = logging.getLogger(__name__)


class CreationOwnershipLostError(RuntimeError):
    """当前任务已失去 Lab creating 租约；不得再拆共享 Compose。"""


async def _require_creation_owner(ctx: NodeContext, svc: Any, lab_id: str) -> None:
    if not await svc.heartbeat_creation(lab_id, ctx.task_id):
        raise CreationOwnershipLostError("靶场创建权已转移，当前任务停止操作共享环境")


async def _upload_then_mark_ready(
    ctx: NodeContext,
    svc: Any,
    result: Any,
    *,
    commit_sha: str,
    lab_compose: str,
    output: dict[str, Any],
    repo: str | None = None,
) -> None:
    repo_name = (repo or "").strip() or None
    recipe_root = (
        str(Path(ctx.host_workdir) / repo_name) if repo_name else ctx.host_workdir
    )
    try:
        await _require_creation_owner(ctx, svc, result.lab_id)
        uploaded = await svc.upload_recipe(
            owner_id=ctx.owner_id,
            project_id=ctx.project_id or "",
            commit_sha=commit_sha,
            lab_workdir=recipe_root,
            compose_path=lab_compose,
            transport_shape=output["transport_shape"],
            initial_creds=output["initial_creds"],
            started_containers=output.get("started_containers") or [],
        )
        if uploaded is False:
            events._emit(
                ctx,
                "警告：配方缓存上传失败（MinIO 异常），靶场仍可用；"
                "rebuild 时将无法复用本配方",
            )
        await _require_creation_owner(ctx, svc, result.lab_id)
        marked = await svc.mark_ready(
            result.lab_id,
            target_url=output["target_url"],
            compose_path=compose_host.workspace_compose_rel(repo_name, lab_compose),
            transport_shape=output["transport_shape"],
            initial_creds=output["initial_creds"],
            expected_statuses={"creating"},
            expected_creator_task_id=ctx.task_id,
        )
        if not marked:
            raise CreationOwnershipLostError(
                "靶场创建权已转移，拒绝旧创建者写入 ready"
            )
    except CreationOwnershipLostError:
        # 新 owner 复用同一个 compose project；旧 owner 不能 down 它的环境。
        raise
    except Exception:
        await compose_host.docker_compose_down(
            ctx.host_workdir,
            lab_compose,
            repo_name,
            lab_id=result.lab_id,
        )
        raise


async def _try_cached_recipe(
    ctx: NodeContext,
    svc: Any,
    result: Any,
    *,
    commit_sha: str,
    exclude_project: str | None,
    repo: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """MinIO 命中后落位 workspace、改口、up、探活。成功产出含 reused；docker 不可用则抛；失败 (None, last_error)。"""
    repo_name = (repo or "").strip() or None
    if not repo_name:
        return None, None
    repo_dir = Path(ctx.host_workdir) / repo_name

    hit = await svc.download_recipe(
        owner_id=ctx.owner_id or "",
        project_id=ctx.project_id or "",
        commit_sha=commit_sha,
        dest_workdir=str(repo_dir),
    )
    if not hit:
        return None, None

    compose_rel = compose_host.repo_compose_rel(
        hit.get("compose_path") if isinstance(hit, dict) else None
    )
    compose_file = repo_dir / compose_rel
    if not compose_file.is_file():
        return None, f"缓存配方缺少 compose 文件: {compose_rel}"

    occupied = ports.list_docker_occupied_host_ports(exclude_project=exclude_project)
    text = compose_file.read_text(encoding="utf-8", errors="replace")
    rewritten = ports.rewrite_compose_host_ports(text, occupied)
    if rewritten is None:
        web_ports = ports.web_host_ports(ports.parse_compose_port_mappings(text))
        if not web_ports:
            return None, "缓存配方 compose 未把 Web 端口映射到宿主机。"
        conflicts = [p for p in web_ports if p in occupied]
        return None, (
            f"缓存配方宿主端口无法改写: {conflicts}。"
            f"docker 当前已占用: {sorted(occupied)}。"
        )
    if rewritten != text:
        compose_file.write_text(rewritten, encoding="utf-8")

    if not ports.load_compose_declares_web_port(str(compose_file)):
        return None, "缓存配方 compose 未把 Web 端口映射到宿主机。"

    await _require_creation_owner(ctx, svc, result.lab_id)
    events._emit(ctx, "命中已缓存配方，平台启动靶场（docker compose up -d --build）")
    ok, err = await compose_host.docker_compose_up(
        compose_rel,
        ctx.host_workdir,
        repo_name,
        lab_id=result.lab_id,
        on_progress=lambda line: events._emit(ctx, line),
    )
    if not ok:
        if compose_host.is_docker_unavailable(err):
            raise RuntimeError(f"failed_stage=docker_unavailable\n{err}")
        stage = compose_host.classify_compose_failure_stage(err)
        logs = await compose_host.collect_compose_logs(
            ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
        )
        last_error = (
            f"缓存配方失败(stage={stage}): {err}\n--- diagnostics ---\n"
            f"{compose_host.summarize_compose_failure(logs)}"
        )
        logger.warning("缓存配方 compose up 失败: %s", (err or "")[:200])
        events._emit(ctx, "缓存配方启动失败，回喂 AI")
        await _require_creation_owner(ctx, svc, result.lab_id)
        await compose_host.docker_compose_down(
            ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
        )
        return None, last_error

    await _require_creation_owner(ctx, svc, result.lab_id)

    advertise = target_url.host_advertise_ip()
    try:
        runtime_bindings = await ports.load_runtime_web_bindings(result.compose_project)
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
        last_error = (
            "缓存配方无可供复现容器访问的 TCP Web 绑定。"
            + (runtime_binding_error or "请勿只绑定 127.0.0.1/::1，也不要只发布 UDP。")
        )
        events._emit(ctx, "缓存配方发布端口不可达，回喂 AI")
        await _require_creation_owner(ctx, svc, result.lab_id)
        await compose_host.docker_compose_down(
            ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
        )
        return None, last_error
    runtime_host_ports = [int(item["host_port"]) for item in usable_bindings]
    events._emit(ctx, f"正在探活实际发布端口 {runtime_host_ports}")
    shape = hit.get("transport_shape") if isinstance(hit, dict) else None
    preferred_scheme = (
        str(shape.get("protocol") or "") if isinstance(shape, dict) else ""
    )
    health_result = await health.health_check(
        runtime_host_ports,
        container_ports=[int(item["container_port"]) for item in usable_bindings],
        host_ips=[str(item["probe_host"]) for item in usable_bindings],
        preferred_scheme=preferred_scheme,
        compose_project=result.compose_project,
    )
    ok, live_port, scheme = health_result
    if not ok or live_port is None:
        logs = await compose_host.collect_compose_logs(
            ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
        )
        last_error = (
            f"健康检查不过(mapped_ports={runtime_host_ports})\n"
            f"{health.failure_reason(health_result)}\n"
            f"--- diagnostics ---\n{compose_host.summarize_compose_failure(logs)}"
        )
        events._emit(ctx, "缓存配方探活失败，回喂 AI")
        await _require_creation_owner(ctx, svc, result.lab_id)
        await compose_host.docker_compose_down(
            ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
        )
        return None, last_error

    live_binding = next(
        item for item in usable_bindings if int(item["host_port"]) == live_port
    )
    published = publish_target_url(
        live_port,
        str(live_binding["public_host"]),
        scheme=scheme,
    )
    transport_shape = dict(shape) if isinstance(shape, dict) else {}
    transport_shape["protocol"] = scheme
    output = {
        "target_url": published,
        "compose_path": compose_rel,
        "transport_shape": transport_shape,
        "initial_creds": (hit.get("initial_creds") if isinstance(hit, dict) else None) or {},
        "started_containers": (hit.get("started_containers") if isinstance(hit, dict) else None)
        or [],
        "reused": True,
    }
    output["started_containers"] = await reuse._live_started_containers(
        getattr(result, "compose_project", None),
        output.get("started_containers"),
    )
    if not output["initial_creds"]:
        # 凭据补查失败必须先拆刚 up 的靶场再抛：否则 DB=failed/容器在跑，
        # 端口泄漏且孤儿 compose 阻塞后续轮次（该分支随 P0#2 修复变可达）
        try:
            output["initial_creds"] = await ai_recipe._lookup_initial_creds(
                ctx,
                target_url=published,
                compose_path=compose_rel,
            )
        except Exception:
            await _require_creation_owner(ctx, svc, result.lab_id)
            await compose_host.docker_compose_down(
                ctx.host_workdir, compose_rel, repo_name, lab_id=result.lab_id
            )
            raise
    await _upload_then_mark_ready(
        ctx,
        svc,
        result,
        commit_sha=commit_sha,
        lab_compose=compose_rel,
        output=output,
        repo=repo_name,
    )
    events._emit(ctx, f"靶场就绪：{published}")
    return output, None
