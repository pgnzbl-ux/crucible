"""triage host 侧辅助：CWE 微评分表加载（注入 input_json.rubric）。

业务角色 / 禁令 / 工作流真相在 node-skills/triage/SKILL.md（-v 挂入容器）。
"""
from __future__ import annotations

from pathlib import Path

_RUBRICS_DIR = Path(__file__).resolve().parents[3] / "finding" / "rubrics"


def load_rubric(cwe: str | None) -> str | None:
    if not cwe:
        return None
    path = _RUBRICS_DIR / f"{cwe}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")
    return f"\n## CWE 微评分表\n{text.strip()}\n"
