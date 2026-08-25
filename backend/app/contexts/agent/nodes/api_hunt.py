"""api_hunt 节点 — 对确定性清单中的 PVE 做鉴权/逻辑嫌疑猎洞。

写 engine=api_hunt RawFinding 并 upsert AlertGroup；嫌疑项对齐 §2.7 合格门后
直接落 agent 判决（via=api_hunt），绕过 screen/triage。
失败隔离：ok=false 仍 completed，不抹杀 cluster。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .base import NodeContext, emit_phase, task_run_cancelled

logger = logging.getLogger(__name__)


def _confidence_label(score: float) -> str:
    if score >= 0.85:
        return "HIGH"
    if score >= 0.6:
        return "MEDIUM"
    return "LOW"


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


async def _adjudicate_hunt_groups(
    svc, *, task_id: str, high_confidence: float = 0.8,
) -> int:
    """把仍为 clustered 且代表为 api_hunt 的组写成 §2.7 全部门对齐的 agent 判决。

    含 conf >= triage_high_confidence，与 dispatch 合格门一致；MEDIUM 只入库不直出。
    """
    from app.contexts.agent.ai_runner import _normalize_hunt_confidence
    from app.contexts.finding.models import Adjudication

    qualified = 0
    groups = await svc.list_groups(task_id, status="clustered")
    for group in groups:
        if "api_hunt" not in (group.engine_set or []):
            continue
        rep = await svc.representative_of(group)
        if rep is None or (rep.engine or "") != "api_hunt":
            continue
        raw = rep.raw if isinstance(rep.raw, dict) else {}
        qualify = raw.get("qualify") if isinstance(raw.get("qualify"), dict) else {}
        why = list(raw.get("why") or [])
        evidence = list(raw.get("evidence") or [])
        conf = _normalize_hunt_confidence(raw.get("confidence_score", raw.get("confidence")))
        if conf is None or conf < high_confidence:
            continue
        if (
            not why
            or not evidence
            or qualify.get("attacker_controlled") is not True
            or qualify.get("reaches_sink") is not True
            or qualify.get("sanitizer") not in ("none", "bypassable")
        ):
            continue
        group.verdict_source = "agent"
        await svc.record_adjudication(
            group=group,
            adjudication=Adjudication(
                alert_group_id=group.id,
                attempt=1,
                provider_id=None,
                model=None,
                verdict="tp",
                confidence=conf,
                why=why,
                evidence=evidence,
                need=[],
                context_log=[{
                    "round": 1,
                    "via": "api_hunt",
                    "tier": "api_hunt",
                    "qualify": {
                        "attacker_controlled": True,
                        "reaches_sink": True,
                        "sanitizer": qualify.get("sanitizer"),
                    },
                    "endpoint_id": raw.get("endpoint_id"),
                }],
                prompt_text="[api_hunt] 猎洞节点直出合格门判决，未走 screen/triage",
                response_text=json.dumps(
                    {
                        "endpoint_id": raw.get("endpoint_id"),
                        "why": why,
                        "qualify": qualify,
                    },
                    ensure_ascii=False,
                    default=str,
                )[:20000],
                usage={},
            ),
        )
        qualified += 1
    return qualified


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
        from app.contexts.agent.api_inventory import (
            group_by_resource_key,
            prioritize_pve,
        )
        from app.contexts.agent.ai_runner import _normalize_hunt_confidence
        from app.contexts.discovery.service import DiscoveryService
        from app.contexts.finding.clustering import cluster_findings
        from app.contexts.finding.sarif import fingerprint
        from app.contexts.finding.service import FindingService
        from app.core.config import get_settings

        inp = self._resolve_input(ctx, node_input)
        ctx.node_input = inp
        settings = get_settings()

        empty = {
            "ok": True,
            "reviewed_count": 0,
            "suspect_count": 0,
            "finding_count": 0,
            "qualified_count": 0,
            "budget_exhausted": False,
        }

        if not getattr(settings, "api_hunt_enabled", True):
            emit_phase(ctx, "API 猎洞未启用，已跳过", phase=self.node_key)
            return {**empty, "skipped": True}

        inventory = getattr(inp, "inventory", None)
        bom_path = getattr(inventory, "bom_path", None) if inventory else None
        if not bom_path or not getattr(inventory, "ok", True):
            emit_phase(ctx, "无可用 API 清单，猎洞空跑", phase=self.node_key)
            return empty

        bom_file = Path(ctx.host_workdir) / str(bom_path)
        try:
            bom = json.loads(bom_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            emit_phase(ctx, f"读取 BOM 失败：{e}", phase=self.node_key)
            return {
                "ok": False,
                "reviewed_count": 0,
                "suspect_count": 0,
                "finding_count": 0,
                "qualified_count": 0,
                "budget_exhausted": False,
                "error": str(e),
            }

        endpoints = list(bom.get("endpoints") or [])
        top_k = int(getattr(settings, "api_hunt_top_k", 20) or 20)
        max_batches = int(getattr(settings, "api_hunt_max_batches", 8) or 8)
        pve = prioritize_pve(endpoints, top_k=top_k)
        batches = group_by_resource_key(pve)[:max_batches]

        if not batches:
            emit_phase(ctx, "无 PVE 候选，猎洞空跑", phase=self.node_key)
            return empty

        svc = FindingService(ctx.db_session)

        emit_phase(
            ctx,
            f"猎洞启动：{len(pve)} PVE / {len(batches)} 资源批（上限 {max_batches}）",
            phase=self.node_key,
        )

        reviewed = 0
        suspects: list[dict[str, Any]] = []
        budget_exhausted = False

        try:
            for batch in batches:
                if await task_run_cancelled(ctx.db_session, ctx.task_id, ctx.run_id):
                    break
                batch_out = await self._hunt_batch(ctx, batch, settings)
                reviewed += int(batch_out.get("reviewed_count") or len(batch))
                suspects.extend(batch_out.get("suspects") or [])
                if batch_out.get("budget_exhausted"):
                    budget_exhausted = True
                    break
        except Exception as e:  # noqa: BLE001 — 失败隔离
            logger.warning("api_hunt 失败(隔离): %s", e, exc_info=True)
            emit_phase(ctx, f"猎洞失败：{str(e)[:200]}", phase=self.node_key)
            return {
                "ok": False,
                "reviewed_count": reviewed,
                "suspect_count": len(suspects),
                "finding_count": 0,
                "qualified_count": 0,
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
            if conf is None:
                continue
            evidence = _normalize_evidence(
                s.get("evidence"), file_path=file_path, line_start=line_start,
            )
            if not evidence or not why:
                continue
            if s.get("attacker_controlled") is not True or s.get("reaches_sink") is not True:
                continue
            if s.get("sanitizer") not in ("none", "bypassable"):
                continue
            fp = fingerprint(
                "api_hunt", f"{rule_id}|{endpoint_id}", file_path, line_start, cwe,
            )
            raw = {
                "confidence": _confidence_label(conf),
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
                "hunt_verdict": "suspect",
                "why": why,
                "evidence": evidence,
                "qualify": {
                    "attacker_controlled": True,
                    "reaches_sink": True,
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

        disc = DiscoveryService(ctx.db_session)
        finding_count = 0
        qualified_count = 0
        if findings:
            scan_run = await disc.start_scan_run(
                task_id=ctx.task_id,
                run_id=ctx.run_id,
                node_run_id=getattr(ctx, "node_run_id", None) or "",
                engine="api_hunt",
                config_summary={"top_k": top_k, "max_batches": max_batches},
            )
            await ctx.db_session.commit()
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
            await disc.finish_scan_run(
                scan_run, status="completed", finding_count=finding_count,
            )
            rows = await svc.list_findings(ctx.task_id)
            hunt_rows = [r for r in rows if r.engine == "api_hunt"]
            as_dicts = []
            for r in hunt_rows:
                raw = r.raw if isinstance(r.raw, dict) else {}
                as_dicts.append({
                    "id": r.id,
                    "engine": r.engine,
                    "rule_id": r.rule_id,
                    "cwe": r.cwe,
                    "severity": r.severity,
                    "file_path": r.file_path,
                    "line_start": r.line_start,
                    "line_end": r.line_end,
                    "function_symbol": raw.get("function_symbol"),
                    "message": r.message,
                    "fingerprint": r.fingerprint,
                    "source_to_sink": r.source_to_sink,
                    "raw": raw,
                })
            groups = cluster_findings(as_dicts, index=[])
            finding_by_id = {r.id: r for r in hunt_rows}
            await svc.upsert_groups(
                task_id=ctx.task_id, groups=groups, finding_by_id=finding_by_id,
            )
            qualified_count = await _adjudicate_hunt_groups(
                svc,
                task_id=ctx.task_id,
                high_confidence=float(settings.triage_high_confidence),
            )
            await ctx.db_session.commit()

        emit_phase(
            ctx,
            (
                f"猎洞完成：审 {reviewed} · 嫌疑 {len(suspects)} · 入库 {finding_count}"
                f" · 合格直出 {qualified_count}"
            ),
            phase=self.node_key,
        )
        return {
            "ok": True,
            "reviewed_count": reviewed,
            "suspect_count": len(suspects),
            "finding_count": finding_count,
            "qualified_count": qualified_count,
            "budget_exhausted": budget_exhausted,
        }

    async def _hunt_batch(
        self,
        ctx: NodeContext,
        batch: list[dict[str, Any]],
        settings,
    ) -> dict[str, Any]:
        """单资源批：Docker AI；SDK 关闭时 mock 空嫌疑。"""
        from app.contexts.agent.ai_runner import run_ai_node

        payload = {
            "batch": [
                {
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
                for e in batch
            ],
            "closed_questions": [
                "对象 id / 路径参数 / 角色是否可由攻击者控制？（attacker_controlled）",
                "未做 ownership/租户校验前是否已读写资源或执行特权操作？（reaches_sink）",
                "鉴权强度：none / bypassable / effective？（effective 不得报嫌疑）",
            ],
            "rubric_hint": (
                "CWE-639/CWE-863/API1/API5；嫌疑必须带 why/evidence/"
                "attacker_controlled/reaches_sink/sanitizer/confidence"
            ),
        }
        input_json = {
            "endpoints": payload["batch"],
            "closed_questions": payload["closed_questions"],
            "rubric_hint": payload["rubric_hint"],
        }
        emit_phase(
            ctx,
            f"审资源批 {batch[0].get('resource_key', '')[:8]}…（{len(batch)} 端点）",
            phase=self.node_key,
        )
        try:
            output = await run_ai_node(
                node_key="api_hunt",
                input_json=input_json,
                host_workdir=ctx.host_workdir,
                runner_env=ctx.runner_env or {},
                on_event=ctx.on_event,
                task_id=ctx.task_id,
                validate=True,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("api_hunt batch 失败，本批跳过: %s", e)
            return {
                "reviewed_count": len(batch),
                "suspects": [],
                "budget_exhausted": False,
            }

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
