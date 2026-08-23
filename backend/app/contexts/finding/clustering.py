"""确定性聚类 — RawFinding → AlertGroup。

分组键：sha256(cwe+file+function)（索引反查函数名，缺失降级 rule_id）；
osv 特例按依赖组件。写 clue_grade(A/B/F，osv 为 null) 与 priority（含攻击面降权）。
"""
from __future__ import annotations

import hashlib
from typing import Any

# §2.4 攻击面降权：这些路径前缀/后缀的组 priority 不得高于 low
_DOWNGRADE_PATH_PREFIXES = ("test/", "tests/", "docs/", "vendor/", "node_modules/")
_DOWNGRADE_SUFFIXES = (".md",)
# 会降权的 CWE 白名单(gitleaks/CWE-798 与集合外 CWE 不降权)
_DOWNGRADE_CWES = frozenset({"CWE-89", "CWE-78", "CWE-79", "CWE-22", "CWE-611", "CWE-601"})

_SEVERITY_RANK = {"error": "high", "warning": "medium", "note": "low", "info": "low"}


def _group_key(cwe: str | None, file_path: str, function_symbol: str | None,
               rule_id: str | None = None, *, engine: str | None = None,
               dependency: str | None = None) -> str:
    """聚类指纹。osv 特例按依赖；通用按 (cwe,file,function)，无函数降级 (cwe,rule,file)。"""
    if engine == "osv":
        return hashlib.sha256(f"osv|{rule_id}|{dependency}".encode()).hexdigest()
    if function_symbol:
        raw = f"{cwe}|{file_path}|{function_symbol}"
    else:
        raw = f"{cwe}|{rule_id}|{file_path}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def should_downgrade(file_path: str, cwe: str | None, engine: str | list[str] | tuple[str, ...] | set[str]) -> bool:
    """§2.4 攻击面降权判定：注入/XSS/路径类落在测试/文档路径才压优先级。

    engine 可传单引擎或 engine_set 全集：合并组只要含 gitleaks 成员即享例外
    （test/ 里的硬编码密钥常是真泄露）；集合外 CWE(918/502/863 等)即使落在
    tests/ 也不因路径降权。
    """
    engines = set(engine) if isinstance(engine, (list, tuple, set)) else {engine}
    if "gitleaks" in engines or cwe == "CWE-798":
        return False
    if cwe not in _DOWNGRADE_CWES:
        return False
    rel = (file_path or "").replace("\\", "/").lstrip("/")
    if any(rel.startswith(p) or f"/{p}" in rel for p in _DOWNGRADE_PATH_PREFIXES):
        return True
    return rel.lower().endswith(_DOWNGRADE_SUFFIXES)


def grade_for(finding: dict[str, Any]) -> str | None:
    """clue_grade：非空 source_to_sink → A；locus+CWE → B；无法定位 → F。
    osv 不参与 A/B/F（bypass 直报），返回 None。
    """
    if finding.get("engine") == "osv":
        return None
    has_locus = bool(finding.get("file_path")) and (
        finding.get("line_start") is not None or finding.get("function_symbol")
    )
    has_cwe = bool(finding.get("cwe"))
    if finding.get("source_to_sink"):
        return "A"
    if has_locus and has_cwe:
        return "B"
    if not has_locus and not has_cwe:
        return "F"
    return "B"


def cluster_findings(findings: list[dict[str, Any]], index: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """聚类 → 组 dict 列表(未落库)。finding 需含 RawFinding 字段。

    同组：同 group_key 的成员合并；代表成员取 severity 最高。
    重跑合并语义由 service 层按 (task_id, group_key) upsert 实现。
    """
    from app.contexts.finding.context_extractor import enclosing
    from app.contexts.finding.classification import infer_cwe

    groups: dict[str, dict[str, Any]] = {}
    for f in findings:
        effective_cwe = infer_cwe(
            cwe=f.get("cwe"), rule_id=f.get("rule_id") or "",
            message=f.get("message") or "", engine=f.get("engine") or "",
        )
        classified_f = {**f, "cwe": effective_cwe}
        rel = (f.get("file_path") or "").replace("\\", "/").lstrip("/")
        function_symbol = f.get("function_symbol")
        if not function_symbol and f.get("engine") != "osv":
            entry = enclosing(index, rel, f.get("line_start"))
            if entry:
                function_symbol = entry.get("symbol")
        dependency = (f.get("raw") or {}).get("dependency_name") if isinstance(f.get("raw"), dict) else None
        key = _group_key(
            effective_cwe, rel, function_symbol, f.get("rule_id"),
            engine=f.get("engine"), dependency=dependency,
        )
        g = groups.get(key)
        if g is None:
            g = {
                "group_key": key,
                "cwe": effective_cwe,
                "file_path": rel,
                "function_symbol": function_symbol,
                "line_span": _span(f.get("line_start"), f.get("line_end")),
                "member_count": 0,
                "representative_finding_id": f.get("id"),
                "rep_severity": _rank(f.get("severity")),
                "rep_raw": f,
                "engine_set": [],
                "clue_grade": grade_for(classified_f),
            }
            groups[key] = g
        else:
            # 补函数名：后到的成员若反查到符号，写回组
            if not g.get("function_symbol") and function_symbol:
                g["function_symbol"] = function_symbol
        g["member_count"] += 1
        if f.get("engine") and f["engine"] not in g["engine_set"]:
            g["engine_set"].append(f["engine"])
        # 代表成员取 severity 最高(并列保留先到)
        if _rank(f.get("severity")) > g["rep_severity"]:
            g["rep_severity"] = _rank(f.get("severity"))
            g["representative_finding_id"] = f.get("id")
            g["rep_raw"] = f
            g["clue_grade"] = grade_for(classified_f)

    out: list[dict[str, Any]] = []
    for g in groups.values():
        rep = g.pop("rep_raw")
        g.pop("rep_severity")
        engine_set = g.pop("engine_set")
        downgrade = should_downgrade(g["file_path"], g["cwe"], engine_set)
        priority = "low" if downgrade else _SEVERITY_RANK.get((rep.get("severity") or "").lower(), "medium")
        out.append({**g, "engine_set": engine_set, "priority": priority, "downgraded": downgrade})
    return out


def _span(start: int | None, end: int | None) -> str | None:
    if start is None:
        return None
    return f"{start}-{end if end is not None else start}"


def _rank(severity: str | None) -> int:
    return {"error": 3, "warning": 2, "note": 1, "info": 0}.get((severity or "").lower(), 1)
