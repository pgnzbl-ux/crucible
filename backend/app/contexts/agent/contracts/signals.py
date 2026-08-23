"""编排层只读的控制信号（分支出口）。

discovery-spec §4.2：
- is_web / gate_verdict 语义不变；
- has_dispatch_lead 仅审计任务由 dispatch 写入(verify 任务视为人已给线索，不走此信号)；
- lead_driven = 审计且已入队 → DAG 上单例 audit/reproduce skip，由 LeadWorker 跑；
- verify_mode 由编排器从 task.task_type 读取，不进 HandoffStore。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlSignals:
    is_web: bool | None = None
    gate_verdict: str | None = None
    has_dispatch_lead: bool | None = None
    verify_mode: bool = False

    @property
    def non_web(self) -> bool:
        return self.is_web is not True

    @property
    def no_dispatch_lead(self) -> bool:
        """NO_DISPATCH_LEAD = 审计任务且无入队线索；验证任务恒 False。"""
        if self.verify_mode:
            return False
        return self.has_dispatch_lead is not True

    @property
    def lead_driven(self) -> bool:
        """审计且有入队线索 → DAG audit/reproduce 由 LeadWorker 承担。"""
        if self.verify_mode:
            return False
        return self.has_dispatch_lead is True
