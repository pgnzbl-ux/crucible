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

# 各 AI 节点的 output schema(校验最小必需字段,spec §1.3)
NODE_OUTPUT_SCHEMAS: dict[str, dict] = {
    "env_ready": {
        "required": ["target_url", "compose_path"],
        "optional": ["transport_shape", "initial_creds", "started_containers"],
    },
    "audit": {
        "required": ["gate_verdict"],
        "optional": ["kill_chain", "defense_layers", "payloads", "gate_reason"],
    },
    "reproduce": {
        "required": ["verdict"],
        "optional": ["reproduced", "evidence", "screenshots"],
    },
    "report": {
        "required": ["report_data", "final_verdict"],
        "optional": ["cvss"],
    },
}

# 各 AI 节点的 submit_result 工具 input schema(传给容器内 run_one.py 构造工具)
NODE_INPUT_SCHEMAS: dict[str, dict] = {
    "env_ready": {
        "type": "object",
        "properties": {
            "target_url": {"type": "string", "description": "靶场访问地址"},
            "compose_path": {"type": "string", "description": ".vuln-env/docker-compose.yml 路径"},
            "transport_shape": {"type": "object", "description": "协议/端口/TLS 等"},
            "initial_creds": {"type": "object", "description": "初始账号密码"},
            "started_containers": {"type": "array", "description": "启动的容器名列表"},
        },
        "required": ["target_url", "compose_path"],
    },
    "audit": {
        "type": "object",
        "properties": {
            "kill_chain": {"type": "string", "description": "entry→sink 完整调用链"},
            "defense_layers": {"type": "array", "description": "每层防御 + 是否 bypass"},
            "payloads": {"type": "array", "description": "构造的 payload 候选"},
            "gate_verdict": {"type": "string", "enum": ["pass", "fail"], "description": "Phase 2.5 三问结论"},
            "gate_reason": {"type": "string", "description": "pass/fail 的理由"},
        },
        "required": ["gate_verdict"],
    },
    "reproduce": {
        "type": "object",
        "properties": {
            "reproduced": {"type": "boolean", "description": "是否真实复现"},
            "evidence": {"type": "array", "description": "证据列表"},
            "screenshots": {"type": "array", "description": "截图文件名列表"},
            "verdict": {
                "type": "string",
                "enum": ["confirmed", "partial", "code_reachable", "code_smell", "false_positive", "not_reproduced"],
                "description": "6 档判定",
            },
        },
        "required": ["verdict"],
    },
    "report": {
        "type": "object",
        "properties": {
            "report_data": {"type": "object", "description": "8 节报告结构化 JSON"},
            "final_verdict": {
                "type": "string",
                "enum": ["confirmed", "partial", "code_reachable", "code_smell", "false_positive", "not_reproduced"],
            },
            "cvss": {"type": "object", "description": "CVSS 向量/分数/等级"},
        },
        "required": ["report_data", "final_verdict"],
    },
}


def _mock_output(node_key: str, input_json: dict[str, Any]) -> dict[str, Any]:
    """Mock 模式:SDK 未启用时返回模拟 output(通过 schema 校验),供编排链路联调。"""
    if node_key == "env_ready":
        return {
            "target_url": "http://localhost:8080",
            "compose_path": ".vuln-env/docker-compose.yml",
            "transport_shape": {"protocol": "http", "listener": "0.0.0.0:8080", "tls_termination": "无"},
            "initial_creds": {},
            "started_containers": ["mock-app"],
        }
    if node_key == "audit":
        return {
            "kill_chain": "[Mock] entry → sink(模拟调用链)",
            "defense_layers": [{"name": "validator", "bypass": "模拟绕过"}],
            "payloads": ["mock-payload"],
            "gate_verdict": "pass",
            "gate_reason": "[Mock] 三问通过",
        }
    if node_key == "reproduce":
        return {
            "reproduced": True,
            "evidence": [{"type": "http_response", "detail": "[Mock] 200 OK, payload reflected"}],
            "screenshots": [],
            "verdict": "confirmed",
        }
    if node_key == "report":
        return {
            "report_data": {
                "product_intro": "[Mock] 产品介绍",
                "vulnerability": {
                    "type": "CWE-89: SQL 注入(Mock)",
                    "cvss": {"vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H", "base_score": 9.8, "severity": "Critical"},
                    "vulnerable_file": "app/mock.py",
                    "vulnerable_lines": "1-10",
                    "preconditions": "无",
                    "entry_point": "POST /login",
                    "core_harm": "[Mock] 数据泄露",
                    "environment_constraint": "默认配置",
                    "trigger_default": "是",
                },
                "impact": {"affected_versions": "all", "unaffected_versions": "—", "trigger_condition_defaults": "默认即满足"},
                "details": {"audit_analysis": [], "poc_construction": {"exploitation_chain": "[Mock]"}},
                "reproduction": {"transport_shape": {"protocol": "HTTP"}, "steps": [], "attack_chain_diagram": "[Mock]"},
                "poc_commands": ["curl -X POST http://localhost:8080/login"],
                "fix_suggestions": [{"priority": "P0", "suggestion": "[Mock] 参数化查询"}],
                "reporting_decision": {"recommendation": "📤 建议报送", "actual_harm": "高", "fix_priority": "P0", "reason": "[Mock]", "risk_description": "[Mock]"},
            },
            "final_verdict": "confirmed",
            "cvss": {"vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H", "base_score": 9.8, "severity": "Critical"},
        }
    return {}


def validate_output(node_key: str, output: dict) -> tuple[bool, str | None]:
    """校验 AI 节点 output 是否满足最小 schema。"""
    schema = NODE_OUTPUT_SCHEMAS.get(node_key)
    if not schema:
        return True, None
    for field_name in schema["required"]:
        if field_name not in output:
            return False, f"缺必需字段: {field_name}"
    return True, None


async def run_ai_node(
    *,
    node_key: str,
    input_json: dict[str, Any],
    host_workdir: str,
    runner_env: dict[str, str],
    on_event: Callable[[dict], None] | None = None,
    timeout_seconds: int = 1800,
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
    node_input_path = Path(host_workdir) / ".node.json"
    node_input_path.write_text(
        json.dumps({"node_key": node_key, "input_json": input_json}, ensure_ascii=False),
        encoding="utf-8",
    )

    # 2. 构造 spec + 起容器(NODE_KEY env 让 run_one.py 选 agent)
    spec = AgentRunnerSpec(
        env={**runner_env, "NODE_KEY": node_key},
        host_workdir=host_workdir,
        timeout_seconds=timeout_seconds,
    )

    def _on_event(event: dict) -> None:
        if on_event:
            on_event(event)

    exit_code, summary = await asyncio.to_thread(
        agent_runner_manager.run_with_streaming, spec, _on_event
    )

    # 3. 读 .node_output.json(submit_result 写的)
    output_path = Path(host_workdir) / ".node_output.json"
    if not output_path.exists():
        stderr_tail = summary.get("stderr_tail", "") if summary else ""
        raise AgentRunnerError(
            f"AI 节点 {node_key} 未产出 .node_output.json (exit={exit_code}): {stderr_tail[:300]}"
        )

    try:
        output = json.loads(output_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise AgentRunnerError(f"AI 节点 {node_key} output JSON 解析失败: {e}") from e

    # 4. schema 校验
    ok, err = validate_output(node_key, output)
    if not ok:
        raise AgentRunnerError(f"AI 节点 {node_key} output 校验失败: {err}")

    return output
