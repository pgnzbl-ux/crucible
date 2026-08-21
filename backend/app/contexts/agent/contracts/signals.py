"""编排层只读的控制信号（分支出口）。"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ControlSignals:
    is_web: bool | None = None
    gate_verdict: str | None = None

    @property
    def non_web(self) -> bool:
        return self.is_web is not True
