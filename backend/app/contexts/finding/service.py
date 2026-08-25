"""finding context service — AlertGroup 状态机唯一入口(discovery-spec §5.3)。

repository 不暴露 status 直写；所有转移经这里的显式方法。
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.contexts.finding.models import Adjudication, AlertGroup, LeadRun, RawFinding, ReviewAction


def numeric_usage(usage: dict | None) -> dict[str, int]:
    """agent 侧 SDK usage 原样回传(含 server_tool_use 等嵌套 dict/str 字段)，
    只保留数值项 —— 列契约是 {token 计数}，其余进 response_text 审计链。"""
    return {
        str(k): int(v)
        for k, v in (usage or {}).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


def _sanitize_adjudication(a: Adjudication) -> Adjudication:
    """agent 自由输出收敛到判决列契约(why/need 全 str、evidence 全 dict、
    usage 全数值)，防止单条脏行打挂复核台详情读取。"""
    a.why = [str(w) for w in (a.why or [])]
    a.need = [str(n) for n in (a.need or [])]
    a.evidence = [e if isinstance(e, dict) else {"detail": str(e)} for e in (a.evidence or [])]
    a.usage = numeric_usage(a.usage)
    return a


class FindingService:
    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 查询 ──

    async def list_findings(self, task_id: str) -> list[RawFinding]:
        result = await self.session.execute(
            select(RawFinding).where(RawFinding.task_id == task_id)
        )
        return list(result.scalars().all())

    async def representative_of(self, group: AlertGroup) -> RawFinding | None:
        """组的代表 finding（dispatch/triage 组装线索用；跨 Context 唯一入口）。"""
        if not group.representative_finding_id:
            return None
        return await self.session.get(RawFinding, group.representative_finding_id)

    async def latest_adjudication(self, group_id: str) -> Adjudication | None:
        """组最近一次二审记录（按 attempt 降序取首条）。"""
        result = await self.session.execute(
            select(Adjudication)
            .where(Adjudication.alert_group_id == group_id)
            .order_by(Adjudication.attempt.desc())
        )
        rows = result.scalars().all()
        return rows[0] if rows else None

    async def reps_and_adjudications(
        self, groups: list[AlertGroup],
    ) -> tuple[dict[str, RawFinding], dict[str, Adjudication]]:
        """一批组的代表成员 + 最高 attempt 判决，避免 dispatch/streamer 2N。"""
        if not groups:
            return {}, {}
        rep_ids = [g.representative_finding_id for g in groups if g.representative_finding_id]
        group_ids = [g.id for g in groups]
        reps_by_finding: dict[str, RawFinding] = {}
        if rep_ids:
            rows = await self.session.execute(
                select(RawFinding).where(RawFinding.id.in_(rep_ids))
            )
            reps_by_finding = {f.id: f for f in rows.scalars().all()}
        reps = {
            g.id: reps_by_finding[g.representative_finding_id]
            for g in groups
            if g.representative_finding_id in reps_by_finding
        }
        latest: dict[str, Adjudication] = {}
        if group_ids:
            max_attempt = (
                select(
                    Adjudication.alert_group_id,
                    func.max(Adjudication.attempt).label("mx"),
                )
                .where(Adjudication.alert_group_id.in_(group_ids))
                .group_by(Adjudication.alert_group_id)
            ).subquery()
            adj_rows = await self.session.execute(
                select(Adjudication).join(
                    max_attempt,
                    (Adjudication.alert_group_id == max_attempt.c.alert_group_id)
                    & (Adjudication.attempt == max_attempt.c.mx),
                )
            )
            for adj in adj_rows.scalars().all():
                latest.setdefault(adj.alert_group_id, adj)
        return reps, latest

    async def list_groups(self, task_id: str, *, status: str | None = None) -> list[AlertGroup]:
        stmt = select(AlertGroup).where(AlertGroup.task_id == task_id)
        if status:
            stmt = stmt.where(AlertGroup.status == status)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_group(self, group_id: str) -> AlertGroup | None:
        return await self.session.get(AlertGroup, group_id)

    async def list_adjudications(self, group_id: str) -> list[Adjudication]:
        result = await self.session.execute(
            select(Adjudication).where(Adjudication.alert_group_id == group_id)
            .order_by(Adjudication.attempt)
        )
        return list(result.scalars().all())

    # ── cluster 侧：分组落库(幂等合并) ──

    async def upsert_groups(
        self, *, task_id: str, groups: list[dict[str, Any]],
        finding_by_id: dict[str, RawFinding],
    ) -> int:
        """按 (task_id, group_key) upsert：已存在则合并成员/引擎集，不重建。"""
        if not groups:
            return 0
        keys = [g["group_key"] for g in groups]
        result = await self.session.execute(
            select(AlertGroup).where(
                AlertGroup.task_id == task_id, AlertGroup.group_key.in_(keys)
            )
        )
        existing = {g.group_key: g for g in result.scalars().all()}
        created = 0
        for g in groups:
            rep_id = g.get("representative_finding_id")
            if not rep_id:
                continue  # 无代表成员的组不落库(代表必须指向真实 RawFinding)
            row = existing.get(g["group_key"])
            if row is None:
                row = AlertGroup(
                    task_id=task_id,
                    group_key=g["group_key"],
                    cwe=g.get("cwe"),
                    file_path=g.get("file_path") or "",
                    function_symbol=g.get("function_symbol"),
                    line_span=g.get("line_span"),
                    member_count=g.get("member_count") or 1,
                    representative_finding_id=rep_id,
                    engine_set=g.get("engine_set") or [],
                    status="clustered",
                    clue_grade=g.get("clue_grade"),
                    priority=g.get("priority"),
                )
                try:
                    async with self.session.begin_nested():
                        self.session.add(row)
                        await self.session.flush()
                    existing[g["group_key"]] = row
                    created += 1
                    self._bind_group_members(row, g, finding_by_id)
                    continue
                except IntegrityError:
                    # 并发(重投双跑)撞 uq_alert_groups_task_key：复用已有行合并，
                    # 与 _get_or_create_node_run 同模式
                    found = (await self.session.execute(
                        select(AlertGroup).where(
                            AlertGroup.task_id == task_id,
                            AlertGroup.group_key == g["group_key"],
                        )
                    )).scalar_one_or_none()
                    if found is None:
                        raise
                    row = found
                    existing[g["group_key"]] = row
            row.member_count = max(row.member_count, g.get("member_count") or 1)
            engines = set(row.engine_set or []) | set(g.get("engine_set") or [])
            row.engine_set = sorted(engines)
            row.priority = g.get("priority") or row.priority
            row.clue_grade = g.get("clue_grade") or row.clue_grade
            self._bind_group_members(row, g, finding_by_id)
        await self.session.flush()
        return created

    @staticmethod
    def _bind_group_members(
        row: AlertGroup, group: dict[str, Any], finding_by_id: dict[str, RawFinding],
    ) -> None:
        ids = list(group.get("member_ids") or [])
        rep_id = group.get("representative_finding_id") or row.representative_finding_id
        if rep_id and rep_id not in ids:
            ids.append(rep_id)
        for fid in ids:
            finding = finding_by_id.get(fid)
            if finding is not None:
                finding.alert_group_id = row.id

    async def mark_bypass_groups(self, task_id: str) -> int:
        """osv 组：clustered → adjudicated(bypass 直报，跳过 triage) + 模板叙事。"""
        from app.contexts.finding.models import Adjudication
        from app.contexts.finding.narrative import osv_template_narrative

        groups = await self.list_groups(task_id)
        n = 0
        for g in groups:
            if "osv" not in (g.engine_set or []) or g.status != "clustered":
                continue
            g.status = "adjudicated"
            g.ai_verdict = "bypass"
            g.priority = g.priority or "medium"
            g.verdict_source = g.verdict_source or "rule"
            rep = await self.representative_of(g)
            raw = rep.raw if rep and isinstance(rep.raw, dict) else {}
            summary, reasoning = osv_template_narrative(
                message=(rep.message if rep else "") or "",
                raw=raw,
                file_path=(rep.file_path if rep else g.file_path) or "",
                rule_id=(rep.rule_id if rep else "") or "",
            )
            why = [
                f"依赖情报 bypass：{raw.get('rule_id') or (rep.rule_id if rep else g.group_key)}",
            ]
            if raw.get("called") is True:
                why.append("调用分析 called=true")
            elif raw.get("called") is False:
                why.append("调用分析 called=false（默认不入终认）")
            else:
                why.append("无调用分析（默认不入终认）")
            evidence: list[dict] = []
            if rep and rep.file_path:
                lines = (
                    f"{rep.line_start}-{rep.line_end or rep.line_start}"
                    if rep.line_start is not None
                    else "1-1"
                )
                evidence.append({"file": rep.file_path, "lines": lines})
            self.session.add(Adjudication(
                alert_group_id=g.id,
                attempt=1,
                provider_id=None,
                model=None,
                verdict="bypass",
                confidence=None,
                why=why,
                evidence=evidence,
                need=[],
                summary=summary,
                reasoning=reasoning,
                context_log=[{"via": "osv_bypass", "called": raw.get("called")}],
                prompt_text="[osv] 依赖情报 bypass，确定性模板叙事，未走 triage",
                response_text=summary[:2000],
                usage={},
            ))
            n += 1
        await self.session.flush()
        return n

    async def attach_vuln_report(
        self,
        *,
        group: AlertGroup,
        lead: LeadRun,
        verification_basis: str,
    ) -> dict | None:
        """终局成功时写入一漏洞一份报告；非成功态清空。"""
        from app.contexts.finding.vuln_report import build_vuln_report, is_vuln_report_verdict

        if not is_vuln_report_verdict(lead.verdict):
            return None
        rep = await self.representative_of(group)
        adjs = await self.list_adjudications(group.id)
        adj = adjs[-1] if adjs else None
        report = build_vuln_report(
            group=group,
            lead=lead,
            representative=rep,
            adjudication=adj,
            verification_basis=verification_basis,
        )
        group.vuln_report = report
        group.verification_basis = verification_basis
        lead.verification_basis = verification_basis
        await self.session.flush()
        return report

    # ── triage 侧：判决落库 ──

    async def record_adjudication(
        self, *, group: AlertGroup, adjudication: Adjudication,
    ) -> None:
        self.session.add(_sanitize_adjudication(adjudication))
        group.status = "adjudicated"
        group.ai_verdict = adjudication.verdict
        group.ai_confidence = adjudication.confidence
        await self.session.flush()

    async def mark_unaudited_for_review(self, task_id: str) -> int:
        """未审组保持 clustered，只计数；禁止记 fp、禁止转人工主队列。"""
        groups = await self.list_groups(task_id, status="clustered")
        return len(groups)

    # ── dispatch / 复核侧 ──

    async def try_claim_dispatch(self, group_id: str) -> AlertGroup | None:
        """CAS：仅当尚未 dispatched 时抢占。并发第二次返回 None。"""
        result = await self.session.execute(
            update(AlertGroup)
            .where(AlertGroup.id == group_id, AlertGroup.status != "dispatched")
            .values(status="dispatched")
        )
        if result.rowcount == 0:
            return None
        return await self.get_group(group_id)

    async def release_dispatch_claim(self, group_id: str, *, restore_status: str) -> None:
        """投递失败时把 CAS 抢占滚回，允许稍后重试本接口。"""
        await self.session.execute(
            update(AlertGroup)
            .where(AlertGroup.id == group_id, AlertGroup.status == "dispatched")
            .values(status=restore_status)
        )
        await self.session.flush()

    async def mark_dispatched(self, group: AlertGroup) -> None:
        group.status = "dispatched"
        await self.session.flush()

    async def mark_needs_review(self, group: AlertGroup, *, priority: str | None = None) -> None:
        group.status = "needs_review"
        if priority:
            group.priority = priority
        await self.session.flush()

    async def mark_resolved(self, group: AlertGroup, resolution: str) -> None:
        """终态：confirmed | partial | code_reachable | false_positive | ignored。"""
        if resolution not in (
            "confirmed", "partial", "code_reachable", "false_positive", "ignored",
        ):
            raise ValueError(f"非法 resolution: {resolution}")
        group.status = "resolved"
        group.resolution = "confirmed" if resolution == "partial" else resolution
        await self.session.flush()

    async def discard_false_positive(self, group: AlertGroup) -> None:
        """明确误报：级联删组与判决，保留 RawFinding。线索台无对象。"""
        gid = group.id
        await self.session.execute(
            update(RawFinding).where(RawFinding.alert_group_id == gid).values(alert_group_id=None)
        )
        await self.session.execute(
            delete(Adjudication).where(Adjudication.alert_group_id == gid)
        )
        await self.session.execute(
            delete(ReviewAction).where(ReviewAction.alert_group_id == gid)
        )
        await self.session.execute(delete(LeadRun).where(LeadRun.alert_group_id == gid))
        await self.session.execute(delete(AlertGroup).where(AlertGroup.id == gid))
        await self.session.flush()

    async def discard_task_false_positives(self, task_id: str) -> int:
        """漏斗计数之后丢掉误报组，线索台无对象。"""
        n = 0
        for g in await self.list_groups(task_id):
            if g.ai_verdict == "fp" and g.status not in ("dispatched", "resolved"):
                await self.discard_false_positive(g)
                n += 1
        return n

    async def revive(self, group: AlertGroup) -> None:
        """遗留：历史误报组若仍在库，可退回 needs_review。新误报无对象。"""
        group.status = "needs_review"
        await self.session.flush()

    # ── 判决回流(§4.4)：六档 verdict → AlertGroup 终态；幂等 ──

    _CONFIRMED = ("confirmed", "partial")

    async def reconcile_from_task(self, task) -> AlertGroup | None:
        """按 Task 终态回写其溯源组。同任务终认与复核台派生走同一函数。

        - confirmed/partial → resolved(confirmed)
        - code_reachable → resolved(code_reachable)
        - false_positive → 丢弃组（漏斗计数，不展示）
        - code_smell/not_reproduced → 退回 needs_review（线索台默认不展示）
        - 任务 needs_review(status) → 组退回 needs_review
        仅 verify 任务（人工/派生终认）按 Task.verdict 回写；discovery 聚合
        任务的 verdict 会错写队列第一条组，改由 LeadWorker 按组回流。
        幂等：组已 resolved 且映射未变则 no-op。丢事件兜底走 reconcile_stale_groups。
        """
        if (getattr(task, "task_type", None) or "verify") != "verify":
            return None
        group_id = getattr(task, "source_alert_group_id", None)
        if not group_id:
            return None
        group = await self.get_group(group_id)
        if group is None:
            return None
        if task.status == "needs_review":
            if group.status == "dispatched":
                group.status = "needs_review"
                await self.session.flush()
            return group
        verdict = getattr(task, "verdict", None)
        if verdict in self._CONFIRMED:
            if not (group.status == "resolved" and group.resolution == "confirmed"):
                await self.mark_resolved(group, "confirmed")
        elif verdict == "false_positive":
            await self.discard_false_positive(group)
            return None
        elif verdict == "code_reachable":
            if not (group.status == "resolved" and group.resolution == "code_reachable"):
                await self.mark_resolved(group, "code_reachable")
        elif verdict in ("code_smell", "not_reproduced"):
            if group.status == "dispatched":
                group.status = "needs_review"
                await self.session.flush()
        return group

    async def reconcile_stale_groups(self, *, owner_id: str | None = None) -> int:
        """低频 sweeper(§4.4 丢事件兜底)：dispatched 组按关联 verify Task 的 verdict 补写。"""
        from sqlalchemy import select

        from app.contexts.task.models import Task

        stmt = (
            select(AlertGroup, Task)
            .join(Task, Task.source_alert_group_id == AlertGroup.id)
            .where(
                AlertGroup.status == "dispatched",
                Task.task_type == "verify",
                Task.status.in_(("completed", "needs_review", "failed")),
            )
        )
        if owner_id:
            stmt = stmt.where(Task.owner_id == owner_id)
        rows = (await self.session.execute(stmt)).all()
        fixed = 0
        for group, task in rows:
            before = group.status
            await self.reconcile_from_task(task)
            if group.status != before:
                fixed += 1
        if fixed:
            await self.session.flush()
        return fixed

    async def record_review_action(
        self, *, group_id: str, user_id: str, action: str,
        reason_tags: list[str] | None = None, reason_text: str | None = None,
    ) -> ReviewAction:
        action_row = ReviewAction(
            alert_group_id=group_id, user_id=user_id, action=action,
            reason_tags=reason_tags or [], reason_text=reason_text,
        )
        self.session.add(action_row)
        await self.session.flush()
        return action_row

    async def delete_groups(
        self, group_ids: list[str], *, owner_id: str,
    ) -> tuple[list[str], list[dict[str, str]]]:
        """物理删除告警组（级联判决/复核/LeadRun；保留 RawFinding 与验证 Task）。

        终认进行中（LeadRun queued/running，或溯源验证 Task 仍 pending/queued/running）
        的组跳过，避免孤儿写回。跨 Context 只清逻辑指针 source_alert_group_id。
        """
        from app.contexts.task.models import Task

        unique_ids = list(dict.fromkeys(group_ids))
        if not unique_ids:
            return [], []

        owned_rows = await self.session.execute(
            select(AlertGroup.id)
            .join(Task, Task.id == AlertGroup.task_id)
            .where(AlertGroup.id.in_(unique_ids), Task.owner_id == owner_id)
        )
        owned = list(owned_rows.scalars().all())
        owned_set = set(owned)
        skipped: list[dict[str, str]] = [
            {"id": gid, "reason": "not_found"}
            for gid in unique_ids
            if gid not in owned_set
        ]
        if not owned:
            return [], skipped

        active_leads = await self.session.execute(
            select(LeadRun.alert_group_id).where(
                LeadRun.alert_group_id.in_(owned),
                LeadRun.status.in_(("queued", "running")),
            ).distinct()
        )
        blocked = set(active_leads.scalars().all())

        active_verify = await self.session.execute(
            select(Task.source_alert_group_id).where(
                Task.source_alert_group_id.in_(owned),
                Task.status.in_(("pending", "queued", "running")),
            ).distinct()
        )
        blocked.update(g for g in active_verify.scalars().all() if g)

        deletable = [gid for gid in owned if gid not in blocked]
        for gid in owned:
            if gid in blocked:
                skipped.append({"id": gid, "reason": "in_progress"})

        if not deletable:
            return [], skipped

        await self.session.execute(
            update(Task)
            .where(Task.source_alert_group_id.in_(deletable))
            .values(source_alert_group_id=None)
        )
        await self.session.execute(
            update(RawFinding)
            .where(RawFinding.alert_group_id.in_(deletable))
            .values(alert_group_id=None)
        )
        await self.session.execute(
            delete(Adjudication).where(Adjudication.alert_group_id.in_(deletable))
        )
        await self.session.execute(
            delete(ReviewAction).where(ReviewAction.alert_group_id.in_(deletable))
        )
        await self.session.execute(
            delete(LeadRun).where(LeadRun.alert_group_id.in_(deletable))
        )
        await self.session.execute(
            delete(AlertGroup).where(AlertGroup.id.in_(deletable))
        )
        await self.session.flush()
        return deletable, skipped

    _SCAN_RETRY_ENGINES = {
        "scan_semgrep": "semgrep",
        "scan_gitleaks": "gitleaks",
        "scan_osv": "osv",
        "api_hunt": "api_hunt",
    }

    async def purge_for_retry(self, task_id: str, from_node: str | None) -> None:
        """整轮或扫描重试时清掉会撞 unique 的发现子树；from_node=cluster 只清组。"""
        from app.contexts.discovery.models import ScanRun

        if from_node is None:
            await self._purge_groups_tree(task_id)
            await self.session.execute(delete(RawFinding).where(RawFinding.task_id == task_id))
            await self.session.execute(delete(ScanRun).where(ScanRun.task_id == task_id))
            await self.session.flush()
            return
        if from_node == "cluster":
            await self._purge_groups_tree(task_id)
            await self.session.flush()
            return
        engine = self._SCAN_RETRY_ENGINES.get(from_node)
        if engine is None:
            return
        await self._purge_groups_tree(task_id)
        await self.session.execute(
            delete(RawFinding).where(
                RawFinding.task_id == task_id, RawFinding.engine == engine,
            )
        )
        await self.session.execute(
            delete(ScanRun).where(ScanRun.task_id == task_id, ScanRun.engine == engine)
        )
        await self.session.flush()

    async def _purge_groups_tree(self, task_id: str) -> None:
        group_ids = list(
            (await self.session.execute(
                select(AlertGroup.id).where(AlertGroup.task_id == task_id)
            )).scalars()
        )
        if not group_ids:
            await self.session.execute(delete(LeadRun).where(LeadRun.task_id == task_id))
            return
        await self.session.execute(
            delete(Adjudication).where(Adjudication.alert_group_id.in_(group_ids))
        )
        await self.session.execute(
            delete(ReviewAction).where(ReviewAction.alert_group_id.in_(group_ids))
        )
        await self.session.execute(delete(LeadRun).where(LeadRun.task_id == task_id))
        await self.session.execute(
            update(RawFinding)
            .where(RawFinding.task_id == task_id)
            .values(alert_group_id=None)
        )
        await self.session.execute(delete(AlertGroup).where(AlertGroup.task_id == task_id))

    async def count_groups(self, task_id: str) -> int:
        result = await self.session.execute(
            select(func.count()).select_from(AlertGroup).where(AlertGroup.task_id == task_id)
        )
        return int(result.scalar_one())
