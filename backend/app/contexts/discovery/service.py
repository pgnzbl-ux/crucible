"""discovery context service — ScanRun 生命周期 + RawFinding 幂等落库。"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.discovery.models import ScanRun
from app.contexts.discovery.repository import DiscoveryRepository
from app.contexts.finding.models import RawFinding


class DiscoveryService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.repo = DiscoveryRepository(session)

    async def start_scan_run(
        self, *, task_id: str, run_id: str, node_run_id: str, engine: str,
        config_summary: dict[str, Any],
    ) -> ScanRun:
        return await self.repo.create_scan_run(
            ScanRun(
                task_id=task_id, run_id=run_id, node_run_id=node_run_id, engine=engine,
                status="running", config_summary=config_summary,
                started_at=datetime.now(timezone.utc),
            )
        )

    async def finish_scan_run(
        self, scan_run: ScanRun, *, status: str, finding_count: int = 0,
        sarif_key: str | None = None, error: str | None = None,
    ) -> ScanRun:
        scan_run.status = status
        scan_run.finding_count = finding_count
        scan_run.sarif_key = sarif_key
        scan_run.error = error[:32_000] if error else None
        scan_run.finished_at = datetime.now(timezone.utc)
        await self.session.flush()
        return scan_run

    async def upsert_raw_findings(
        self, *, task_id: str, scan_run_id: str, findings: list[dict[str, Any]],
    ) -> int:
        """按 (task_id, fingerprint) 幂等落库；重跑不重复(WP2 DoD)。

        并发/重投场景靠唯一约束兜底：撞 uq_raw_findings_task_fp 的行跳过。
        """
        if not findings:
            return 0
        fps = {f["fingerprint"] for f in findings}
        result = await self.session.execute(
            select(RawFinding.fingerprint).where(
                RawFinding.task_id == task_id, RawFinding.fingerprint.in_(fps)
            )
        )
        existing = set(result.scalars().all())
        new_rows: list[RawFinding] = []
        for f in findings:
            if f["fingerprint"] in existing:
                continue
            new_rows.append(
                RawFinding(
                    task_id=task_id,
                    scan_run_id=scan_run_id,
                    engine=f["engine"], rule_id=f["rule_id"], cwe=f.get("cwe"),
                    severity=f.get("severity"), file_path=f["file_path"],
                    line_start=f.get("line_start"), line_end=f.get("line_end"),
                    message=f.get("message") or "", source_to_sink=f.get("source_to_sink"),
                    code_snippet=f.get("code_snippet"), fingerprint=f["fingerprint"],
                    raw=f.get("raw") or {},
                )
            )
            existing.add(f["fingerprint"])
        if not new_rows:
            return 0
        try:
            # 行在 savepoint 内 add：撞唯一约束时回滚自动逐出对象，
            # 外层未提交状态(如事件行)原样保留——不得整段 rollback
            async with self.session.begin_nested():
                self.session.add_all(new_rows)
                await self.session.flush()
            return len(new_rows)
        except IntegrityError:
            # 并发撞唯一约束：复查真实存在的指纹，只补插缺失的行
            # (与 upsert_groups 的恢复路径同模式)
            result = await self.session.execute(
                select(RawFinding.fingerprint).where(
                    RawFinding.task_id == task_id, RawFinding.fingerprint.in_(fps)
                )
            )
            existing = set(result.scalars().all())
            retry_rows = [r for r in new_rows if r.fingerprint not in existing]
            if retry_rows:
                self.session.add_all(retry_rows)
                await self.session.flush()
            return len(retry_rows)

    async def archive_sarif(self, *, scan_run: ScanRun, payload: str) -> str | None:
        """原始 SARIF 归档 MinIO crucible-task 桶(私有，可含未脱敏原文)。best-effort。"""
        try:
            from app.shared.object_store import get_object_store

            key = f"node_run/scan-sarif/{scan_run.id}.json"
            get_object_store().put_at(
                "node_run", key,
                json.dumps(
                    {"engine": scan_run.engine, "payload": payload}, ensure_ascii=False
                ).encode("utf-8"),
                content_type="application/json",
            )
            return key
        except Exception:  # noqa: BLE001 — 归档失败不阻塞扫描
            return None

    async def get_scan_runs(self, run_id: str) -> list[ScanRun]:
        return await self.repo.list_scan_runs(run_id)
