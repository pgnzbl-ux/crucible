"""SARIF+ 归一化 — 三引擎输出 → 统一 RawFinding 字段(discovery-spec §6.1)。

- semgrep：SARIF 2.1.6；必须解析 codeFlows/threadFlows 为 source_to_sink
  (--dataflow-traces)，无 traces 则 None，不得丢弃已有 traces。
- gitleaks：SARIF 输出，命中原文入库供线索台研判；不得 `--redact`。
- osv-scanner：原生 JSON(osv 结果不是 SARIF)，按依赖组件映射为可读摘要。
发给 LLM / 日志的脱敏见 redact_secrets（discovery-spec §8.2）。
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


def _semgrep_confidence(props: dict) -> str:
    """规则 metadata.confidence → HIGH|MEDIUM|LOW|UNKNOWN（缺省 UNKNOWN，不作 LOW）。"""
    raw = props.get("confidence") or props.get("Likelihood") or ""
    if isinstance(raw, list):
        raw = raw[0] if raw else ""
    text = str(raw).strip().upper()
    if text in ("HIGH", "MEDIUM", "LOW"):
        return text
    return "UNKNOWN"


def _semgrep_category(props: dict) -> str | None:
    """优先 properties.category；否则从 tags 推断 security/best-practice 等。"""
    cat = props.get("category") or props.get("Category")
    if isinstance(cat, list):
        cat = cat[0] if cat else None
    if cat:
        return str(cat).strip().lower() or None
    tags = props.get("tags") or []
    if not isinstance(tags, list):
        return None
    lowered = [str(t).lower() for t in tags]
    for needle in ("security", "best-practice", "best_practice", "correctness", "performance"):
        if any(needle in t for t in lowered):
            return "best-practice" if "best" in needle else needle
    return None


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
            source_to_sink = _semgrep_source_to_sink(result)
            confidence = _semgrep_confidence(props)
            category = _semgrep_category(props)
            findings.append({
                "engine": "semgrep",
                "rule_id": rule_id,
                "cwe": cwe,
                "severity": result.get("level") or "warning",
                "file_path": uri,
                "line_start": line_start,
                "line_end": line_end,
                "message": message[:4000],
                "source_to_sink": source_to_sink,
                "code_snippet": (snippet or "")[:8000] or None,
                "fingerprint": fingerprint("semgrep", rule_id, uri, line_start, cwe),
                "raw": {
                    "rule_id": rule_id,
                    "level": result.get("level"),
                    "confidence": confidence,
                    "category": category,
                    "has_dataflow": bool(source_to_sink),
                },
            })
    return findings


# ── gitleaks SARIF ──

# 厂商/已知前缀规则 → known；其余（含 generic-api-key、entropy 类）→ generic
_GITLEAKS_KNOWN_RULE_IDS = frozenset({
    "aws-access-token", "aws-access-token-id", "aws-secret-access-key",
    "github-pat", "github-fine-grained-pat", "github-app-token",
    "github-oauth", "github-refresh-token", "gitlab-pat", "gitlab-pat-routable",
    "gitlab-deploy-token", "slack-bot-token", "slack-user-token", "slack-app-token",
    "slack-legacy-token", "stripe-access-token", "stripe-restricted-api-key",
    "private-key", "rsa-private-key", "ssh-private-key", "pgp-private-key",
    "google-api-key", "google-oauth-access-token", "heroku-api-key",
    "npm-access-token", "pypi-upload-token", "huggingface-access-token",
    "openai-api-key", "anthropic-api-key", "jwt", "jwt-base64",
})


def gitleaks_rule_class(rule_id: str) -> str:
    """known（厂商前缀）| generic（熵/泛匹配）。"""
    rid = (rule_id or "").strip().lower()
    if not rid or rid in ("gitleaks.generic", "generic-api-key", "generic-secret"):
        return "generic"
    if rid in _GITLEAKS_KNOWN_RULE_IDS:
        return "known"
    if rid.startswith("generic") or "entropy" in rid or rid.endswith("-generic"):
        return "generic"
    # 带厂商味的 id（aws-/github-/slack-…）默认 known
    prefixes = (
        "aws-", "github-", "gitlab-", "slack-", "stripe-", "google-", "gcp-",
        "azure-", "heroku-", "npm-", "pypi-", "openai-", "anthropic-", "huggingface-",
        "private-key", "rsa-", "ssh-", "pgp-", "jwt",
    )
    if any(rid.startswith(p) for p in prefixes):
        return "known"
    return "generic"


def _gitleaks_entropy(result: dict) -> float | None:
    """从 SARIF properties / partialFingerprints 尽量取 entropy。"""
    props = result.get("properties") or {}
    for key in ("entropy", "Entropy", "gitleaks:entropy"):
        val = props.get(key)
        if val is None:
            continue
        try:
            return float(val)
        except (TypeError, ValueError):
            continue
    fps = result.get("partialFingerprints") or {}
    for key, val in fps.items():
        if "entropy" in str(key).lower():
            try:
                return float(val)
            except (TypeError, ValueError):
                continue
    return None


def _gitleaks_rule_description(rule_meta: dict) -> str:
    short = rule_meta.get("shortDescription")
    if isinstance(short, dict) and short.get("text"):
        return str(short["text"]).strip()
    name = rule_meta.get("name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return ""


def _gitleaks_meta(result: dict) -> dict[str, str]:
    """从 partialFingerprints / properties 抽出提交信息。"""
    fps = result.get("partialFingerprints") or {}
    props = result.get("properties") or {}
    out: dict[str, str] = {}

    def _pick(*names: str) -> str:
        for name in names:
            val = fps.get(name)
            if val is None:
                val = props.get(name)
            if val is None or val == "":
                continue
            text = str(val).strip()
            if text:
                return text
        return ""

    mapping = {
        "commit": ("commitSha", "commit", "commitHash"),
        "author": ("author", "Author"),
        "email": ("email", "Email"),
        "date": ("date", "Date"),
        "commit_message": ("commitMessage", "message"),
    }
    for key, names in mapping.items():
        val = _pick(*names)
        if val:
            out[key] = val
    return out


def normalize_gitleaks(sarif: dict) -> list[dict]:
    findings: list[dict] = []
    rules = _semprep_rule_index(sarif)
    for run in sarif.get("runs") or []:
        for result in run.get("results") or []:
            rule_id = result.get("ruleId") or "gitleaks.generic"
            loc = (result.get("locations") or [{}])[0].get("physicalLocation", {})
            uri = (loc.get("artifactLocation") or {}).get("uri") or ""
            region = loc.get("region") or {}
            line_start = region.get("startLine")
            original_message = (result.get("message") or {}).get("text") or ""
            snippet_obj = region.get("snippet")
            snippet = snippet_obj.get("text") if isinstance(snippet_obj, dict) else snippet_obj
            snippet = (snippet or original_message or "").strip() or None
            rule_class = gitleaks_rule_class(rule_id)
            entropy = _gitleaks_entropy(result)
            description = _gitleaks_rule_description(rules.get(rule_id, {}))
            locus = f"{uri}:{line_start}" if line_start else uri
            label = description or rule_id
            message = f"{label} 命中 {locus}".strip() if locus else label
            raw: dict[str, Any] = {"rule_id": rule_id, "rule_class": rule_class}
            if entropy is not None:
                raw["entropy"] = entropy
            if description:
                raw["description"] = description
            raw.update(_gitleaks_meta(result))
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
                "code_snippet": (snippet or "")[:8000] or None,
                "fingerprint": fingerprint("gitleaks", rule_id, uri, line_start, "CWE-798"),
                "raw": raw,
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


def _osv_called_index(pkg: dict) -> dict[str, bool]:
    """groups[].experimentalAnalysis|analysis → vuln_id → called。"""
    out: dict[str, bool] = {}
    for group in pkg.get("groups") or []:
        if not isinstance(group, dict):
            continue
        analysis = group.get("experimentalAnalysis") or group.get("analysis") or {}
        if not isinstance(analysis, dict):
            continue
        for vid, detail in analysis.items():
            if isinstance(detail, dict) and "called" in detail:
                out[str(vid)] = bool(detail["called"])
    return out


def _osv_unimportant(vuln: dict) -> bool | None:
    ds = vuln.get("database_specific") if isinstance(vuln.get("database_specific"), dict) else {}
    for key in ("unimportant", "is_unimportant", "hide"):
        if key in ds:
            return bool(ds[key])
    return None


def _osv_numeric_score(value: object) -> float | None:
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        pass
    upper = text.upper()
    if upper.startswith("CVSS:4"):
        return _cvss4_heuristic_score(text)
    if upper.startswith("CVSS:"):
        return _cvss3_base_score(text)
    return None


def _osv_best_score(vuln: dict) -> float | None:
    ds = vuln.get("database_specific") if isinstance(vuln.get("database_specific"), dict) else {}
    candidates: list[object] = [ds.get("nvd_cvss_score"), ds.get("cvss_score")]
    for entry in vuln.get("severity") or []:
        if isinstance(entry, dict):
            candidates.append(entry.get("score"))
    best: float | None = None
    for candidate in candidates:
        score = _osv_numeric_score(candidate)
        if score is None:
            continue
        if best is None or score > best:
            best = score
    return best


_SEVERITY_ZH = {"error": "高危", "warning": "中危", "note": "低危", "info": "提示"}


def _severity_zh(level: str, score: float | None = None) -> str:
    if score is not None:
        if score >= 9.0:
            return "严重"
        if score >= 7.0:
            return "高危"
        if score >= 4.0:
            return "中危"
        if score > 0:
            return "低危"
    return _SEVERITY_ZH.get(level, "")


def _osv_summary_text(vuln: dict) -> str:
    summary = str(vuln.get("summary") or "").strip()
    if summary:
        return summary
    details = str(vuln.get("details") or "").strip()
    if not details:
        return ""
    return details.split("\n", 1)[0].strip()[:240]


def _osv_advisory_cwe(vuln: dict) -> str | None:
    """仅写入 raw 供展示；finding.cwe 保持空，避免改动 osv 聚类/bypass。"""
    ds = vuln.get("database_specific") if isinstance(vuln.get("database_specific"), dict) else {}
    for key in ("cwe_ids", "cwe", "CWE"):
        val = ds.get(key)
        if isinstance(val, list):
            found = _cwe_from_text(" ".join(str(x) for x in val))
        elif val:
            found = _cwe_from_text(str(val))
        else:
            found = None
        if found:
            return found
    return _cwe_from_text(vuln.get("summary"), str(vuln.get("details") or "")[:800])


def _osv_fixed_versions(vuln: dict, dep_name: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for aff in vuln.get("affected") or []:
        if not isinstance(aff, dict):
            continue
        pkg = (aff.get("package") or {}).get("name") if isinstance(aff.get("package"), dict) else None
        if pkg and dep_name and pkg != dep_name:
            continue
        for rng in aff.get("ranges") or []:
            if not isinstance(rng, dict):
                continue
            for event in rng.get("events") or []:
                if not isinstance(event, dict):
                    continue
                fixed = event.get("fixed")
                if not fixed:
                    continue
                text = str(fixed).strip()
                if text and text not in seen:
                    seen.add(text)
                    out.append(text)
    return out[:8]


def _osv_human_message(
    *,
    dep_name: str,
    version: str,
    ecosystem: str,
    summary: str,
    vid: str,
    cve: str,
    severity_label: str,
) -> str:
    pkg = f"{dep_name} {version}".strip()
    eco = f"（{ecosystem}）" if ecosystem else ""
    title = summary or vid
    ids = " / ".join(part for part in (cve, vid) if part)
    sev = f"，{severity_label}" if severity_label else ""
    id_bit = f"（{ids}{sev}）" if ids or sev else ""
    return f"{pkg}{eco} 存在依赖漏洞：{title}{id_bit}".strip()


def _osv_advisory_text(
    *,
    dep_name: str,
    version: str,
    ecosystem: str,
    lockfile: str,
    vid: str,
    cve: str,
    aliases: list,
    summary: str,
    details: str,
    severity_label: str,
    cvss_score: float | None,
    fixed_versions: list[str],
    called: bool | None,
) -> str:
    pkg = f"{dep_name} {version}".strip()
    lines = [f"依赖：{pkg}" + (f"（{ecosystem}）" if ecosystem else "")]
    if lockfile:
        lines.append(f"清单：{lockfile}")
    ids = " / ".join(part for part in (vid, cve) if part)
    if ids:
        lines.append(f"漏洞：{ids}")
    extra_aliases = [str(a) for a in aliases if a and a not in {vid, cve}]
    if extra_aliases:
        lines.append("别名：" + "、".join(extra_aliases[:8]))
    if severity_label or cvss_score is not None:
        score_bit = f"（CVSS {cvss_score}）" if cvss_score is not None else ""
        lines.append(f"严重度：{severity_label}{score_bit}")
    if summary:
        lines.append(f"摘要：{summary}")
    if details and details.strip() != summary:
        lines.append(f"说明：{details.strip()[:2000]}")
    if fixed_versions:
        lines.append("修复版本：" + "、".join(fixed_versions))
    if called is True:
        lines.append("可达性：受影响代码已被调用")
    elif called is False:
        lines.append("可达性：受影响代码未被调用")
    if vid:
        lines.append(f"查阅：https://osv.dev/vulnerability/{vid}")
    return "\n".join(lines)


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
            ecosystem = str(package.get("ecosystem") or "").strip()
            called_by_id = _osv_called_index(pkg)
            for vuln in pkg.get("vulnerabilities") or []:
                vid = vuln.get("id") or ""
                if not vid:
                    continue
                aliases = [str(a) for a in (vuln.get("aliases") or []) if a]
                cve = next((a for a in aliases if a.startswith("CVE-")), "")
                called = called_by_id.get(vid)
                if called is None:
                    for aid in aliases:
                        if aid in called_by_id:
                            called = called_by_id[aid]
                            break
                unimportant = _osv_unimportant(vuln)
                summary = _osv_summary_text(vuln)
                details = str(vuln.get("details") or "").strip()
                cvss_score = _osv_best_score(vuln)
                level = _osv_severity(vuln)
                if not level and cvss_score is not None:
                    level = _level_from_score(cvss_score)
                severity_label = _severity_zh(level, cvss_score)
                fixed_versions = _osv_fixed_versions(vuln, dep_name)
                advisory_cwe = _osv_advisory_cwe(vuln)
                raw: dict[str, Any] = {
                    "rule_id": vid, "dependency_name": dep_name, "version": version,
                    "cve": cve, "aliases": aliases[:8],
                    "cvss": _osv_cvss_raw(vuln),
                    "called": called,  # bool | None
                    "osv_url": f"https://osv.dev/vulnerability/{vid}",
                }
                if ecosystem:
                    raw["ecosystem"] = ecosystem
                if summary:
                    raw["summary"] = summary[:500]
                if details:
                    raw["details"] = details[:2000]
                if cvss_score is not None:
                    raw["cvss_score"] = cvss_score
                if severity_label:
                    raw["severity_label"] = severity_label
                if fixed_versions:
                    raw["fixed_versions"] = fixed_versions
                if advisory_cwe:
                    raw["cwe"] = advisory_cwe
                if unimportant is not None:
                    raw["unimportant"] = unimportant
                findings.append({
                    "engine": "osv",
                    "rule_id": vid,
                    "cwe": None,  # osv 不稳定提供 CWE；依赖情报直报
                    "severity": level,
                    "file_path": lockfile,
                    "line_start": None,
                    "line_end": None,
                    "message": _osv_human_message(
                        dep_name=dep_name, version=version, ecosystem=ecosystem,
                        summary=summary, vid=vid, cve=cve, severity_label=severity_label,
                    )[:4000],
                    "source_to_sink": None,
                    "code_snippet": _osv_advisory_text(
                        dep_name=dep_name, version=version, ecosystem=ecosystem,
                        lockfile=lockfile, vid=vid, cve=cve, aliases=aliases,
                        summary=summary, details=details, severity_label=severity_label,
                        cvss_score=cvss_score, fixed_versions=fixed_versions,
                        called=called,
                    )[:8000] or None,
                    "fingerprint": fingerprint("osv", vid, f"{lockfile}#{dep_name}", None, None),
                    "raw": raw,
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
