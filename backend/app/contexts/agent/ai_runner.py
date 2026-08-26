"""AI 节点容器编排 — 每节点起一个 agent-runner 容器调 SDK。

流程:
  1. 写本次执行专属的 node.json(node_key + input_json)到 host_workdir/.runner/<id>
  2. 起 agent-runner 容器：
     - bind host_workdir → /workspace
     - bind node-skills/<node> → /node-skill:ro（仅当前节点 skill）
     - 注入 ANTHROPIC_* env + NODE_KEY
  3. 容器内 run_one.py 读 /node-skill/SKILL.md 作 system_prompt，跑完 submit_result
  4. submit_result 把 input 写到本次执行专属的 node_output.json
  5. worker 读 node_output.json → schema 校验 → 返回 output_json
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from app.contexts.agent.llm_errors import classify_llm_api_error, is_llm_api_failure
from app.core.agent_runner import (
    AgentRunnerError,
    AgentRunnerSpec,
    agent_runner_manager,
)

logger = logging.getLogger(__name__)

# 仓库根：backend/app/contexts/agent/ai_runner.py → parents[4] = Crucible/
_REPO_ROOT = Path(__file__).resolve().parents[4]
_NODE_SKILLS_ROOT = _REPO_ROOT / "infrastructure" / "agent-runner" / "node-skills"


def resolve_node_skill_dir(node_key: str) -> Path:
    """当前节点 skill 目录（host）；缺 SKILL.md 则 Fail-Fast。"""
    skill_dir = _NODE_SKILLS_ROOT / node_key
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        raise AgentRunnerError(f"节点 skill 不存在: {skill_file}")
    return skill_dir


def apply_stream_usage_fallback(meta_out: dict[str, Any] | None, stream: dict[str, Any]) -> None:
    """sidecar 未给出 usage dict 时，用 stdout 的 agent.completed 回填。

    同一轮 sidecar 已是 dict 则不动——形状回喂累加依赖 prev/fresh 两份 sidecar，
    再叠本轮 stdout 会把这一轮算两遍。
    """
    if meta_out is None or not isinstance(stream, dict):
        return
    if not isinstance(meta_out.get("usage"), dict) and isinstance(stream.get("usage"), dict):
        meta_out["usage"] = stream["usage"]
    if meta_out.get("model_usage") is None and stream.get("model_usage") is not None:
        meta_out["model_usage"] = stream["model_usage"]

REPORT_SECTION_KEYS = (
    "product_intro", "vulnerability", "impact", "details",
    "reproduction", "poc_commands", "fix_suggestions", "reporting_decision",
)
RECORD_SECTION_KEYS = (
    "product_intro", "claimed_issue", "whitebox_analysis", "test_record",
    "blocker", "observed_facts", "remaining_conditions", "reporting_decision",
)
_VERDICTS = (
    "confirmed", "partial", "code_reachable", "code_smell",
    "false_positive", "not_reproduced",
)
_CONFIRMED_VERDICTS = ("confirmed", "partial")
# report 节点独有：audit uncertain（无 reproduce）也要出一份验证记录，
# 挂 needs_review 判定与任务 needs_review 状态对齐。reproduce 不产此值。
_REPORT_VERDICTS = _VERDICTS + ("needs_review",)
_ATTEMPT_KEYS = (
    "purpose", "request", "response_status", "response_excerpt", "observation", "result",
)
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# 各 AI 节点的 output schema(校验最小必需字段,见 docs/discovery-spec.md §4.3/§6)
NODE_OUTPUT_SCHEMAS: dict[str, dict] = {
    "canary": {
        "required": ["marker", "probe_completed", "credential_visible", "summary"],
        "optional": [],
    },
    "profile": {
        "required": ["is_web"],
        "optional": [
            "language", "framework", "port", "has_dockerfile", "has_compose",
            "detected_services", "start_command", "non_web_reason",
        ],
    },
    "env_ready": {
        "required": ["target_url", "compose_path", "initial_creds"],
        "optional": ["transport_shape", "started_containers"],
    },
    "audit": {
        "required": ["gate_verdict", "gate_reason"],
        "optional": ["kill_chain", "defense_layers", "payloads", "runtime_dependent", "core_claim", "unresolved_facts"],
    },
    "reproduce": {
        "required": ["verdict", "reproduced", "attempts"],
        "optional": ["evidence", "screenshots", "cvss", "vulnerable_file", "poc"],
    },
    "report": {
        "required": ["report_data", "final_verdict"],
        "optional": ["cvss", "vulnerable_file"],
    },
    "triage": {
        "required": ["verdict", "confidence", "why", "summary", "reasoning"],
        "optional": [
            "evidence", "need", "attacker_controlled", "reaches_sink", "sanitizer",
        ],
    },
    "api_hunt": {
        "required": ["suspects", "reviewed_count"],
        "optional": ["budget_exhausted"],
    },
}

def rewrite_url_for_agent_container(url: str | None) -> str | None:
    """回环/遗留 host.docker.internal 改成宿主机 IP:port；已发布地址原样保留。"""
    from app.contexts.agent.target_url import rewrite_url_for_agent_container as _rewrite
    return _rewrite(url)


def _mock_output(node_key: str, input_json: dict[str, Any]) -> dict[str, Any]:
    """Mock 模式:SDK 未启用时返回模拟 output(通过 schema 校验),供编排链路联调。"""
    if node_key == "canary":
        return {
            "marker": "",
            "probe_completed": False,
            "credential_visible": True,
            "summary": "[Mock] 未运行真实 Agent canary",
        }
    if node_key == "profile":
        hints = input_json.get("hints") or {}
        return {
            "is_web": hints.get("is_web", True),
            "language": hints.get("language") or "python",
            "framework": hints.get("framework") or "fastapi",
            "port": hints.get("port") or 8000,
            "has_dockerfile": bool(hints.get("has_dockerfile")),
            "has_compose": bool(hints.get("has_compose")),
            "detected_services": hints.get("detected_services") or [],
        }
    if node_key == "env_ready":
        return {
            "target_url": "http://localhost:8080",
            "compose_path": ".vuln-env/docker-compose.yml",
            "transport_shape": {"protocol": "http", "listener": "0.0.0.0:8080", "tls_termination": "无"},
            "initial_creds": {"note": "[Mock] 未配置预设账号"},
            "started_containers": ["mock-app"],
        }
    if node_key == "audit":
        return {
            "kill_chain": "[Mock] entry → sink(模拟调用链)",
            "defense_layers": [{"name": "validator", "bypass": "模拟绕过"}],
            "payloads": [{
                "method": "GET",
                "path": "/mock",
                "expected_observable": "[Mock] 回显 marker",
            }],
            "gate_verdict": "pass",
            "gate_reason": "[Mock] 三问通过",
            "runtime_dependent": False,
            "core_claim": "[Mock] 匿名可读 /mock 回显",
        }
    if node_key == "reproduce":
        return {
            "reproduced": False,
            "evidence": [],
            "attempts": [{
                "purpose": "[Mock] SDK 未启用，不进行 live 复现",
                "request": "n/a",
                "response_status": 0,
                "response_excerpt": "[Mock] 无沙箱 HTTP",
                "observation": "[Mock] 未发起真实请求",
                "result": "not_attempted",
            }],
            "screenshots": [],
            "verdict": "not_reproduced",
            "vulnerable_file": "",
        }
    if node_key == "report":
        expected = input_json.get("expected_verdict")
        if expected not in _REPORT_VERDICTS:
            repro = input_json.get("reproduce") or {}
            expected = repro.get("verdict") if repro.get("verdict") in _VERDICTS else None
        if expected not in _REPORT_VERDICTS:
            audit = input_json.get("audit") or {}
            gate = audit.get("gate_verdict")
            if gate == "fail":
                expected = "false_positive"
            elif gate == "uncertain":
                expected = "needs_review"
            else:
                expected = "not_reproduced"
        kind = document_kind_for_verdict(expected)
        keys = REPORT_SECTION_KEYS if kind == "vulnerability_report" else RECORD_SECTION_KEYS
        report_data = {k: f"[Mock] {k}" for k in keys}
        report_data["document_kind"] = kind
        output = {
            "report_data": report_data,
            "final_verdict": expected,
            "vulnerable_file": "",
        }
        if expected in _CONFIRMED_VERDICTS:
            output["cvss"] = {
                "vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",
                "base_score": 9.8,
                "severity": "Critical",
            }
        return output
    if node_key == "triage":
        return {
            "verdict": "tp",
            "confidence": 0.85,
            "why": ["[Mock] llm_gateway 已退役；SDK 未启用时的固定二审"],
            "summary": "[Mock] 可疑真洞：模拟入口可达危险点。",
            "reasoning": "[Mock] 攻击者可控输入经未消毒路径到达 sink。",
            "evidence": [{"file": "app.py", "lines": "1-1"}],
            "need": [],
            "attacker_controlled": True,
            "reaches_sink": True,
            "sanitizer": "none",
        }
    if node_key == "api_hunt":
        return {
            "suspects": [],
            "reviewed_count": len(input_json.get("endpoints") or []),
            "budget_exhausted": False,
        }
    return {}


def _validate_audit_output(output: dict) -> tuple[bool, str | None]:
    """audit 三值形状表(spec §3):平台只校验形状并路由,不判 kill_chain 真伪。"""
    gate = output.get("gate_verdict")
    if gate not in ("pass", "fail", "uncertain"):
        return False, "gate_verdict 必须是 pass|fail|uncertain"
    reason = output.get("gate_reason")
    if not isinstance(reason, str) or not reason.strip():
        return False, "gate_reason 不能为空"
    if gate == "pass":
        chain = output.get("kill_chain")
        if not isinstance(chain, str) or not chain.strip():
            return False, "pass 需要非空 kill_chain"
        payloads = output.get("payloads")
        if not isinstance(payloads, list) or len(payloads) < 1:
            return False, "pass 需要 payloads 至少 1 条"
        if not isinstance(output.get("runtime_dependent"), bool):
            return False, "pass 需要 runtime_dependent 为 bool"
        layers = output.get("defense_layers")
        if layers is None:
            output["defense_layers"] = []
        elif not isinstance(layers, list):
            return False, "pass 需要 defense_layers 为数组"
        claim = output.get("core_claim")
        if not isinstance(claim, str) or not claim.strip():
            return False, "pass 需要非空 core_claim"
        for item in payloads:
            if not isinstance(item, dict):
                return False, "pass 的 payloads 必须是请求模板对象"
            method = item.get("method")
            path = item.get("path")
            expected = item.get("expected_observable")
            if not isinstance(method, str) or not method.strip():
                return False, "pass 的 payloads 必须是请求模板对象"
            if not isinstance(path, str) or not path.strip():
                return False, "pass 的 payloads 必须是请求模板对象"
            if not isinstance(path, str) or not path.startswith("/"):
                return False, "payload path 必须以 / 开头"
            if not isinstance(expected, str) or not expected.strip():
                return False, "pass 的 payloads 必须是请求模板对象"
        if output.get("runtime_dependent") is True:
            facts = output.get("unresolved_facts")
            if (
                not isinstance(facts, list)
                or len(facts) < 1
                or any(not isinstance(f, str) or not f.strip() for f in facts)
            ):
                return False, "pass 需要 unresolved_facts 为非空字符串数组"
    elif gate == "fail":
        chain = output.get("kill_chain")
        if not isinstance(chain, str) or not chain.strip():
            return False, "fail 需要非空 kill_chain"
        layers = output.get("defense_layers")
        if not isinstance(layers, list) or len(layers) < 1:
            return False, "fail 需要 defense_layers 为长度≥1 的数组"
    else:
        payloads = output.get("payloads")
        if isinstance(payloads, list) and len(payloads) > 0:
            return False, "uncertain 不得带非空 payloads"
    return True, None


def document_kind_for_verdict(verdict: str | None) -> str:
    if verdict in _CONFIRMED_VERDICTS:
        return "vulnerability_report"
    return "verification_record"


def authoritative_verdict(repro: dict[str, Any] | None, audit: dict[str, Any] | None) -> str | None:
    repro = repro or {}
    audit = audit or {}
    if repro.get("verdict") in _VERDICTS:
        return str(repro["verdict"])
    gate = audit.get("gate_verdict")
    if gate == "fail":
        return "false_positive"
    if gate == "uncertain":
        return "needs_review"
    # reproduce 已跳过/无动态结果时，gate=pass 以白盒 code_reachable 收口
    if gate == "pass":
        return "code_reachable"
    return None


def _validate_report_data_markdown(report_data: Any, keys: tuple[str, ...]) -> tuple[bool, str | None]:
    """指定 8 节必须都是非空 Markdown 字符串；嵌套 object/array 不合格。"""
    if not isinstance(report_data, dict) or any(k not in report_data for k in keys):
        return False, "report_data 需要 8 节 Markdown 字符串"
    for key in keys:
        value = report_data.get(key)
        if not isinstance(value, str) or not value.strip():
            return False, f"report_data.{key} 必须是非空字符串"
    return True, None


def _validate_cvss(cvss: Any) -> tuple[bool, str | None]:
    if not isinstance(cvss, dict):
        return False, "cvss 需要 vector/base_score/severity"
    vector = cvss.get("vector")
    severity = cvss.get("severity")
    score = cvss.get("base_score")
    if not isinstance(vector, str) or not vector.strip():
        return False, "cvss 需要 vector/base_score/severity"
    if not isinstance(severity, str) or not severity.strip():
        return False, "cvss 需要 vector/base_score/severity"
    if not isinstance(score, (int, float)) or isinstance(score, bool):
        return False, "cvss 需要 vector/base_score/severity"
    return True, None


def _validate_evidence(
    evidence: Any, *, min_items: int = 1,
) -> tuple[bool, str | None]:
    if not isinstance(evidence, list):
        return False, "confirmed/partial 需要 evidence 至少 1 条"
    if len(evidence) < min_items:
        return False, "confirmed/partial 需要 evidence 至少 1 条"
    for item in evidence:
        if not isinstance(item, dict):
            return False, "evidence 条目需要非空 type 和 detail"
        typ = item.get("type")
        detail = item.get("detail")
        if not isinstance(typ, str) or not typ.strip():
            return False, "evidence 条目需要非空 type 和 detail"
        if not isinstance(detail, str) or not detail.strip():
            return False, "evidence 条目需要非空 type 和 detail"
    return True, None


def _has_cvss_payload(output: dict) -> bool:
    cvss = output.get("cvss")
    return cvss is not None and cvss != {}


def _validate_attempts(attempts: Any) -> tuple[bool, str | None]:
    if not isinstance(attempts, list) or len(attempts) < 1:
        return False, "attempts 需要至少 1 条"
    for item in attempts:
        if not isinstance(item, dict):
            return False, "attempts 条目必须是对象"
        for key in _ATTEMPT_KEYS:
            val = item.get(key)
            if key == "response_status":
                if isinstance(val, bool) or not isinstance(val, (int, str)):
                    return False, "attempts 需要 purpose/request/response_status/response_excerpt/observation/result"
                if isinstance(val, str) and not val.strip():
                    return False, "attempts 需要 purpose/request/response_status/response_excerpt/observation/result"
            elif not isinstance(val, str) or not val.strip():
                return False, "attempts 需要 purpose/request/response_status/response_excerpt/observation/result"
    return True, None


def _container_to_host_path(raw: str, host_workdir: str | None) -> str:
    """容器绝对路径 → 宿主路径（agent 只看得见 /workspace bind mount）。

    模型按 SKILL 指示写 {source_path}/VULN-*/img/...（容器视角 /workspace/...），
    worker 在宿主机校验：/workspace/<repo>/x → {host_workdir}/<repo>/x；
    /workspace/x → {host_workdir}/x。非容器前缀原样返回（相对路径场景）。
    """
    normalized = raw.replace("\\", "/")
    prefix = "/workspace/"
    if normalized.startswith(prefix):
        rel = normalized[len(prefix):]
        if not host_workdir:
            return rel
        return str(Path(host_workdir) / rel)
    return raw


def _validate_screenshots(shots: list[str], host_workdir: str | None) -> tuple[bool, str | None]:
    root = Path(host_workdir).resolve() if host_workdir else None
    for raw in shots:
        suffix = Path(raw).suffix.lower()
        if suffix not in _IMAGE_EXTS:
            return False, "screenshots 必须是工作区内真实图片（禁止 .txt）"
        if root is None:
            continue
        candidate = Path(_container_to_host_path(raw, host_workdir))
        path = candidate if candidate.is_absolute() else (root / candidate)
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except ValueError:
            return False, "screenshots 必须位于任务工作区"
        if not resolved.is_file():
            return False, "screenshots 文件不存在"
    return True, None


_POC_LANGS = ("python", "bash", "other")
_POC_FENCE = {"python": "python", "bash": "bash", "other": "text"}


def _poc_has_code(poc: Any) -> bool:
    return isinstance(poc, dict) and isinstance(poc.get("code"), str) and bool(str(poc.get("code")).strip())


def _validate_poc(poc: Any, *, required: bool) -> tuple[bool, str | None]:
    if not required:
        if poc in (None, {}) or poc is False:
            return True, None
        if _poc_has_code(poc):
            return False, "未确认判定不得交 poc"
        return True, None
    if not isinstance(poc, dict):
        return False, "confirmed/partial 需要 poc.language/filename/code/usage"
    lang = poc.get("language")
    if lang not in _POC_LANGS:
        return False, "poc.language 必须是 python|bash|other"
    for key in ("filename", "code", "usage"):
        val = poc.get(key)
        if not isinstance(val, str) or not val.strip():
            return False, "confirmed/partial 需要 poc.language/filename/code/usage"
    if len(poc["filename"]) > 255:
        return False, "poc.filename 长度不能超过 255"
    if len(poc["usage"]) > 1024:
        return False, "poc.usage 长度不能超过 1024"
    if lang != "python":
        reason = poc.get("language_reason")
        if not isinstance(reason, str) or not reason.strip():
            return False, "非 python 的 poc 需要 language_reason"
    return True, None


def render_poc_markdown(poc: dict[str, Any]) -> str:
    fence = _POC_FENCE.get(str(poc.get("language") or ""), "text")
    code = str(poc.get("code") or "").rstrip()
    usage = str(poc.get("usage") or "").strip()
    return f"```{fence}\n{code}\n```\n\n用法：`{usage}`\n"


def apply_poc_to_report_output(
    output: dict[str, Any], poc: dict[str, Any] | None, expected_verdict: str,
) -> dict[str, Any]:
    if expected_verdict in _CONFIRMED_VERDICTS:
        ok, err = _validate_poc(poc, required=True)
        if not ok:
            if poc is None:
                raise RuntimeError("confirmed/partial 缺少 reproduce.poc")
            raise RuntimeError(err or "confirmed/partial 缺少 reproduce.poc")
        assert isinstance(poc, dict)
        rd = dict(output.get("report_data") or {})
        rd["poc_commands"] = render_poc_markdown(poc)
        output["report_data"] = rd
        output["poc"] = poc
        return output
    output.pop("poc", None)
    rd = output.get("report_data")
    if isinstance(rd, dict):
        rd.pop("poc_commands", None)
    return output


def _validate_reproduce_output(
    output: dict, *, host_workdir: str | None = None,
) -> tuple[bool, str | None]:
    """reproduce 只交测试事实。平台只校形状，不判 HTTP 真伪。"""
    if "report_data" in output:
        return False, "reproduce 不得交 report_data，报告由 report 节点撰写"
    verdict = output.get("verdict")
    if verdict not in _VERDICTS:
        return False, (
            "verdict 必须是 confirmed|partial|code_reachable|code_smell|"
            "false_positive|not_reproduced"
        )
    if verdict in _CONFIRMED_VERDICTS:
        if output.get("reproduced") is not True:
            return False, "confirmed/partial 需要 reproduced=true"
        ok, err = _validate_evidence(output.get("evidence"))
        if not ok:
            return False, err
        ok, err = _validate_cvss(output.get("cvss"))
        if not ok:
            return False, err
    else:
        if verdict in ("false_positive", "not_reproduced") and output.get("reproduced") is not False:
            return False, "false_positive/not_reproduced 需要 reproduced=false"
        if _has_cvss_payload(output):
            return False, "未确认判定不得交 cvss"

    evidence = output.get("evidence")
    if evidence is not None:
        # 未确认档允许空数组；有条目时仍须 type/detail 非空
        ok, err = _validate_evidence(evidence, min_items=0)
        if not ok:
            return False, err

    ok, err = _validate_attempts(output.get("attempts"))
    if not ok:
        return False, err

    shots = output.get("screenshots")
    if shots is None:
        output["screenshots"] = []
    elif not isinstance(shots, list) or any(not isinstance(s, str) for s in shots):
        return False, "screenshots 必须是字符串数组"
    else:
        ok, err = _validate_screenshots(shots, host_workdir)
        if not ok:
            return False, err

    vf = output.get("vulnerable_file")
    if vf is None:
        output["vulnerable_file"] = ""
    elif not isinstance(vf, str):
        return False, "vulnerable_file 必须是字符串"
    need_poc = verdict in _CONFIRMED_VERDICTS
    ok, err = _validate_poc(output.get("poc"), required=need_poc)
    if not ok:
        return False, err
    return True, None


def _validate_report_output(output: dict) -> tuple[bool, str | None]:
    """report 按权威 verdict 选择漏洞报告或验证记录。"""
    verdict = output.get("final_verdict")
    if verdict not in _REPORT_VERDICTS:
        return False, (
            "final_verdict 必须是 confirmed|partial|code_reachable|code_smell|"
            "false_positive|not_reproduced|needs_review"
        )
    rd = output.get("report_data")
    if not isinstance(rd, dict):
        return False, "report_data 需要 8 节 Markdown 字符串"
    kind = rd.get("document_kind")
    if kind not in ("vulnerability_report", "verification_record"):
        return False, "report_data.document_kind 必须是 vulnerability_report 或 verification_record"
    expected_kind = document_kind_for_verdict(verdict)
    if kind != expected_kind:
        return False, f"{verdict} 需要 document_kind={expected_kind}"
    if kind == "vulnerability_report":
        ok, err = _validate_report_data_markdown(rd, REPORT_SECTION_KEYS)
        if not ok:
            return False, err
        return _validate_cvss(output.get("cvss"))
    if rd.get("poc_commands"):
        return False, "验证记录不得含 poc_commands"
    ok, err = _validate_report_data_markdown(rd, RECORD_SECTION_KEYS)
    if not ok:
        return False, err
    if _has_cvss_payload(output):
        return False, "未确认判定不得交 cvss"
    return True, None


def validate_initial_creds(value: Any) -> tuple[bool, str | None]:
    if not isinstance(value, dict) or not value:
        return False, "initial_creds 必须明确提供账密、免登录或凭据来源说明"

    username = value.get("username")
    password = value.get("password")
    has_username = isinstance(username, str) and bool(username.strip())
    has_password = isinstance(password, str) and bool(password.strip())
    if has_username or has_password:
        if has_username and has_password:
            return True, None
        return False, "initial_creds 的 username/password 必须同时为非空字符串"

    if value.get("auth_required") is False:
        return True, None

    note = value.get("note")
    if isinstance(note, str) and note.strip():
        return True, None

    return False, "initial_creds 必须明确提供账密、auth_required=false 或非空 note"


def _validate_triage_output(output: dict) -> tuple[bool, str | None]:
    """T3 二审形状：why + 叙事必填；可疑真洞还要证据与合格门字段（Agent 输出不可信）。"""
    verdict = output.get("verdict")
    if verdict not in ("tp", "fp", "need_more_context"):
        return False, "verdict 必须是 tp|fp|need_more_context"
    why = output.get("why")
    if not isinstance(why, list) or not any(isinstance(x, str) and x.strip() for x in why):
        return False, "why 必须为非空字符串数组"
    summary = output.get("summary")
    if not (isinstance(summary, str) and summary.strip()):
        return False, "summary 必须为非空字符串（§2.3.1）"
    reasoning = output.get("reasoning")
    if not (isinstance(reasoning, str) and reasoning.strip()):
        return False, "reasoning 必须为非空字符串（§2.3.1）"
    if verdict != "tp":
        return True, None
    evidence = output.get("evidence")
    if not isinstance(evidence, list) or len(evidence) < 1:
        return False, "可疑真洞必须提供非空 evidence"
    if output.get("attacker_controlled") is not True:
        return False, "可疑真洞必须 attacker_controlled=true"
    if output.get("reaches_sink") is not True:
        return False, "可疑真洞必须 reaches_sink=true"
    if output.get("sanitizer") not in ("none", "bypassable"):
        return False, "可疑真洞的 sanitizer 必须是 none 或 bypassable"
    return True, None


def _normalize_hunt_confidence(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        conf = float(value)
        return conf if 0.0 <= conf <= 1.0 else None
    if isinstance(value, str):
        raw = value.strip().upper()
        if raw == "HIGH":
            return 0.9
        if raw == "MEDIUM":
            return 0.75
        if raw == "LOW":
            return 0.4
        try:
            conf = float(raw)
        except ValueError:
            return None
        return conf if 0.0 <= conf <= 1.0 else None
    return None


def _validate_api_hunt_output(output: dict) -> tuple[bool, str | None]:
    """API Hunt 只校验候选形状，不在发现阶段执行 TP 合格门。

    三个安全判断必须显式给出，但允许未知或反证；最终真假由 triage 决定。
    """
    suspects = output.get("suspects")
    if not isinstance(suspects, list):
        return False, "suspects 必须为数组"
    reviewed = output.get("reviewed_count")
    if not isinstance(reviewed, int) or reviewed < 0:
        return False, "reviewed_count 必须为非负整数"
    for i, s in enumerate(suspects):
        if not isinstance(s, dict):
            return False, f"suspects[{i}] 必须为对象"
        if not (isinstance(s.get("file_path"), str) and s["file_path"].strip()):
            return False, f"suspects[{i}] 必须有非空 file_path（locus）"
        if not (isinstance(s.get("endpoint_id"), str) and s["endpoint_id"].strip()):
            return False, f"suspects[{i}] 必须有非空 endpoint_id"
        why = s.get("why")
        if (
            not isinstance(why, list)
            or not any(isinstance(x, str) and x.strip() for x in why)
        ):
            return False, f"suspects[{i}].why 必须为非空字符串数组"
        evidence = s.get("evidence")
        if not isinstance(evidence, list) or len(evidence) < 1:
            return False, f"suspects[{i}] 必须提供非空 evidence"
        for field in ("attacker_controlled", "reaches_sink"):
            if field not in s:
                return False, f"suspects[{i}].{field} 必须显式给出 true|false|null"
            if s.get(field) is not None and not isinstance(s.get(field), bool):
                return False, f"suspects[{i}].{field} 必须是 true|false|null"
        if "sanitizer" not in s:
            return False, f"suspects[{i}].sanitizer 必须显式给出"
        if s.get("sanitizer") not in (None, "none", "bypassable", "effective", "unknown"):
            return False, (
                f"suspects[{i}].sanitizer 必须是 "
                "none|bypassable|effective|unknown|null"
            )
        if "confidence" in s and s.get("confidence") is not None:
            if _normalize_hunt_confidence(s.get("confidence")) is None:
                return False, (
                    f"suspects[{i}].confidence 必须是 0–1、HIGH/MEDIUM/LOW 或 null"
                )
        summary = s.get("summary")
        if not (isinstance(summary, str) and summary.strip()):
            return False, f"suspects[{i}].summary 必须为非空字符串（§2.3.1）"
        reasoning = s.get("reasoning")
        if not (isinstance(reasoning, str) and reasoning.strip()):
            return False, f"suspects[{i}].reasoning 必须为非空字符串（§2.3.1）"
    return True, None


def validate_output(
    node_key: str,
    output: dict,
    *,
    host_workdir: str | None = None,
) -> tuple[bool, str | None]:
    """校验 AI 节点 output 是否满足最小 schema。"""
    schema = NODE_OUTPUT_SCHEMAS.get(node_key)
    if not schema:
        return True, None
    for field_name in schema["required"]:
        if field_name not in output:
            return False, f"缺必需字段: {field_name}"
    if node_key == "env_ready":
        return validate_initial_creds(output.get("initial_creds"))
    if node_key == "audit":
        return _validate_audit_output(output)
    if node_key == "reproduce":
        return _validate_reproduce_output(output, host_workdir=host_workdir)
    if node_key == "report":
        return _validate_report_output(output)
    if node_key == "triage":
        return _validate_triage_output(output)
    if node_key == "api_hunt":
        return _validate_api_hunt_output(output)
    return True, None


async def _run_one_container_unthrottled(
    *,
    node_key: str,
    input_json: dict[str, Any],
    host_workdir: str,
    runner_env: dict[str, str],
    on_event: Callable[[dict], None] | None,
    task_id: str | None,
    meta_out: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """单轮容器执行：写 .node.json → 起容器 → 读 .node_output.json。

    不做 schema 校验（调用方决定）；容器/输出层失败抛 AgentRunnerError。
    meta_out 非空时回填容器侧真实 prompt/usage（审计链，spec §4.2）。
    """
    # 轻工位（triage/profile）凭据最小化（spec §7.4）：
    # 任务级凭据 env 一律剥离，只保留 SDK 必需变量；.secrets/ 用空 tmpfs 遮蔽
    env = dict(runner_env or {})
    hide_paths: tuple[str, ...] = ()
    if node_key in _LIGHT_WORKSTATION_NODES:
        env = {k: v for k, v in env.items() if _is_sdk_env_key(k)}
        hide_paths = ("/workspace/.secrets",)

    # Mock 模式:SDK 未启用时直接返回模拟 output(不起容器)
    from app.core.config import get_settings
    if not get_settings().claude_agent_sdk_enabled:
        logger.info(f"[Mock] AI 节点 {node_key} 返回模拟 output(SDK 未启用)")
        output = _mock_output(node_key, input_json)
        if on_event:
            on_event({"type": "phase.updated", "phase": node_key, "message": f"[Mock] {node_key} 完成"})
        return output

    # 1. 每次容器执行使用独立控制目录。终认线索会共享源码工作区，固定的
    #    .node*.json 会互相覆盖，造成输入/输出串线。
    #    先清旧的 .node_output.json:回喂重跑每轮重调本函数,
    #    若上轮 agent 没调 submit_result,容器会读到上轮遗留的旧 output(run_one
    #    退出码 0 → 本函数读到旧数据,静默用错)。.node_meta.json 同理。
    execution_id = uuid4().hex
    control_dir = Path(host_workdir) / ".runner" / execution_id
    control_dir.mkdir(parents=True, mode=0o777, exist_ok=False)
    control_dir.chmod(0o777)
    node_input_path = control_dir / "node.json"
    node_output_path = control_dir / "node_output.json"
    node_meta_path = control_dir / "node_meta.json"
    node_input_path.write_text(
        json.dumps({"node_key": node_key, "input_json": input_json}, ensure_ascii=False),
        encoding="utf-8",
    )

    skill_dir = resolve_node_skill_dir(node_key)

    # 2. 构造 spec + 起容器(NODE_KEY env + skill 卷映射)
    spec = AgentRunnerSpec(
        env={
            **env,
            "NODE_KEY": node_key,
            "NODE_INPUT_PATH": f"/workspace/.runner/{execution_id}/node.json",
            "NODE_OUTPUT_PATH": f"/workspace/.runner/{execution_id}/node_output.json",
            "NODE_META_PATH": f"/workspace/.runner/{execution_id}/node_meta.json",
        },
        host_workdir=host_workdir,
        skill_host_dir=str(skill_dir),
        extra_labels={"crucible.task_id": task_id, "task_id": task_id} if task_id else {},
        hide_workspace_paths=hide_paths,
    )

    last_fail = ""
    llm_fail = ""
    dsml_leak = False
    stream_usage: dict[str, Any] = {}

    def _on_event(event: dict) -> None:
        nonlocal last_fail, llm_fail, dsml_leak
        et = event.get("type")
        if et == "agent.completed":
            for k in ("usage", "model_usage"):
                if event.get(k) is not None:
                    stream_usage[k] = event[k]
        if et in ("agent.message", "agent.thinking", "agent.completed"):
            blob = " ".join(
                str(event.get(k) or "")
                for k in ("text", "reasoning", "content", "message")
            )
            # DeepSeek 偶发把 tool_use 泄成 DSML 纯文本，CLI 无法执行工具
            if "DSML" in blob or "｜DSML｜" in blob:
                dsml_leak = True
        if et in ("agent.failed", "raw"):
            err = (
                event.get("error")
                or event.get("content")
                or event.get("message")
                or ""
            )
            last_fail = err
            if is_llm_api_failure(err) and not llm_fail:
                llm_fail = err
        if on_event:
            on_event(event)

    exit_code, summary = await asyncio.to_thread(
        agent_runner_manager.run_with_streaming, spec, _on_event
    )

    # 3. 读 .node_output.json(submit_result 写的)
    output_path = node_output_path
    if not output_path.exists():
        stderr_tail = summary.get("stderr_tail", "") if summary else ""
        # 结构化 agent.failed 优先于 stdout JSONL 截尾：长会话的
        # agent.completed 用量字段会把真正的 no_submit 埋进截断噪声。
        combined = (last_fail or stderr_tail or "").strip()
        # LLM 网关错误（如 401 余额不足）常出现在较早的 agent.failed；
        # 末尾可能是 SDK 误报或「未调用 submit_result」次生事件，须优先保留 llm_fail。
        primary = (llm_fail or combined).strip()
        if dsml_leak and "DSML" not in primary:
            primary = (
                "模型输出含 DSML 工具标记（未形成有效 tool_use）；" + primary
            ).strip("；")
        detail = primary[-600:]
        if exit_code == 137:
            raise AgentRunnerError(
                f"AI 节点 {node_key} 容器被 SIGKILL 终止(exit=137，"
                f"多为任务取消、外部强杀或 OOM): {detail}"
            )
        if llm_fail or classify_llm_api_error(detail):
            raise AgentRunnerError(f"AI 节点 {node_key} LLM 调用失败: {detail}")
        raise AgentRunnerError(
            f"AI 节点 {node_key} 未产出 .node_output.json (exit={exit_code}): {detail}"
        )

    try:
        output = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AgentRunnerError(f"AI 节点 {node_key} output JSON 解析失败: {e}") from e

    # 4.5 容器侧真实 prompt/usage（审计链，spec §4.2）；读后即清，读失败不阻断
    if node_meta_path.exists():
        try:
            if meta_out is not None:
                fresh_meta = json.loads(node_meta_path.read_text(encoding="utf-8"))
                prev_usage = meta_out.get("usage")
                prev_mu = meta_out.get("model_usage")
                meta_out.clear()
                meta_out.update(fresh_meta)
                if (
                    isinstance(prev_usage, dict)
                    and isinstance(fresh_meta.get("usage"), dict)
                ):
                    # 形状回喂重试时 meta 每轮整体覆盖：usage 必须跨轮累加，
                    # 否则台账只记最后一轮、预算会系统性晚停。
                    # cache_* 同为 SDK/API 回传字段，禁止自算，只做各轮相加。
                    def _u(d: dict, *keys: str) -> int:
                        for k in keys:
                            v = d.get(k)
                            if isinstance(v, (int, float)) and not isinstance(v, bool):
                                return int(v)
                        return 0

                    meta_out["usage"] = {
                        "prompt_tokens": _u(
                            prev_usage, "prompt_tokens", "input_tokens", "inputTokens",
                        )
                        + _u(
                            fresh_meta["usage"], "prompt_tokens", "input_tokens", "inputTokens",
                        ),
                        "completion_tokens": _u(
                            prev_usage, "completion_tokens", "output_tokens", "outputTokens",
                        )
                        + _u(
                            fresh_meta["usage"],
                            "completion_tokens", "output_tokens", "outputTokens",
                        ),
                        "cache_read_input_tokens": _u(
                            prev_usage, "cache_read_input_tokens", "cacheReadInputTokens",
                        )
                        + _u(
                            fresh_meta["usage"],
                            "cache_read_input_tokens", "cacheReadInputTokens",
                        ),
                        "cache_creation_input_tokens": _u(
                            prev_usage,
                            "cache_creation_input_tokens", "cacheCreationInputTokens",
                        )
                        + _u(
                            fresh_meta["usage"],
                            "cache_creation_input_tokens", "cacheCreationInputTokens",
                        ),
                    }
                fresh_mu = fresh_meta.get("model_usage")
                if prev_mu is not None and fresh_mu is not None:
                    # 多轮各保留一份；台账侧 normalize 时对整份求和。
                    merged: list = []
                    if isinstance(prev_mu, list):
                        merged.extend(prev_mu)
                    else:
                        merged.append(prev_mu)
                    if isinstance(fresh_mu, list):
                        merged.extend(fresh_mu)
                    else:
                        merged.append(fresh_mu)
                    meta_out["model_usage"] = merged
        except (json.JSONDecodeError, OSError):
            pass
        try:
            node_meta_path.unlink()
        except OSError:
            pass

    if meta_out is not None:
        apply_stream_usage_fallback(meta_out, stream_usage)

    return output


async def _run_one_container(
    *,
    node_key: str,
    input_json: dict[str, Any],
    host_workdir: str,
    runner_env: dict[str, str],
    on_event: Callable[[dict], None] | None,
    task_id: str | None,
    meta_out: dict[str, Any] | None = None,
    reproduce_scope: str | None = None,
) -> dict[str, Any]:
    """按运行时预算等待槽位；等待与容器执行均不设总时长超时。"""
    from app.core.config import get_settings

    call = dict(
        node_key=node_key,
        input_json=input_json,
        host_workdir=host_workdir,
        runner_env=runner_env,
        on_event=on_event,
        task_id=task_id,
        meta_out=meta_out,
    )
    # Mock 不占 Docker/AI 资源，也不依赖 Redis。
    if not get_settings().claude_agent_sdk_enabled:
        return await _run_one_container_unthrottled(**call)

    from app.contexts.agent.runner_slots import agent_runner_slot, reproduce_slot

    def _report_wait(message: str) -> Callable[[], None]:
        def _emit_wait() -> None:
            if on_event:
                on_event({"type": "phase.updated", "phase": node_key, "message": message})

        return _emit_wait

    if reproduce_scope:
        # 先占靶场作用域，再占真正稀缺的 AI 容器，避免等待靶场时空耗全局槽。
        async with reproduce_slot(
            reproduce_scope,
            on_wait=_report_wait("同一靶场正在复现，等待靶场槽位"),
        ):
            async with agent_runner_slot(
                task_id=task_id,
                node_key=node_key,
                on_wait=_report_wait("全局 AI 容器已满，等待资源槽位"),
            ):
                return await _run_one_container_unthrottled(**call)
    async with agent_runner_slot(
        task_id=task_id,
        node_key=node_key,
        on_wait=_report_wait("全局 AI 容器已满，等待资源槽位"),
    ):
        return await _run_one_container_unthrottled(**call)


# 轻工位节点（spec §7.4：不注入任务凭据；工具仅 Read/Grep/Glob）
_LIGHT_WORKSTATION_NODES = frozenset({"triage", "profile", "api_hunt"})
# SDK 运行必需的 env 前缀/白名单；凭据最小化时 runner_env 只保留这些
_SDK_ENV_PREFIXES = ("ANTHROPIC_", "CLAUDE_")
_SDK_ENV_KEYS = frozenset({
    "API_TIMEOUT_MS", "PYTHONUNBUFFERED", "HOME", "NODE_KEY", "PYTHONPATH",
})


def _is_sdk_env_key(key: str) -> bool:
    return key.startswith(_SDK_ENV_PREFIXES) or key in _SDK_ENV_KEYS


async def run_ai_node(
    *,
    node_key: str,
    input_json: dict[str, Any],
    host_workdir: str,
    runner_env: dict[str, str],
    on_event: Callable[[dict], None] | None = None,
    task_id: str | None = None,
    validate: bool = True,
    meta_out: dict[str, Any] | None = None,
    reproduce_scope: str | None = None,
) -> dict[str, Any]:
    """起 agent-runner 容器跑一个 AI 节点,返回 output_json。

    失败抛 AgentRunnerError。
    SDK 未启用(claude_agent_sdk_enabled=False)时走 mock,返回模拟 output 供编排链路联调。

    validate=False 供自带回喂环的调用方（env_ready 排障循环）使用：平台层
    不再对 output 先斩后奏，形状校验交给调用方的逐项检查 + 回喂重试语义
    （2026-08-19 修复：否则 env_ready 的 initial_creds 回喂分支永不可达）。

    meta_out 非空时回填容器侧真实 prompt/usage（Adjudication 审计链）。
    """
    output = await _run_one_container(
        node_key=node_key,
        input_json=input_json,
        host_workdir=host_workdir,
        runner_env=runner_env,
        on_event=on_event,
        task_id=task_id,
        meta_out=meta_out,
        reproduce_scope=reproduce_scope,
    )

    # 4. schema 校验（validate=False 时跳过：调用方自带回喂环）
    if validate:
        ok, err = validate_output(node_key, output, host_workdir=host_workdir)
        if not ok:
            raise AgentRunnerError(f"AI 节点 {node_key} output 校验失败: {err}")

    return output


AI_NODE_MAX_SHAPE_RETRIES = 2
"""回喂重跑上限：形状修复通常是补一两个字段，2 轮足够；再多说明模型
系统性不服从 schema，重跑只是烧钱（白盒审计一轮 10-30 分钟）。"""


async def run_ai_node_with_shape_retry(
    *,
    node_key: str,
    input_json: dict[str, Any],
    host_workdir: str,
    runner_env: dict[str, str],
    on_event: Callable[[dict], None] | None = None,
    task_id: str | None = None,
    meta_out: dict[str, Any] | None = None,
    reproduce_scope: str | None = None,
) -> dict[str, Any]:
    """AI 节点 + 形状回喂环（P0#1 修复）。

    audit/reproduce/report 的一次性形状失败不再直接判死：第一轮
    validate_output 失败后，把校验错误 + 上轮 submit 摘要拼进 input_json
    重新起容器，让模型修自己的 output 形状。容器/超时/no_submit 等执行层
    失败仍立即抛（重跑救不了，别烧钱）。

    与 env_ready 的节点级排障环（多轮、含 compose/探活等平台动作）不同，
    这里只救「模型差一两个字段」这一种失败模式。

    meta_out 非空时回填最后一轮容器侧真实 prompt/usage（Adjudication 审计链）。
    """
    from app.contexts.agent.node_failure import snapshot_attempt

    base_input = dict(input_json)
    last_submit: dict[str, Any] | None = None
    last_err: str | None = None

    for attempt in range(1, AI_NODE_MAX_SHAPE_RETRIES + 2):
        # 回喂：attempt>1 时附上轮校验错误 + 提交摘要（模型看不见上轮容器，
        # 必须显式带回）
        run_input = dict(base_input)
        if attempt > 1:
            run_input["attempt"] = attempt
            run_input["previous_error"] = (
                f"上一轮 submit_result 的 output 未通过平台 schema 校验:\n{last_err}"
            )
            if last_submit is not None:
                run_input["previous_submit_summary"] = _summarize_submit(last_submit)

        output = await _run_one_container(
            node_key=node_key,
            input_json=run_input,
            host_workdir=host_workdir,
            runner_env=runner_env,
            on_event=on_event,
            task_id=task_id,
            meta_out=meta_out,
            reproduce_scope=reproduce_scope,
        )

        ok, err = validate_output(node_key, output, host_workdir=host_workdir)
        if ok:
            return output

        last_submit = output
        last_err = err
        try:
            snapshot_attempt(
                host_workdir,
                node_key,
                attempt,
                previous_error=last_err,
                platform_error=f"failed_stage=shape_validation\n{last_err}",
                submit=output,
            )
        except Exception:  # noqa: BLE001
            logger.warning("形状回喂快照失败 node=%s attempt=%s", node_key, attempt, exc_info=True)
        if on_event:
            on_event({
                "type": "phase.updated",
                "phase": node_key,
                "message": (
                    f"output 形状不合格，回喂重跑"
                    f"（{attempt}/{AI_NODE_MAX_SHAPE_RETRIES + 1}）：{(err or '')[:200]}"
                ),
            })

    raise AgentRunnerError(
        f"AI 节点 {node_key} output 形状校验 {AI_NODE_MAX_SHAPE_RETRIES + 1} 轮全失败: {last_err}"
    )


def _summarize_submit(submit: dict[str, Any]) -> str:
    """给模型看的上轮提交摘要：关键字段截断，避免 input 爆炸。"""
    keep = {}
    for k, v in (submit or {}).items():
        if isinstance(v, str):
            keep[k] = v[:300]
        elif isinstance(v, (int, float, bool)) or v is None:
            keep[k] = v
        elif isinstance(v, list):
            keep[k] = f"<list:{len(v)} 项>"
        elif isinstance(v, dict):
            keep[k] = f"<dict:{sorted(v.keys())}>"
        else:
            keep[k] = str(v)[:100]
    return json.dumps(keep, ensure_ascii=False)
