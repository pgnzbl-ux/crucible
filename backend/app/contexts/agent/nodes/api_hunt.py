"""api_hunt 节点 — 对确定性清单中的 PVE 做鉴权/逻辑嫌疑猎洞。

仅写 engine=api_hunt 的 ScanRun + RawFinding。AlertGroup / Adjudication / LeadRun
由后续统一 cluster → screen → triage → dispatch 负责。
失败隔离：ok=false 仍 completed，不抹杀其它发现。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .base import NodeContext, emit_phase, task_run_cancelled

logger = logging.getLogger(__name__)

_NODE_SKILLS_ROOT = (
    Path(__file__).resolve().parents[5]
    / "backend"
    / "agent-runner"
    / "node-skills"
)
_API_HUNT_STACKS = _NODE_SKILLS_ROOT / "api_hunt" / "stacks"


def _profile_langs_fws(profile) -> tuple[list[str], list[str]]:
    langs: list[str] = []
    fws: list[str] = []
    if profile is None:
        return langs, fws
    for fact in getattr(profile, "languages", None) or []:
        lid = fact.get("id") if isinstance(fact, dict) else getattr(fact, "id", None)
        if lid:
            langs.append(str(lid).lower())
    primary = getattr(profile, "primary_language", None) or getattr(profile, "language", None)
    if primary:
        langs.append(str(primary).lower())
    for item in getattr(profile, "frameworks", None) or []:
        fws.append(str(item).lower())
    single = getattr(profile, "framework", None)
    if single:
        fws.append(str(single).lower())
    return list(dict.fromkeys(langs)), list(dict.fromkeys(fws))


def _load_stack_notes(langs: list[str], fws: list[str]) -> str:
    """读取 api_hunt/stacks/<lang>/<fw>.md；框架未命中时回退 <lang>/_default.md。"""
    from app.contexts.agent.stacks.registry import canonicalize_framework, canonicalize_language

    chunks: list[str] = []
    for lang in langs:
        canon_lang = canonicalize_language(lang)
        matched = False
        for fw in fws:
            canon_fw = canonicalize_framework(fw)
            path = _API_HUNT_STACKS / canon_lang / f"{canon_fw}.md"
            if not path.is_file():
                continue
            try:
                text = path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if text:
                matched = True
                chunks.append(f"## stack {canon_lang}/{canon_fw}\n\n{text}")
        if not matched:
            # 画像没识别出框架（或该框架暂无笔记）时，退语言级通用提示
            fallback = _API_HUNT_STACKS / canon_lang / "_default.md"
            try:
                text = fallback.read_text(encoding="utf-8").strip() if fallback.is_file() else ""
            except OSError:
                text = ""
            if text:
                chunks.append(f"## stack {canon_lang}\n\n{text}")
    return "\n\n".join(chunks)


def _confidence_label(score: float) -> str:
    if score >= 0.85:
        return "HIGH"
    if score >= 0.6:
        return "MEDIUM"
    return "LOW"


def _candidate_evidence_state(candidate: dict[str, Any]) -> str:
    """候选证据完整度；只是发现层标签，不是漏洞判决。"""
    if (
        candidate.get("attacker_controlled") is False
        or candidate.get("reaches_sink") is False
        or candidate.get("sanitizer") == "effective"
    ):
        return "contradicted"
    if (
        candidate.get("attacker_controlled") is True
        and candidate.get("reaches_sink") is True
        and candidate.get("sanitizer") in ("none", "bypassable")
    ):
        return "supported"
    return "uncertain"


def _normalize_evidence(
    evidence: Any, *, file_path: str, line_start: int | None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(evidence, list):
        return out
    for item in evidence:
        if isinstance(item, dict):
            file_v = item.get("file") or item.get("file_path") or file_path
            lines = item.get("lines")
            if lines is None and line_start is not None:
                lines = str(line_start)
            out.append({"file": str(file_v), "lines": str(lines or "")})
        elif isinstance(item, str) and item.strip():
            out.append({
                "file": file_path,
                "lines": str(line_start) if line_start is not None else "",
                "note": item.strip(),
            })
    return out


class ApiHuntNode:
    node_key = "api_hunt"

    @property
    def is_ai(self) -> bool:
        return True

    def _resolve_input(self, ctx: NodeContext, node_input):
        from app.contexts.agent.contracts import InputAssembler

        if node_input is not None:
            return node_input
        return InputAssembler.from_previous_outputs(
            "api_hunt",
            ctx.previous_outputs,
            host_workdir=ctx.host_workdir,
            source_path=ctx.source_path,
        )

    async def execute(self, ctx: NodeContext, node_input=None) -> dict[str, Any]:
        from app.contexts.agent.ai_runner import _normalize_hunt_confidence
        from app.contexts.agent.api_inventory import (
            group_by_resource_key,
            prioritize_pve,
        )
        from app.contexts.discovery.service import DiscoveryService
        from app.contexts.finding.sarif import fingerprint
        from app.core.config import get_settings

        inp = self._resolve_input(ctx, node_input)
        ctx.node_input = inp
        settings = get_settings()
        disc = DiscoveryService(ctx.db_session)
        scan_run = await disc.start_scan_run(
            task_id=ctx.task_id,
            run_id=ctx.run_id,
            node_run_id=getattr(ctx, "node_run_id", None) or "",
            engine="api_hunt",
            config_summary={
                "top_k": int(getattr(settings, "api_hunt_top_k", 20) or 20),
                "max_batches": int(getattr(settings, "api_hunt_max_batches", 8) or 8),
            },
        )
        await ctx.db_session.commit()

        async def finish_scan(status: str, finding_count: int = 0) -> None:
            await disc.finish_scan_run(
                scan_run, status=status, finding_count=finding_count,
            )
            await ctx.db_session.commit()

        empty = {
            "engine": "api_hunt",
            "scan_run_id": scan_run.id,
            "ok": True,
            "reviewed_count": 0,
            "suspect_count": 0,
            "finding_count": 0,
            "candidate_count": 0,
            "candidate_state_counts": {},
            "budget_exhausted": False,
        }

        if not getattr(settings, "api_hunt_enabled", True):
            emit_phase(ctx, "API 猎洞未启用，已跳过", phase=self.node_key)
            await finish_scan("skipped")
            return {**empty, "status": "skipped", "skipped": True}

        inventory = getattr(inp, "inventory", None)
        bom_path = getattr(inventory, "bom_path", None) if inventory else None
        if not bom_path or not getattr(inventory, "ok", True):
            emit_phase(ctx, "无可用 API 清单，猎洞空跑", phase=self.node_key)
            await finish_scan("skipped")
            return {**empty, "status": "skipped"}

        bom_file = Path(ctx.host_workdir) / str(bom_path)
        try:
            bom = json.loads(bom_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            emit_phase(ctx, f"读取 BOM 失败：{e}", phase=self.node_key)
            await finish_scan("failed")
            return {
                **empty,
                "status": "failed",
                "ok": False,
                "error": str(e),
            }

        endpoints = list(bom.get("endpoints") or [])
        top_k = int(getattr(settings, "api_hunt_top_k", 20) or 20)
        max_batches = int(getattr(settings, "api_hunt_max_batches", 8) or 8)
        pve = prioritize_pve(endpoints, top_k=top_k)
        batches = group_by_resource_key(pve)[:max_batches]

        if not batches:
            emit_phase(ctx, "无 PVE 候选，猎洞空跑", phase=self.node_key)
            await finish_scan("completed")
            return {**empty, "status": "completed"}

        profile = getattr(inp, "profile", None)
        langs, fws = _profile_langs_fws(profile)
        stack_label = "+".join(langs + ([f"fw:{','.join(fws)}"] if fws else [])) or "unknown"
        stack_notes = _load_stack_notes(langs, fws)

        emit_phase(
            ctx,
            (
                f"猎洞启动：{len(pve)} PVE / {len(batches)} 资源批（上限 {max_batches}）"
                f" · stack={stack_label}"
            ),
            phase=self.node_key,
        )

        reviewed = 0
        suspects: list[dict[str, Any]] = []
        budget_exhausted = False

        try:
            for batch in batches:
                if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
                    break
                batch_out = await self._hunt_batch(
                    ctx, batch, settings, stack_notes=stack_notes,
                )
                reviewed += int(batch_out.get("reviewed_count") or len(batch))
                suspects.extend(batch_out.get("suspects") or [])
                if batch_out.get("budget_exhausted"):
                    budget_exhausted = True
                    break
        except Exception as e:  # noqa: BLE001 — 失败隔离
            logger.warning("api_hunt 失败(隔离): %s", e, exc_info=True)
            emit_phase(ctx, f"猎洞失败：{str(e)[:200]}", phase=self.node_key)
            await finish_scan("failed")
            return {
                **empty,
                "status": "failed",
                "ok": False,
                "reviewed_count": reviewed,
                "suspect_count": len(suspects),
                "budget_exhausted": budget_exhausted,
                "error": str(e),
            }

        findings: list[dict[str, Any]] = []
        for s in suspects:
            cwe = str(s.get("cwe") or "CWE-863")
            file_path = str(s.get("file_path") or "").replace("\\", "/")
            line_start = s.get("line_start")
            if isinstance(line_start, str) and line_start.isdigit():
                line_start = int(line_start)
            if not isinstance(line_start, int):
                line_start = None
            rule_id = str(s.get("evidence_kind") or "missing_ownership_check")
            endpoint_id = str(s.get("endpoint_id") or "")
            msg = f"API 鉴权/逻辑嫌疑：{endpoint_id}".strip()
            why = [str(x).strip() for x in (s.get("why") or []) if str(x).strip()]
            if why:
                msg = f"{msg} — {why[0]}"
            conf = _normalize_hunt_confidence(s.get("confidence"))
            evidence = _normalize_evidence(
                s.get("evidence"), file_path=file_path, line_start=line_start,
            )
            if not evidence or not why:
                continue
            evidence_state = _candidate_evidence_state(s)
            fp = fingerprint(
                "api_hunt", f"{rule_id}|{endpoint_id}", file_path, line_start, cwe,
            )
            raw = {
                "confidence": _confidence_label(conf) if conf is not None else "UNKNOWN",
                "confidence_score": conf,
                "category": "security",
                "has_dataflow": False,
                "endpoint_id": endpoint_id,
                "method": s.get("method"),
                "path_template": s.get("path_template"),
                "resource_key": s.get("resource_key"),
                "auth_observed": s.get("auth_observed") or [],
                "owasp_api": s.get("owasp_api") or "API1",
                "evidence_kind": rule_id,
                "hunt_verdict": "candidate",
                "candidate_evidence_state": evidence_state,
                "why": why,
                "evidence": evidence,
                "summary": str(s.get("summary") or "").strip() or None,
                "reasoning": str(s.get("reasoning") or "").strip() or None,
                "qualify": {
                    "attacker_controlled": s.get("attacker_controlled"),
                    "reaches_sink": s.get("reaches_sink"),
                    "sanitizer": s.get("sanitizer"),
                },
            }
            findings.append({
                "engine": "api_hunt",
                "rule_id": rule_id,
                "cwe": cwe,
                "severity": "warning",
                "file_path": file_path,
                "line_start": line_start,
                "line_end": line_start,
                "function_symbol": s.get("function_symbol"),
                "message": msg[:2000],
                "code_snippet": None,
                "fingerprint": fp,
                "source_to_sink": s.get("source_to_sink") or None,
                "raw": raw,
            })

        finding_count = 0
        state_counts: dict[str, int] = {}
        for finding in findings:
            state = str((finding.get("raw") or {}).get("candidate_evidence_state") or "uncertain")
            state_counts[state] = state_counts.get(state, 0) + 1
        if findings:
            for f in findings:
                sym = f.get("function_symbol")
                if sym:
                    raw = dict(f.get("raw") or {})
                    raw["function_symbol"] = sym
                    f["raw"] = raw
            finding_count = await disc.upsert_raw_findings(
                task_id=ctx.task_id,
                scan_run_id=scan_run.id,
                findings=findings,
            )
        await finish_scan("completed", finding_count)

        emit_phase(
            ctx,
            (
                f"猎洞完成：审 {reviewed} · 嫌疑 {len(suspects)} · 入库 {finding_count}"
                f" · 候选 {finding_count}"
            ),
            phase=self.node_key,
        )
        return {
            "engine": "api_hunt",
            "scan_run_id": scan_run.id,
            "status": "completed",
            "ok": True,
            "reviewed_count": reviewed,
            "suspect_count": len(suspects),
            "finding_count": finding_count,
            "candidate_count": finding_count,
            "candidate_state_counts": state_counts,
            "budget_exhausted": budget_exhausted,
        }

    async def _hunt_batch(
        self,
        ctx: NodeContext,
        batch: list[dict[str, Any]],
        settings,
        *,
        stack_notes: str = "",
    ) -> dict[str, Any]:
        """单资源批：Docker AI；SDK 关闭时 mock 空嫌疑。"""
        from app.contexts.agent.ai_runner import run_ai_node

        def _ep_payload(e: dict[str, Any]) -> dict[str, Any]:
            row: dict[str, Any] = {
                "endpoint_id": e.get("endpoint_id"),
                "method": e.get("method"),
                "path_template": e.get("path_template"),
                "handler_file": e.get("handler_file"),
                "handler_symbol": e.get("handler_symbol"),
                "line_start": e.get("line_start"),
                "id_params": e.get("id_params") or [],
                "auth_observed": e.get("auth_observed") or [],
                "resource_key": e.get("resource_key"),
                "has_object_id": e.get("has_object_id"),
            }
            if e.get("parser"):
                row["parser"] = e.get("parser")
            if e.get("route_file"):
                row["route_file"] = e.get("route_file")
            return row

        payload = {
            "batch": [_ep_payload(e) for e in batch],
            "closed_questions": [
                "对象 id / 路径参数 / 角色是否可由攻击者控制？未知写 null。",
                "未做 ownership/租户校验前是否已读写资源或执行特权操作？未知写 null。",
                "鉴权强度：none / bypassable / effective / unknown？",
            ],
            "rubric_hint": (
                "CWE-639/CWE-863/API1/API5；候选必须可定位并带 why/evidence；"
                "安全判断允许 false/null/unknown，真假交由后续 triage"
            ),
        }
        input_json: dict[str, Any] = {
            "endpoints": payload["batch"],
            "closed_questions": payload["closed_questions"],
            "rubric_hint": payload["rubric_hint"],
        }
        if stack_notes:
            input_json["stack_notes"] = stack_notes
        emit_phase(
            ctx,
            f"审资源批 {batch[0].get('resource_key', '')[:8]}…（{len(batch)} 端点）",
            phase=self.node_key,
        )
        meta: dict[str, Any] = {}
        try:
            output = await run_ai_node(
                node_key="api_hunt",
                input_json=input_json,
                host_workdir=ctx.host_workdir,
                runner_env=ctx.runner_env or {},
                on_event=ctx.on_event,
                task_id=ctx.task_id,
                validate=True,
                meta_out=meta,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("api_hunt batch 失败，本批跳过: %s", e)
            return {
                "reviewed_count": len(batch),
                "suspects": [],
                "budget_exhausted": False,
            }
        from app.contexts.agent.usage_ledger import record_node_usage

        await record_node_usage(ctx, "api_hunt", meta)

        suspects = list(output.get("suspects") or [])
        by_ep = {e.get("endpoint_id"): e for e in batch}
        normalized: list[dict[str, Any]] = []
        for s in suspects:
            if not isinstance(s, dict):
                continue
            ep_id = s.get("endpoint_id")
            base = by_ep.get(ep_id) or {}
            file_path = s.get("file_path") or base.get("handler_file")
            if not file_path:
                continue
            normalized.append({
                **s,
                "file_path": file_path,
                "function_symbol": s.get("function_symbol") or base.get("handler_symbol"),
                "line_start": s.get("line_start") or base.get("line_start"),
                "method": s.get("method") or base.get("method"),
                "path_template": s.get("path_template") or base.get("path_template"),
                "resource_key": s.get("resource_key") or base.get("resource_key"),
                "auth_observed": s.get("auth_observed") or base.get("auth_observed") or [],
                "endpoint_id": ep_id or base.get("endpoint_id"),
            })
        return {
            "reviewed_count": int(output.get("reviewed_count") or len(batch)),
            "suspects": normalized,
            "budget_exhausted": bool(output.get("budget_exhausted")),
        }
