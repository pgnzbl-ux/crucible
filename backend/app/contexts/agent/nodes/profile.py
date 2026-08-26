"""节点 1 项目画像 — 规则引擎产 hints；SDK 开启且无缓存时一律轻度 AI。

discovery-spec §6.0：画像升为权威结构化契约。
- languages[] 多事实，AI 只能追加 source=ai 低置信项，不得覆盖文件证据；
- semgrep_configs / primary_language 由纯函数派生，AI 输出里出现也一律重算；
- 强 Web / 强非 Web 时规则画像直接落库，AI 不出关键路径；
- 旧缓存缺派生字段时重算补齐，不整份作废；
- 同 SHA 缓存命中跳过 AI；SDK 关闭时规则结果直接落库。
"""
from __future__ import annotations

import asyncio
from typing import Any

from app.contexts.agent.contracts import InputAssembler, ProfileInput, SourceHandoff
from app.contexts.agent.profile_detector import (
    append_ai_language,
    derive_primary_language,
    derive_semgrep_configs,
    detect_profile,
    profile_needs_ai,
)

from .base import NodeContext, emit_phase, workspace_repo_path

# 权威事实：只列一份。language/framework/primary_language/semgrep_configs 是派生别名，
# 由 rebuild_derived_fields 写死，禁止当独立字段填。
_HINT_FILL_KEYS = (
    "languages",
    "frameworks",
    "package_managers",
    "osv_manifests",
    "port",
    "has_dockerfile",
    "has_compose",
    "detected_services",
)

PROFILE_FACT_KEYS = (
    "is_web",
    "languages",
    "frameworks",
    "package_managers",
    "osv_manifests",
    "port",
    "has_dockerfile",
    "has_compose",
    "detected_services",
    "start_command",
    "non_web_reason",
    "profile_source",
)

# 旧缓存/旧 AI 输出才有的单数字段，sanitize 时读入再交给 rebuild 升级
_LEGACY_ALIAS_KEYS = ("language", "framework")

# AI 不得写死的派生字段：出现即丢弃、一律重算（language 只作 append_ai_language 的输入）
_AI_FORBIDDEN_KEYS = frozenset({"languages", "primary_language", "semgrep_configs", "language"})


def coerce_is_web(value: Any) -> bool | None:
    """只接受 JSON/Python 布尔；拒绝 "false" 这类会被 bool() 判真的字符串。"""
    if isinstance(value, bool):
        return value
    return None


def rebuild_derived_fields(profile: dict[str, Any]) -> dict[str, Any]:
    """从 languages 重算主语言/兼容字段/semgrep_configs；frameworks 与 framework 互相同步。"""
    languages = profile.get("languages") or []
    primary = derive_primary_language(languages)
    profile["primary_language"] = primary
    profile["language"] = primary
    profile["semgrep_configs"] = derive_semgrep_configs(languages)
    frameworks = profile.get("frameworks")
    if not frameworks and profile.get("framework"):
        frameworks = [profile["framework"]]
    profile["frameworks"] = frameworks or []
    profile["framework"] = frameworks[0] if frameworks else None
    # 契约形状稳定：缺省的清单字段补空(缓存路径无仓库可重扫，空 = scan_osv 整仓扫)
    if "package_managers" not in profile:
        profile["package_managers"] = []
    if "osv_manifests" not in profile:
        profile["osv_manifests"] = []
    return profile


def upgrade_profile_facts(facts: dict[str, Any]) -> dict[str, Any]:
    """旧 schema(单 language) → 新 schema(languages[] + 派生字段)；缓存兼容入口。"""
    if not facts.get("languages"):
        legacy = facts.get("language")
        if legacy:
            facts = {
                **facts,
                "languages": [{"id": legacy, "evidence_files": [], "source": "rules", "confidence": 1.0}],
            }
    if not facts.get("profile_source"):
        facts = {**facts, "profile_source": "cache"}
    return rebuild_derived_fields(facts)


def merge_profile(ai: dict[str, Any], hints: dict[str, Any]) -> dict[str, Any]:
    """AI 产出优先；空缺由规则引擎 hints 补齐。is_web 非 bool 则保留 hints。

    语言约束(§6.0)：AI 的 language 只能以 source=ai 追加，不覆盖 rules 证据、
    不进 semgrep_configs；派生字段无论 AI 写了什么都重算。
    """
    merged = dict(hints)
    for key, value in ai.items():
        if key == "is_web" or key in _AI_FORBIDDEN_KEYS:
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
    merged["languages"] = append_ai_language(hints.get("languages") or [], ai.get("language"))
    merged["profile_source"] = "rules+ai"
    return rebuild_derived_fields(merged)


def sanitize_profile(output: dict[str, Any]) -> dict[str, Any]:
    """节点结果只留架构事实 + is_web，丢掉 README 式长文；并补齐/重算派生字段。"""
    facts: dict[str, Any] = {}
    for key in (*PROFILE_FACT_KEYS, *_LEGACY_ALIAS_KEYS):
        if key not in output:
            continue
        value = output[key]
        if key == "is_web":
            coerced = coerce_is_web(value)
            if coerced is None:
                continue
            facts[key] = coerced
            continue
        if key == "languages":
            facts[key] = [
                f for f in (value or [])
                if isinstance(f, dict) and f.get("id")
            ]
            continue
        if value is None or value == "" or value == []:
            continue
        facts[key] = value
    return upgrade_profile_facts(facts)


def require_is_web(facts: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(facts.get("is_web"), bool):
        raise RuntimeError("画像缺少显式 is_web，拒绝继续")
    return facts


def _hints_phase_message(hints: dict[str, Any]) -> str:
    primary = hints.get("primary_language") or hints.get("language") or "未知"
    framework = hints.get("framework")
    n_lang = len(hints.get("languages") or [])
    stack = f"{primary}/{framework}" if framework else str(primary)
    return f"规则扫描完成（{stack} · {n_lang} 语言）"


def _merged_phase_message(facts: dict[str, Any]) -> str:
    web = "Web" if facts.get("is_web") is True else "非 Web"
    lang = facts.get("primary_language") or facts.get("language") or "未知"
    return f"画像合并完成（{web} · {lang}）"


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
            emit_phase(ctx, "复用同 SHA 画像缓存", phase=self.node_key)
            if ctx.project_id:
                await _persist_profile(ctx, commit_sha, facts)
            return facts

        root = src.project_path or inp.source_path or ctx.source_path
        # 同步磁盘遍历放线程池：大仓库直接跑会卡住同波次并发节点的心跳/SSE
        hints = await asyncio.to_thread(detect_profile, root)
        emit_phase(ctx, _hints_phase_message(hints), phase=self.node_key)

        from app.core.config import get_settings

        settings = get_settings()
        # 强 Web / 强非 Web：规则已足够，AI 不出关键路径（降低阻塞 Semgrep/清单的成本）
        if (
            not settings.claude_agent_sdk_enabled
            or not profile_needs_ai(root, hints)
        ):
            facts = require_is_web(sanitize_profile(hints))
            if not settings.claude_agent_sdk_enabled:
                emit_phase(ctx, "SDK 关闭，采用规则画像", phase=self.node_key)
            else:
                emit_phase(ctx, "规则画像已充分，跳过 AI", phase=self.node_key)
            await _persist_profile(ctx, commit_sha, facts)
            return facts

        from app.contexts.agent.ai_runner import run_ai_node

        emit_phase(ctx, "启动轻度 AI 画像", phase=self.node_key)
        repo = src.repo_dirname
        input_json = {
            "source_path": src.workspace_path or workspace_repo_path(repo),
            "hints": hints,
        }
        profile_meta: dict[str, Any] = {}
        output = await run_ai_node(
            node_key="profile",
            input_json=input_json,
            host_workdir=ctx.host_workdir,
            runner_env=ctx.runner_env,
            on_event=ctx.on_event,
            task_id=ctx.task_id,
            meta_out=profile_meta,
        )
        from app.contexts.agent.usage_ledger import record_node_usage

        await record_node_usage(ctx, "profile", profile_meta)
        merged = merge_profile(output, hints)
        facts = require_is_web(sanitize_profile(merged))
        emit_phase(ctx, _merged_phase_message(facts), phase=self.node_key)
        await _persist_profile(ctx, commit_sha, facts)
        return facts
