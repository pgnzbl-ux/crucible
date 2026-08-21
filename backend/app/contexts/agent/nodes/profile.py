"""节点 1 项目画像 — 默认规则引擎；同 SHA 复用；拿不准才起 AI。"""
from __future__ import annotations

from typing import Any

from app.contexts.agent.contracts import InputAssembler, ProfileInput, SourceHandoff
from app.contexts.agent.profile_detector import detect_profile, profile_needs_ai

from .base import NodeContext, workspace_repo_path

# 规则引擎可补的缺口字段；is_web 仅接受真正的 bool
_HINT_FILL_KEYS = (
    "language",
    "framework",
    "port",
    "has_dockerfile",
    "has_compose",
    "detected_services",
)

# 平台落库/展示的画像字段：架构事实 + web 门禁，不含 AI 长文
PROFILE_FACT_KEYS = (
    "is_web",
    "language",
    "framework",
    "port",
    "has_dockerfile",
    "has_compose",
    "detected_services",
    "start_command",
    "non_web_reason",
)


def coerce_is_web(value: Any) -> bool | None:
    """只接受 JSON/Python 布尔；拒绝 \"false\" 这类会被 bool() 判真的字符串。"""
    if isinstance(value, bool):
        return value
    return None


def merge_profile(ai: dict[str, Any], hints: dict[str, Any]) -> dict[str, Any]:
    """AI 产出优先；空缺由规则引擎 hints 补齐。is_web 非 bool 则保留 hints。"""
    merged = dict(hints)
    for key, value in ai.items():
        if key == "is_web":
            continue
        if value is None or value == "":
            continue
        merged[key] = value
    for key in _HINT_FILL_KEYS:
        if merged.get(key) in (None, "") and hints.get(key) not in (None, ""):
            merged[key] = hints[key]
    coerced = coerce_is_web(ai.get("is_web")) if "is_web" in ai else None
    if coerced is not None:
        merged["is_web"] = coerced
    return merged


def sanitize_profile(output: dict[str, Any]) -> dict[str, Any]:
    """节点结果只留架构事实 + is_web，丢掉 README 式长文。"""
    facts: dict[str, Any] = {}
    for key in PROFILE_FACT_KEYS:
        if key not in output:
            continue
        value = output[key]
        if key == "is_web":
            coerced = coerce_is_web(value)
            if coerced is None:
                continue
            facts[key] = coerced
            continue
        if value is None or value == "":
            continue
        facts[key] = value
    return facts


def require_is_web(facts: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(facts.get("is_web"), bool):
        raise RuntimeError("画像缺少显式 is_web，拒绝继续")
    return facts


def _project_service(ctx: NodeContext):
    if ctx.db_session is None:
        return None
    from app.contexts.project.repository import ProjectRepository
    from app.contexts.project.service import ProjectService

    return ProjectService(ProjectRepository(ctx.db_session))


async def _load_cached_profile(ctx: NodeContext, commit_sha: str | None) -> dict[str, Any] | None:
    if not ctx.owner_id or not commit_sha:
        return None
    svc = _project_service(ctx)
    if svc is None:
        return None
    return await svc.find_cached_profile(ctx.owner_id, commit_sha)


async def _persist_profile(ctx: NodeContext, commit_sha: str | None, profile: dict[str, Any]) -> None:
    svc = _project_service(ctx)
    if svc is None:
        return
    if ctx.owner_id and commit_sha:
        await svc.save_source_profile(
            owner_id=ctx.owner_id,
            commit_sha=commit_sha,
            profile=profile,
            project_id=ctx.project_id,
        )
        return
    if ctx.project_id:
        await svc.update_profile(
            ctx.project_id,
            language=profile.get("language"),
            framework=profile.get("framework"),
            is_web=profile.get("is_web"),
        )


class ProfileNode:
    node_index = 1
    node_key = "profile"

    @property
    def is_ai(self) -> bool:
        return True

    def _resolve_input(self, ctx: NodeContext, node_input: ProfileInput | None) -> ProfileInput:
        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "profile",
            ctx.previous_outputs,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input: ProfileInput | None = None) -> dict[str, Any]:
        inp = self._resolve_input(ctx, node_input)
        src: SourceHandoff = inp.source
        commit_sha = src.commit_sha

        cached = await _load_cached_profile(ctx, commit_sha)
        if cached:
            facts = require_is_web(sanitize_profile(cached))
            if ctx.project_id:
                await _persist_profile(ctx, commit_sha, facts)
            return facts

        root = src.project_path or inp.source_path or ctx.source_path
        hints = detect_profile(root)

        from app.core.config import get_settings

        use_ai = get_settings().claude_agent_sdk_enabled and profile_needs_ai(root, hints)
        if not use_ai:
            facts = require_is_web(sanitize_profile(hints))
            await _persist_profile(ctx, commit_sha, facts)
            return facts

        from app.contexts.agent.ai_runner import run_ai_node

        repo = src.repo_dirname
        input_json = {
            "source_path": src.workspace_path or workspace_repo_path(repo),
            "hints": hints,
        }
        output = await run_ai_node(
            node_key="profile",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            timeout_seconds=600,
            task_id=ctx.task_id,
        )
        merged = merge_profile(output, hints)
        facts = require_is_web(sanitize_profile(merged))
        await _persist_profile(ctx, commit_sha, facts)
        return facts
