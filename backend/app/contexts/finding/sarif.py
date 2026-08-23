"""SARIF+ 归一化 — 三引擎输出 → 统一 RawFinding 字段(discovery-spec §6.1)。

- semgrep：SARIF 2.1.6；必须解析 codeFlows/threadFlows 为 source_to_sink
  (--dataflow-traces)，无 traces 则 None，不得丢弃已有 traces。
- gitleaks：SARIF 输出 + `--redact` 双保险；入库前再过本地脱敏(§8.2)。
- osv-scanner：原生 JSON(osv 结果不是 SARIF)，按依赖组件映射。
"""
from __future__ import annotations

import hashlib
import math
import re
from typing import Any

# ── 秘密脱敏(discovery-spec §8.2)：保留前 4 + … + 后 4 与总长，其余 *** ──

_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p)
    for p in (
        r"AKIA[0-9A-Z]{16}",
        r"ASIA[0-9A-Z]{16}",
        r"ghp_[A-Za-z0-9]{36}",
        r"gho_[A-Za-z0-9]{36}",
        r"github_pat_[A-Za-z0-9_]{22,}",
        r"sk-[A-Za-z0-9]{20,}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"AIza[0-9A-Za-z_-]{35}",
        r"(?:eyJ[A-Za-z0-9_-]{10,}\.){2}[A-Za-z0-9_-]{10,}",  # JWT
        r"(?i)(?:password|passwd|secret|token|api[_-]?key)\s*[=:]\s*['\"]?([A-Za-z0-9+/_@!#$%^&*.-]{16,})['\"]?",
    )
)

_KEY_BLOCK = re.compile(
    r"-----BEGIN ((?:RSA |EC |OPENSSH |DSA )?)PRIVATE KEY-----.+?-----END \1PRIVATE KEY-----",
    re.DOTALL,
)


def redact_value(value: str) -> str:
    """单个秘密值 → 前 4 + … + 后 4 + 长度标注。短值(<12)整体掩码。"""
    value = value.strip()
    if len(value) < 12:
        return "***"
    return f"{value[:4]}…{value[-4:]}***[len={len(value)}]"


def redact_secrets(text: str) -> str:
    """对文本跑全部秘密指纹；gitleaks 类输出入库前的最后一道防线。"""
    if not text:
        return text
    # 先整体私钥块(单跑 BEGIN 头会把块正则破坏掉)，再逐指纹
    text = _KEY_BLOCK.sub(
        lambda m: "-----BEGIN PRIVATE KEY-----" + redact_value(m.group(0)), text
    )
    # 上下文捕获组(password=xxx)只掩值本身
    def _ctx(m: re.Match) -> str:
        return f"{m.group(0)[:m.start(1) - m.start()]}{redact_value(m.group(1))}"

    for pat in _SECRET_PATTERNS:
        if pat.groups:
            text = pat.sub(_ctx, text)
        else:
            text = pat.sub(lambda m: redact_value(m.group(0)), text)
    return text


def fingerprint(engine: str, rule_id: str, file_path: str, line_start: int | None, cwe: str | None) -> str:
    raw = f"{engine}|{rule_id}|{file_path}|{line_start}|{cwe or ''}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _cwe_from_text(*texts: str | None) -> str | None:
    for text in texts:
        if not text:
            continue
        m = re.search(r"CWE-\d+", text, re.IGNORECASE)
        if m:
            return f"CWE-{m.group(0).split('-')[1]}"
    return None


# ── semgrep SARIF ──

def _semprep_rule_index(sarif: dict) -> dict[str, dict]:
    index: dict[str, dict] = {}
    for driver in (sarif.get("runs") or [{}])[0].get("tool", {}).get("driver", {}).get("rules", []) or []:
        rid = driver.get("id")
        if rid:
            index[rid] = driver
    return index


def _semgrep_source_to_sink(result: dict) -> list[str] | None:
    """codeFlows/threadFlows → ["src/a.py:88 (read_input)", ...]；无则 None。"""
    chain: list[str] = []
    for flow in result.get("codeFlows") or []:
        for tf in flow.get("threadFlows") or []:
            for loc in tf.get("locations") or []:
                phys = (loc.get("location") or {}).get("physicalLocation") or {}
                artifact = (phys.get("artifactLocation") or {})
                uri = artifact.get("uri")
                if not uri:
                    continue
                line = (phys.get("region") or {}).get("startLine")
                msg = (loc.get("location") or {}).get("message", {}).get("text") or ""
                label = f" {msg.strip()}" if msg.strip() else ""
                chain.append(f"{uri}:{line}({label.strip()})" if label else f"{uri}:{line}")
    return chain or None


def normalize_semgrep(sarif: dict) -> list[dict]:
    """semgrep SARIF → RawFinding 字段 dict 列表(未脱敏引擎不含秘密，消息原样)。"""
    rules = _semprep_rule_index(sarif)
    findings: list[dict] = []
    for run in sarif.get("runs") or []:
        for result in run.get("results") or []:
            rule_id = result.get("ruleId") or "semgrep.unknown"
            loc = (result.get("locations") or [{}])[0].get("physicalLocation", {})
            uri = (loc.get("artifactLocation") or {}).get("uri") or ""
            region = loc.get("region") or {}
            line_start = region.get("startLine")
            line_end = region.get("endLine", line_start)
            message = (result.get("message") or {}).get("text") or ""
            snippet_obj = region.get("snippet")
            snippet = snippet_obj.get("text") if isinstance(snippet_obj, dict) else snippet_obj

            rule_meta = rules.get(rule_id, {})
            props = rule_meta.get("properties") or {}
            cwe = _cwe_from_text(
                " ".join(props.get("cwe") or []) if isinstance(props.get("cwe"), list) else str(props.get("cwe") or ""),
                " ".join(props.get("tags") or []),
                rule_meta.get("shortDescription", {}).get("text"),
                message,
            )
            findings.append({
                "engine": "semgrep",
                "rule_id": rule_id,
                "cwe": cwe,
                "severity": result.get("level") or "warning",
                "file_path": uri,
                "line_start": line_start,
                "line_end": line_end,
                "message": message[:4000],
                "source_to_sink": _semgrep_source_to_sink(result),
                "code_snippet": (snippet or "")[:8000] or None,
                "fingerprint": fingerprint("semgrep", rule_id, uri, line_start, cwe),
                "raw": {"rule_id": rule_id, "level": result.get("level")},
            })
    return findings


# ── gitleaks SARIF ──

def normalize_gitleaks(sarif: dict) -> list[dict]:
    findings: list[dict] = []
    for run in sarif.get("runs") or []:
        for result in run.get("results") or []:
            rule_id = result.get("ruleId") or "gitleaks.generic"
            loc = (result.get("locations") or [{}])[0].get("physicalLocation", {})
            uri = (loc.get("artifactLocation") or {}).get("uri") or ""
            region = loc.get("region") or {}
            line_start = region.get("startLine")
            message = redact_secrets((result.get("message") or {}).get("text") or "")
            snippet_obj = region.get("snippet")
            snippet = snippet_obj.get("text") if isinstance(snippet_obj, dict) else snippet_obj
            findings.append({
                "engine": "gitleaks",
                "rule_id": rule_id,
                "cwe": "CWE-798",  # gitleaks 全部是硬编码密钥类
                "severity": result.get("level") or "error",
                "file_path": uri,
                "line_start": line_start,
                "line_end": region.get("endLine", line_start),
                "message": message[:4000],
                "source_to_sink": None,
                "code_snippet": (redact_secrets(snippet or "") or "")[:8000] or None,
                "fingerprint": fingerprint("gitleaks", rule_id, uri, line_start, "CWE-798"),
                "raw": {"rule_id": rule_id},  # 原始条目不落数据库(SARIF 归档在 MinIO)
            })
    return findings


# ── osv-scanner JSON ──

# SARIF level ← CVSS 定性（FIRST：0 None / 0.1–3.9 Low / 4.0–6.9 Medium / 7.0–8.9 High / 9.0–10 Critical）
_OSV_LABEL_TO_LEVEL = {
    "critical": "error",
    "high": "error",
    "medium": "warning",
    "moderate": "warning",
    "low": "note",
    "none": "info",
    "info": "info",
    "error": "error",
    "warning": "warning",
    "note": "note",
}
_CVSS3_AV = {"N": 0.85, "A": 0.62, "L": 0.55, "P": 0.2}
_CVSS3_AC = {"L": 0.77, "H": 0.44}
_CVSS3_PR_U = {"N": 0.85, "L": 0.62, "H": 0.27}
_CVSS3_PR_C = {"N": 0.85, "L": 0.68, "H": 0.50}
_CVSS3_UI = {"N": 0.85, "R": 0.62}
_CVSS3_CIA = {"H": 0.56, "L": 0.22, "N": 0.0}


def _cvss_roundup(value: float) -> float:
    """CVSS 3.1 roundup：向 0.1 进位。"""
    scaled = round(value * 100000)
    if scaled % 10000 == 0:
        return scaled / 100000.0
    return math.floor(scaled / 10000 + 1) / 10.0


def _parse_cvss_metrics(vector: str) -> dict[str, str]:
    parts = vector.strip().split("/")
    metrics: dict[str, str] = {}
    for part in parts[1:] if parts and parts[0].upper().startswith("CVSS:") else parts:
        if ":" not in part:
            continue
        key, val = part.split(":", 1)
        metrics[key.upper()] = val.upper()
    return metrics


def _cvss3_base_score(vector: str) -> float | None:
    m = _parse_cvss_metrics(vector)
    try:
        scope_changed = m["S"] == "C"
        iss = 1 - (
            (1 - _CVSS3_CIA[m["C"]])
            * (1 - _CVSS3_CIA[m["I"]])
            * (1 - _CVSS3_CIA[m["A"]])
        )
        if scope_changed:
            impact = 7.52 * (iss - 0.029) - 3.25 * (iss - 0.02) ** 15
        else:
            impact = 6.42 * iss
        pr = _CVSS3_PR_C if scope_changed else _CVSS3_PR_U
        exploitability = (
            8.22 * _CVSS3_AV[m["AV"]] * _CVSS3_AC[m["AC"]] * pr[m["PR"]] * _CVSS3_UI[m["UI"]]
        )
    except KeyError:
        return None
    if impact <= 0:
        return 0.0
    if scope_changed:
        return _cvss_roundup(min(1.08 * (impact + exploitability), 10))
    return _cvss_roundup(min(impact + exploitability, 10))


def _cvss4_heuristic_score(vector: str) -> float | None:
    """CVSS 4.0 完整公式过重；按 VC/VI/VA 影响给定性分，只用于落到 error/warning/note。"""
    m = _parse_cvss_metrics(vector)
    vuln = {m.get("VC", "N"), m.get("VI", "N"), m.get("VA", "N")}
    sub = {m.get("SC", "N"), m.get("SI", "N"), m.get("SA", "N")}
    if "H" in vuln:
        return 8.0
    if "L" in vuln:
        return 5.0
    if "H" in sub or "L" in sub:
        return 2.0
    return 0.0


def _level_from_score(score: float) -> str:
    if score >= 7.0:
        return "error"
    if score >= 4.0:
        return "warning"
    if score > 0:
        return "note"
    return "info"


def _osv_score_to_level(value: object) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return ""
    if isinstance(value, (int, float)):
        return _level_from_score(float(value))
    text = str(value).strip()
    if not text:
        return ""
    mapped = _OSV_LABEL_TO_LEVEL.get(text.lower())
    if mapped:
        return mapped
    try:
        return _level_from_score(float(text))
    except ValueError:
        pass
    upper = text.upper()
    if upper.startswith("CVSS:4"):
        score = _cvss4_heuristic_score(text)
        return _level_from_score(score) if score is not None else ""
    if upper.startswith("CVSS:"):
        score = _cvss3_base_score(text)
        return _level_from_score(score) if score is not None else ""
    return ""


def _osv_severity(vuln: dict) -> str:
    """OSV severity[].score 经常是 CVSS 向量，不能原文写入 varchar(20)。"""
    ds = vuln.get("database_specific") if isinstance(vuln.get("database_specific"), dict) else {}
    for candidate in (ds.get("severity"), ds.get("nvd_cvss_score")):
        level = _osv_score_to_level(candidate)
        if level:
            return level
    best = ""
    best_rank = -1
    ranks = {"error": 3, "warning": 2, "note": 1, "info": 0}
    for entry in vuln.get("severity") or []:
        if not isinstance(entry, dict):
            continue
        level = _osv_score_to_level(entry.get("score"))
        rank = ranks.get(level, -1)
        if rank > best_rank:
            best, best_rank = level, rank
    return best


def _osv_cvss_raw(vuln: dict) -> object:
    for entry in vuln.get("severity") or []:
        if isinstance(entry, dict) and entry.get("score") is not None:
            return entry.get("score")
    return None


def normalize_osv(report: dict) -> list[dict]:
    """osv-scanner scan --format=json → 每个依赖组件一条 finding(直报，不进 triage)。"""
    findings: list[dict] = []
    for result in report.get("results") or []:
        source = result.get("source") or {}
        lockfile = source.get("path") or ""
        for pkg in result.get("packages") or []:
            package = pkg.get("package") or {}
            dep_name = package.get("name") or "unknown"
            version = package.get("version") or ""
            for vuln in pkg.get("vulnerabilities") or []:
                vid = vuln.get("id") or ""
                if not vid:
                    continue
                aliases = vuln.get("aliases") or []
                cve = next((a for a in aliases if a.startswith("CVE-")), "")
                findings.append({
                    "engine": "osv",
                    "rule_id": vid,
                    "cwe": None,  # osv 不稳定提供 CWE；依赖情报直报
                    "severity": _osv_severity(vuln),
                    "file_path": lockfile,
                    "line_start": None,
                    "line_end": None,
                    "message": redact_secrets(
                        f"{dep_name} {version}: {vuln.get('summary') or vid} {cve}".strip()
                    )[:4000],
                    "source_to_sink": None,
                    "code_snippet": None,
                    "fingerprint": fingerprint("osv", vid, f"{lockfile}#{dep_name}", None, None),
                    "raw": {
                        "rule_id": vid, "dependency_name": dep_name, "version": version,
                        "cve": cve, "aliases": aliases[:5],
                        "cvss": _osv_cvss_raw(vuln),
                    },
                })
    return findings


def normalize(engine: str, payload: dict) -> list[dict]:
    if engine == "semgrep":
        return normalize_semgrep(payload)
    if engine == "gitleaks":
        return normalize_gitleaks(payload)
    if engine == "osv":
        return normalize_osv(payload)
    raise ValueError(f"未知引擎: {engine}")
