"""编排层只读的控制信号（分支出口）。

discovery-spec §4.2：
- is_web：仅显式 False 才触发 NON_WEB；None（未知）不得当作非 Web；
- gate_verdict 语义不变；
- has_dispatch_lead 仅审计任务由 dispatch 写入(verify 任务视为人已给线索，不走此信号)；
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
        """未知 ≠ 否定：只有画像明确 is_web=False 才 skip 靶场/复现。"""
        return self.is_web is False

    @property
    def no_dispatch_lead(self) -> bool:
        """NO_DISPATCH_LEAD = 审计任务且无入队线索；验证任务恒 False。"""
        if self.verify_mode:
            return False
        return self.has_dispatch_lead is not True
