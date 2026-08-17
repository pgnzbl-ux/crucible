"""AI 节点容器编排 — 每节点起一个 agent-runner 容器调 SDK。

流程:
  1. 写 .node.json(node_key + input_json)到 host_workdir
  2. 起 agent-runner 容器(bind mount host_workdir + 注入 ANTHROPIC_* env + NODE_KEY)
  3. 容器内 run_one.py 按 NODE_KEY 选 agent,跑完调 submit_result(MCP 工具)
  4. submit_result 把 input 写到 /workspace/.node_output.json
  5. worker 读 .node_output.json → schema 校验 → 返回 output_json

submit_result 注入机制(SDK 0.2.134 PoC 确认):
  create_sdk_mcp_server + @tool,容器内 run_one.py 构造。
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

from app.core.agent_runner import (
    AgentRunnerError,
    AgentRunnerSpec,
    agent_runner_manager,
)

logger = logging.getLogger(__name__)

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
_ATTEMPT_KEYS = (
    "purpose", "request", "response_status", "response_excerpt", "observation", "result",
)
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

# 各 AI 节点的 output schema(校验最小必需字段,spec §1.3)
NODE_OUTPUT_SCHEMAS: dict[str, dict] = {
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
        "optional": ["evidence", "screenshots", "cvss", "vulnerable_file"],
    },
    "report": {
        "required": ["report_data", "final_verdict"],
        "optional": ["cvss", "vulnerable_file"],
    },
}

# 各 AI 节点的 submit_result 工具 input schema(传给容器内 run_one.py 构造工具)
NODE_INPUT_SCHEMAS: dict[str, dict] = {
    "profile": {
        "type": "object",
        "properties": {
            "is_web": {"type": "boolean", "description": "是否 web / web api"},
            "language": {"type": "string", "description": "主语言"},
            "framework": {"type": "string", "description": "Web 框架"},
            "port": {"type": "integer", "description": "默认或配置中的监听端口"},
            "has_dockerfile": {"type": "boolean"},
            "has_compose": {"type": "boolean"},
            "detected_services": {"type": "array", "description": "中间件名列表"},
            "summary": {"type": "string", "description": "一两句项目全景"},
            "start_command": {"type": "string", "description": "文档中的启动命令"},
            "non_web_reason": {"type": "string", "description": "非 web 时的类型说明"},
        },
        "required": ["is_web"],
    },
    "env_ready": {
        "type": "object",
        "properties": {
            "target_url": {"type": "string", "description": "靶场访问地址"},
            "compose_path": {"type": "string", "description": ".vuln-env/docker-compose.yml 路径"},
            "transport_shape": {"type": "object", "description": "协议/端口/TLS 等"},
            "initial_creds": {
                "type": "object",
                "description": "实际可用的已有/靶场初始化账号；确认无登录功能写 {auth_required: false}；有登录但无法自动提供凭据写 {note: ...}",
                "properties": {
                    "username": {"type": "string", "minLength": 1},
                    "password": {"type": "string", "minLength": 1},
                    "login_url": {"type": "string"},
                    "auth_required": {"type": "boolean", "enum": [False]},
                    "note": {"type": "string", "minLength": 1},
                },
                "anyOf": [
                    {"required": ["username", "password"]},
                    {"required": ["auth_required"]},
                    {"required": ["note"]},
                ],
            },
            "started_containers": {"type": "array", "description": "启动的容器名列表"},
        },
        "required": ["target_url", "compose_path", "initial_creds"],
    },
    "audit": {
        "type": "object",
        "properties": {
            "kill_chain": {"type": "string", "description": "entry→sink 完整调用链"},
            "defense_layers": {"type": "array", "description": "每层防御 + 是否 bypass"},
            "payloads": {
                "type": "array",
                "description": "HTTP 请求模板候选",
                "items": {
                    "type": "object",
                    "properties": {
                        "method": {"type": "string", "description": "HTTP 方法"},
                        "path": {"type": "string", "description": "必须以 / 开头的路径"},
                        "expected_observable": {"type": "string", "description": "预期可观察危害"},
                        "headers": {"type": "object", "description": "可选请求头"},
                        "body": {"type": "string", "description": "可选请求体"},
                        "content_type": {"type": "string", "description": "可选 Content-Type"},
                    },
                    "required": ["method", "path", "expected_observable"],
                },
            },
            "gate_verdict": {
                "type": "string",
                "enum": ["pass", "fail", "uncertain"],
                "description": "Phase 2.5 三问结论:纸上通/runtime-dependent=pass;结构性阻断=fail;描述对不上/锁不住 harm=uncertain",
            },
            "gate_reason": {"type": "string", "description": "三问各一句或阻断/待复核原因"},
            "runtime_dependent": {"type": "boolean"},
            "core_claim": {"type": "string", "description": "一条 HTTP 可观察危害"},
            "unresolved_facts": {
                "type": "array",
                "items": {"type": "string"},
                "description": "runtime_dependent 时缺的运行时事实",
            },
        },
        "required": ["gate_verdict", "gate_reason"],
    },
    "reproduce": {
        "type": "object",
        "properties": {
            "reproduced": {"type": "boolean", "description": "是否真实复现"},
            "evidence": {"type": "array", "description": "证据列表"},
            "attempts": {"type": "array", "description": "每次 HTTP 尝试的结构化记录"},
            "screenshots": {"type": "array", "description": "工作区内真实截图路径"},
            "verdict": {
                "type": "string",
                "enum": ["confirmed", "partial", "code_reachable", "code_smell", "false_positive", "not_reproduced"],
                "description": "6 档判定",
            },
            "cvss": {"type": "object", "description": "仅 confirmed/partial 的 CVSS"},
            "vulnerable_file": {"type": "string", "description": "漏洞文件定位"},
        },
        "required": ["verdict", "reproduced", "attempts"],
    },
    "report": {
        "type": "object",
        "properties": {
            "report_data": {"type": "object", "description": "document_kind + 8 节 Markdown"},
            "final_verdict": {
                "type": "string",
                "enum": ["confirmed", "partial", "code_reachable", "code_smell", "false_positive", "not_reproduced"],
            },
            "cvss": {"type": "object", "description": "仅漏洞报告的 CVSS"},
            "vulnerable_file": {"type": "string"},
        },
        "required": ["report_data", "final_verdict"],
    },
}


def rewrite_url_for_agent_container(url: str | None) -> str | None:
    """把宿主机靶标改写成 agent-runner 容器可达的 host.docker.internal。"""
    from app.contexts.agent.target_url import rewrite_url_for_agent_container as _rewrite
    return _rewrite(url)


def _mock_output(node_key: str, input_json: dict[str, Any]) -> dict[str, Any]:
    """Mock 模式:SDK 未启用时返回模拟 output(通过 schema 校验),供编排链路联调。"""
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
            "reproduced": True,
            "evidence": [{"type": "http_response", "detail": "[Mock] 200 OK, payload reflected"}],
            "attempts": [{
                "purpose": "[Mock] 确认核心危害",
                "request": "curl -sS -i http://host.docker.internal:8080/login",
                "response_status": 200,
                "response_excerpt": "[Mock] payload reflected",
                "observation": "[Mock] HTTP 可观察危害",
                "result": "observed_harm",
            }],
            "screenshots": [],
            "verdict": "confirmed",
            "cvss": {
                "vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",
                "base_score": 9.8,
                "severity": "Critical",
            },
            "vulnerable_file": "app/mock.py",
        }
    if node_key == "report":
        expected = input_json.get("expected_verdict")
        if expected not in _VERDICTS:
            repro = input_json.get("reproduce") or {}
            expected = repro.get("verdict") if repro.get("verdict") in _VERDICTS else None
        if expected not in _VERDICTS:
            audit = input_json.get("audit") or {}
            expected = "false_positive" if audit.get("gate_verdict") == "fail" else "confirmed"
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
    if audit.get("gate_verdict") == "fail":
        return "false_positive"
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


def _validate_screenshots(shots: list[str], host_workdir: str | None) -> tuple[bool, str | None]:
    root = Path(host_workdir).resolve() if host_workdir else None
    for raw in shots:
        suffix = Path(raw).suffix.lower()
        if suffix not in _IMAGE_EXTS:
            return False, "screenshots 必须是工作区内真实图片（禁止 .txt）"
        if root is None:
            continue
        candidate = Path(raw)
        path = candidate if candidate.is_absolute() else (root / candidate)
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except ValueError:
            return False, "screenshots 必须位于任务工作区"
        if not resolved.is_file():
            return False, "screenshots 文件不存在"
    return True, None


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
    return True, None


def _validate_report_output(output: dict) -> tuple[bool, str | None]:
    """report 按权威 verdict 选择漏洞报告或验证记录。"""
    verdict = output.get("final_verdict")
    if verdict not in _VERDICTS:
        return False, (
            "final_verdict 必须是 confirmed|partial|code_reachable|code_smell|"
            "false_positive|not_reproduced"
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
    return True, None


async def run_ai_node(
    *,
    node_key: str,
    input_json: dict[str, Any],
    host_workdir: str,
    runner_env: dict[str, str],
    on_event: Callable[[dict], None] | None = None,
    timeout_seconds: int = 1800,
    task_id: str | None = None,
) -> dict[str, Any]:
    """起 agent-runner 容器跑一个 AI 节点,返回 output_json。

    失败抛 AgentRunnerError。
    SDK 未启用(claude_agent_sdk_enabled=False)时走 mock,返回模拟 output 供编排链路联调。
    """
    # Mock 模式:SDK 未启用时直接返回模拟 output(不起容器)
    from app.core.config import get_settings
    if not get_settings().claude_agent_sdk_enabled:
        logger.info(f"[Mock] AI 节点 {node_key} 返回模拟 output(SDK 未启用)")
        output = _mock_output(node_key, input_json)
        if on_event:
            on_event({"type": "phase.updated", "phase": node_key, "message": f"[Mock] {node_key} 完成"})
        return output

    # 1. 写 .node.json(容器内 run_one.py 读)
    #    先清旧的 .node_output.json:env_ready 排障循环每轮重调本函数,
    #    若上轮 agent 没调 submit_result,容器会读到上轮遗留的旧 output(run_one
    #    退出码 0 → 本函数读到旧数据,静默用错)。
    node_input_path = Path(host_workdir) / ".node.json"
    node_output_path = Path(host_workdir) / ".node_output.json"
    if node_output_path.exists():
        try:
            node_output_path.unlink()
        except OSError:
            pass
    node_input_path.write_text(
        json.dumps({"node_key": node_key, "input_json": input_json}, ensure_ascii=False),
        encoding="utf-8",
    )

    # 2. 构造 spec + 起容器(NODE_KEY env 让 run_one.py 选 agent)
    spec = AgentRunnerSpec(
        env={**runner_env, "NODE_KEY": node_key},
        host_workdir=host_workdir,
        timeout_seconds=timeout_seconds,
        extra_labels={"crucible.task_id": task_id, "task_id": task_id} if task_id else {},
    )

    last_fail = ""

    def _on_event(event: dict) -> None:
        nonlocal last_fail
        et = event.get("type")
        if et in ("agent.failed", "raw"):
            last_fail = (
                event.get("error")
                or event.get("content")
                or event.get("message")
                or ""
            )
        if on_event:
            on_event(event)

    exit_code, summary = await asyncio.to_thread(
        agent_runner_manager.run_with_streaming, spec, _on_event
    )

    if summary.get("timed_out"):
        raise AgentRunnerError(f"AI 节点 {node_key} 超时({timeout_seconds}s)")

    # 3. 读 .node_output.json(submit_result 写的)
    output_path = Path(host_workdir) / ".node_output.json"
    if not output_path.exists():
        stderr_tail = summary.get("stderr_tail", "") if summary else ""
        detail = (stderr_tail or last_fail or "").strip()
        raise AgentRunnerError(
            f"AI 节点 {node_key} 未产出 .node_output.json (exit={exit_code}): {detail[:300]}"
        )

    try:
        output = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AgentRunnerError(f"AI 节点 {node_key} output JSON 解析失败: {e}") from e

    # 4. schema 校验
    ok, err = validate_output(node_key, output, host_workdir=host_workdir)
    if not ok:
        raise AgentRunnerError(f"AI 节点 {node_key} output 校验失败: {err}")

    return output
