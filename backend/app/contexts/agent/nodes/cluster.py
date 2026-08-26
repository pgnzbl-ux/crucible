"""cluster 节点 — 确定性降噪 + 函数索引 + 指纹分组 + grade/降权。

入口检查：四路 handoff 指向的 ScanRun 全 failed → raise「全引擎失败」。
只汇入 handoff 明确指向的 semgrep/gitleaks/osv/api_hunt RawFinding，禁止跨运行全扫。
按 profile.languages 建索引（python/java/js/ts）；产出 ClusterHandoff 计数与进度事件。
"""
from __future__ import annotations

import asyncio
from typing import Any

from .base import NodeContext, emit_phase


class ClusterNode:
    node_key = "cluster"

    @property
    def is_ai(self) -> bool:
        return False

    def _resolve_input(self, ctx: NodeContext, node_input):
        from app.contexts.agent.contracts import InputAssembler

        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "cluster",
            ctx.previous_outputs,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        from sqlalchemy import select

        from app.contexts.finding.clustering import cluster_findings
        from app.contexts.finding.context_extractor import (
            build_function_index,
            resolve_index_languages,
            save_index,
        )
        from app.contexts.finding.denoise import partition_for_cluster
        from app.contexts.finding.models import RawFinding
        from app.contexts.finding.service import FindingService

        inp = self._resolve_input(ctx, node_input)
        ctx.node_input = inp

        from app.contexts.discovery.models import ScanRun
        from app.contexts.discovery.service import DiscoveryService

        scan_handoffs = list(getattr(inp, "scans", None) or [])
        handed_ids = {
            str(scan.scan_run_id)
            for scan in scan_handoffs
            if getattr(scan, "scan_run_id", None)
        }
        missing_provenance = [
            str(getattr(scan, "engine", None) or "unknown")
            for scan in scan_handoffs
            if not getattr(scan, "scan_run_id", None)
            and int(getattr(scan, "finding_count", 0) or 0) > 0
        ]
        if missing_provenance:
            raise RuntimeError(
                "发现节点产出非零 finding 但缺少 scan_run_id："
                + ", ".join(sorted(missing_provenance))
            )

        if handed_ids:
            scan_runs = list((await ctx.db_session.execute(
                select(ScanRun).where(
                    ScanRun.id.in_(handed_ids),
                    ScanRun.task_id == ctx.task_id,
                )
            )).scalars().all())
            resolved_ids = {scan.id for scan in scan_runs}
            unresolved_ids = sorted(handed_ids - resolved_ids)
            if unresolved_ids:
                raise RuntimeError(
                    "发现 handoff 引用了无效或跨任务 ScanRun："
                    + ", ".join(unresolved_ids)
                )
        else:
            # 兼容升级前无 scan_run_id 的历史 NodeRun；新运行必须走上面的显式 handoff。
            scan_runs = await DiscoveryService(ctx.db_session).get_scan_runs(ctx.run_id)
            resolved_ids = {scan.id for scan in scan_runs}
        terminal = [s for s in scan_runs if s.status in ("completed", "failed", "skipped")]
        if scan_runs and terminal and all(s.status == "failed" for s in terminal):
            raise RuntimeError("全引擎失败：semgrep/gitleaks/osv/api_hunt 均未产出，请从发现节点重试")

        profile = getattr(inp, "profile", None)
        profile_ids: list[str] = []
        if profile is not None:
            for fact in getattr(profile, "languages", None) or []:
                lid = fact.get("id") if isinstance(fact, dict) else getattr(fact, "id", None)
                if lid:
                    profile_ids.append(str(lid))
            # 兼容仅有 primary/language 的旧缓存
            if not profile_ids:
                legacy = getattr(profile, "primary_language", None) or getattr(profile, "language", None)
                if legacy:
                    profile_ids.append(str(legacy))

        # profile 存在但 languages 为空 → 空索引（不静默全扫）；profile is None → 全语言
        if profile is None:
            index_langs = resolve_index_languages(None)
        else:
            index_langs = resolve_index_languages(profile_ids)
        emit_phase(
            ctx,
            f"构建函数索引（语言 {', '.join(index_langs) or '无'}）",
            phase=self.node_key,
        )
        repo_root = getattr(inp.source, "project_path", None) or ctx.source_path
        # 同步磁盘索引放线程池：卡事件循环会拖住同波次并发节点
        index = await asyncio.to_thread(
            build_function_index, repo_root, languages=index_langs,
        )
        index_built = bool(index)
        if index_built:
            save_index(ctx.host_workdir, index)
        emit_phase(ctx, f"索引完成：{len(index)} 个符号", phase=self.node_key)

        from app.contexts.finding.clustering import is_cluster_engine

        all_rows = []
        if resolved_ids:
            all_rows = list((await ctx.db_session.execute(
                select(RawFinding).where(
                    RawFinding.task_id == ctx.task_id,
                    RawFinding.scan_run_id.in_(resolved_ids),
                )
            )).scalars().all())
        findings = [f for f in all_rows if is_cluster_engine(f.engine)]
        skipped_unknown = len(all_rows) - len(findings)
        emit_phase(
            ctx,
            (
                f"读取本轮四路 findings {len(findings)} 条 / ScanRun {len(resolved_ids)} 个"
                + (f"（忽略未知引擎 {skipped_unknown}）" if skipped_unknown else "")
            ),
            phase=self.node_key,
        )
        dicts = [
            {
                "id": f.id, "engine": f.engine, "rule_id": f.rule_id, "cwe": f.cwe,
                "severity": f.severity, "file_path": f.file_path,
                "line_start": f.line_start, "line_end": f.line_end,
                "function_symbol": (
                    (f.raw or {}).get("function_symbol")
                    if isinstance(f.raw, dict) else None
                ),
                "message": f.message, "source_to_sink": f.source_to_sink,
                "raw": f.raw or {},
            }
            for f in findings
        ]
        keep, dropped_c, dropped_by_engine = partition_for_cluster(dicts)
        if dropped_c:
            emit_phase(
                ctx,
                f"降噪丢弃 C 档 {len(dropped_c)} 条（{' '.join(f'{k}={v}' for k, v in sorted(dropped_by_engine.items()))}）",
                phase=self.node_key,
            )
        groups = cluster_findings(keep, index)

        svc = FindingService(ctx.db_session)
        finding_by_id = {f.id: f for f in findings}
        await svc.upsert_groups(task_id=ctx.task_id, groups=groups, finding_by_id=finding_by_id)
        bypass_count = await svc.mark_bypass_groups(ctx.task_id)

        by_cwe: dict[str, int] = {}
        by_engine: dict[str, int] = {}
        by_grade: dict[str, int] = {}
        without_fn = 0
        downgraded = 0
        for g in groups:
            by_cwe[g.get("cwe") or "unknown"] = by_cwe.get(g.get("cwe") or "unknown", 0) + 1
            for e in g.get("engine_set") or []:
                by_engine[e] = by_engine.get(e, 0) + 1
            grade = g.get("clue_grade")
            grade_key = grade if grade else "bypass"
            by_grade[grade_key] = by_grade.get(grade_key, 0) + 1
            if not g.get("function_symbol") and "osv" not in (g.get("engine_set") or []):
                without_fn += 1
            if g.get("downgraded"):
                downgraded += 1

        emit_phase(
            ctx,
            (
                f"分组完成：{len(groups)} 组"
                f"（A={by_grade.get('A', 0)} B={by_grade.get('B', 0)} "
                f"F={by_grade.get('F', 0)} C={len(dropped_c)} bypass={bypass_count}）"
            ),
            phase=self.node_key,
        )
        return {
            "group_count": len(groups),
            "groups_by_cwe": by_cwe,
            "groups_by_engine": by_engine,
            "groups_by_grade": by_grade,
            "index_built": index_built,
            "index_languages": index_langs,
            "index_symbol_count": len(index),
            "finding_count": len(findings),
            "findings_without_function": without_fn,
            "bypass_count": bypass_count,
            "downgraded_count": downgraded,
            "dropped_c_count": len(dropped_c),
            "dropped_c_by_engine": dropped_by_engine,
        }
