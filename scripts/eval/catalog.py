"""加载 scripts/eval/golden/*/case.yaml。"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from metrics import CaseRecord

EVAL_DIR = Path(__file__).resolve().parent
GOLDEN_DIR = EVAL_DIR / "golden"


class CatalogError(ValueError):
    pass


def _require(data: dict[str, Any], key: str, case_dir: Path) -> Any:
    if key not in data or data[key] in (None, ""):
        raise CatalogError(f"{case_dir.name}: 缺少 {key}")
    return data[key]


def load_case(case_dir: Path) -> CaseRecord:
    path = case_dir / "case.yaml"
    if not path.is_file():
        raise CatalogError(f"{case_dir}: 缺少 case.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    expected = data.get("expected") or []
    if not isinstance(expected, list) or not expected:
        raise CatalogError(f"{case_dir.name}: expected 必须是非空列表")
    for i, item in enumerate(expected):
        if not item.get("cwe") or not item.get("file_contains"):
            raise CatalogError(f"{case_dir.name}: expected[{i}] 需要 cwe 与 file_contains")
    labels = data.get("labels") or {}
    notes = (data.get("notes") or "").strip()
    readme = case_dir / "README.md"
    if readme.is_file():
        notes = notes or readme.read_text(encoding="utf-8").strip()
    if not notes:
        raise CatalogError(f"{case_dir.name}: 需要 notes 或 README.md 说明")
    return CaseRecord(
        case_id=str(_require(data, "id", case_dir)),
        git_url=str(_require(data, "git_url", case_dir)),
        ref=str(_require(data, "ref", case_dir)),
        expected=list(expected),
        tp_samples=list(labels.get("tp_samples") or []),
        fp_samples=list(labels.get("fp_samples") or []),
        language=str(data.get("language") or ""),
        notes=notes,
    )


def load_catalog(golden_dir: Path | None = None) -> list[CaseRecord]:
    root = golden_dir or GOLDEN_DIR
    if not root.is_dir():
        raise CatalogError(f"黄金集目录不存在: {root}")
    cases: list[CaseRecord] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "case.yaml").exists():
            cases.append(load_case(child))
    if len(cases) < 50:
        raise CatalogError(f"黄金集不足 50 个（当前 {len(cases)}）")
    ids = [c.case_id for c in cases]
    if len(ids) != len(set(ids)):
        raise CatalogError("黄金集 id 重复")
    return cases
