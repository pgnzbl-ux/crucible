"""确定性降噪 — RawFinding → 可聚类子集 + C 档统计(discovery-spec §2.4/§2.5)。

在 cluster_findings 之前调用；OSV 不在此标 C（仍走 mark_bypass）。
缺 raw 字段时保守保留进组，不得静默当误报。
"""
from __future__ import annotations

import re
from typing import Any

# Gitleaks generic：明显占位符/示例串
_PLACEHOLDER_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bEXAMPLE\b",
        r"\bYOUR[_-]",
        r"\bchangeme\b",
        r"\bplaceholder\b",
        r"\bfake[_-]?(?:secret|key|token|password)\b",
        r"\bxxx+\b",
        r"\bTODO\b.*(?:key|secret|token|password)",
        r"AKIA[0-9A-Z]{16}EXAMPLE",
        r"<your[_-][^>]+>",
        r"\$\{[^}]*(?:SECRET|KEY|TOKEN|PASSWORD)[^}]*\}",
    )
)

_DOC_PATH_PREFIXES = ("docs/", "doc/", "documentation/")
_DOC_SUFFIXES = (".md", ".rst", ".adoc", ".txt")


def _raw(finding: dict[str, Any]) -> dict[str, Any]:
    r = finding.get("raw")
    return r if isinstance(r, dict) else {}


def _rel_path(finding: dict[str, Any]) -> str:
    return (finding.get("file_path") or "").replace("\\", "/").lstrip("/")


def _is_doc_path(path: str) -> bool:
    rel = path.replace("\\", "/").lstrip("/")
    lower = rel.lower()
    if any(lower.startswith(p) or f"/{p}" in f"/{lower}" for p in _DOC_PATH_PREFIXES):
        return True
    return any(lower.endswith(s) for s in _DOC_SUFFIXES)


def _placeholder_hit(finding: dict[str, Any]) -> bool:
    hay = f"{finding.get('message') or ''} {finding.get('code_snippet') or ''}"
    return any(p.search(hay) for p in _PLACEHOLDER_PATTERNS)


def is_c_grade(finding: dict[str, Any]) -> bool:
    """表驱动 C 档判定；True = 不形成 AlertGroup。"""
    engine = (finding.get("engine") or "").lower()
    if engine == "osv":
        return False
    raw = _raw(finding)

    if engine == "semgrep":
        confidence = str(raw.get("confidence") or "UNKNOWN").upper()
        has_flow = bool(finding.get("source_to_sink")) or bool(raw.get("has_dataflow"))
        if confidence == "LOW" and not has_flow:
            return True
        category = (raw.get("category") or "").strip().lower()
        if category and category not in ("security", "vuln", "vulnerability"):
            # 仅当明确拿到非 security 类时砍；缺 category 保守保留
            return True
        return False

    if engine == "gitleaks":
        rule_class = (raw.get("rule_class") or "").strip().lower()
        if rule_class != "generic":
            return False
        if _placeholder_hit(finding):
            return True
        if _is_doc_path(_rel_path(finding)):
            return True
        return False

    if engine == "api_hunt":
        # 无 locus 或无 CWE → C；缺字段保守保留
        if not finding.get("file_path"):
            return True
        if not finding.get("cwe"):
            return True
        return False

    return False


def partition_for_cluster(
    findings: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """拆成 (可聚类, C档, by_engine_dropped)。"""
    keep: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    by_engine: dict[str, int] = {}
    for f in findings:
        if is_c_grade(f):
            dropped.append(f)
            eng = f.get("engine") or "unknown"
            by_engine[eng] = by_engine.get(eng, 0) + 1
        else:
            keep.append(f)
    return keep, dropped, by_engine
