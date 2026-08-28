"""backend 契约单一来源：node input schemas + 蒸馏 SKILL.md 的内容约束。

schema 已迁至 app.contexts.agent.contracts.node_input_schemas（backend 所有，
经 AgentSpec.submit_schema 下发 runner）；skill 目录仍由 worker 挂载下发。
"""

import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.contexts.agent.contracts.node_input_schemas import NODE_INPUT_SCHEMAS  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
NODE_SKILLS = ROOT / "backend" / "agent-runner" / "node-skills"

_NODE_KEYS = frozenset(NODE_INPUT_SCHEMAS)


def test_node_registry_and_skills_are_complete():
    assert _NODE_KEYS == frozenset(
        {
            "canary", "profile", "env_ready", "api_hunt",
            "audit", "reproduce", "report", "triage", "triage_batch",
        }
    )
    for key in _NODE_KEYS:
        path = NODE_SKILLS / key / "SKILL.md"
        assert path.is_file(), f"缺少 {path}"
        assert "submit_result" in path.read_text(encoding="utf-8")


def test_submit_schema_is_single_source_in_backend_contracts():
    """schema 单一真相在 backend contracts；runner 不再持有任何业务契约。"""
    agent_runner_dir = ROOT / "backend" / "agent-runner" / "runner"
    assert not (agent_runner_dir / "node_schemas.py").exists()
    # gateway 源码不 import 任何节点清单/契约
    gateway_src = (agent_runner_dir / "gateway.py").read_text(encoding="utf-8")
    assert "NODE_INPUT_SCHEMAS" not in gateway_src
    assert "NODE_AI_KEYS" not in gateway_src


_UNSUPPORTED_TOP_KEYS = ("allOf", "anyOf", "oneOf", "not", "if", "then", "else", "const", "$ref")


def test_submit_schema_uses_only_anthropic_tool_subset():
    """submit_result 工具 schema 只用 Anthropic 工具接口保证的子集。

    Anthropic tools API 只保证 type/properties/required/description/items/enum/
    minLength 等基础关键字；JSON Schema 组合器（allOf/anyOf/oneOf/if/then/const）
    不在保证集内，第三方网关（360AI 实测）解析顶层组合器失败时静默丢弃整个
    工具定义 → 模型看不到 submit_result → 节点以 runner.no_submit 失败
    （2026-08-19 audit 节点教训）。条件形状只在后端 validate_output 表达。
    嵌在 properties 内的 anyOf（env_ready.initial_creds）360AI 实测可用，豁免。
    """
    for node_key, schema in NODE_INPUT_SCHEMAS.items():
        for key in schema:
            assert key not in _UNSUPPORTED_TOP_KEYS, (
                f"{node_key} 工具 schema 顶层出现 {key!r}：第三方网关会静默丢弃 "
                "submit_result 工具定义，模型无法提交节点结果"
            )


def test_reproduce_submit_schema_rejects_empty_evidence_fields():
    evidence = NODE_INPUT_SCHEMAS["reproduce"]["properties"]["evidence"]
    item = evidence["items"]

    assert item["type"] == "object"
    assert item["required"] == ["type", "detail"]
    assert item["properties"]["type"]["minLength"] == 1
    assert item["properties"]["detail"]["minLength"] == 1


def test_env_ready_schema_creds_contract():
    schema = NODE_INPUT_SCHEMAS["env_ready"]
    desc = schema["properties"]["initial_creds"]["description"]
    assert "auth_required" in desc
    assert "无登录功能" in desc
    assert "initial_creds" in schema["required"]
    assert len(schema["properties"]["initial_creds"]["anyOf"]) == 3


def _skill_text(node_key: str) -> str:
    return (NODE_SKILLS / node_key / "SKILL.md").read_text(encoding="utf-8")


def test_skills_content_contracts():
    body = _skill_text("canary")
    assert "Read" in body and "Bash" in body
    assert "credential_visible" in body

    body = _skill_text("audit")
    assert "一次 HTTP 请求测" not in body
    assert "不发" in body or "禁止" in body

    body = _skill_text("profile")
    assert "docker compose up" not in body.lower()

    body = _skill_text("env_ready")
    assert "不要执行" in body or "禁止" in body
    assert "没有 Docker CLI" in body
    assert "不要寻找历史会话" in body
    assert "上一轮产物" in body
    assert "failed_stage" in body
    assert "auth_required" in body
    assert "不要交空对象" in body
    assert "先判断是否存在登录功能" in body
    assert "公开 dashboard" in body
    assert "仅修改 `.vuln-env`" in body
    assert "初始化靶场专用账号" in body
    assert "禁止修改项目业务源码" in body
    assert "credential_lookup_only=true" in body
    assert "build.context" in body
    assert "禁止把源码复制" in body
    assert "一容器一进程" in body
    assert "Could not transfer" in body
    assert "不要合并容器" in body
    assert "stdout" in body

    body = _skill_text("reproduce")
    assert "原样使用" in body
    assert "initial_creds" in body
    assert "IP:port" in body or "target_url" in body
    assert "attempts" in body
    assert "`type`" in body
    assert "`detail`" in body
    assert "report_data" in body
    assert "禁止" in body
    assert "host.docker.internal" in body  # 明确禁止该项
    assert "上限 5 次" not in body
    assert "payloads[0]" in body
    assert "判定即停" in body
    assert "poc" in body
    assert "python" in body.lower()
    assert "禁止空转" in body
    assert "不设验证次数上限" not in body

    body = _skill_text("report")
    assert "verification_record" in body
    assert "vulnerability_report" in body
    assert "expected_verdict" in body
    assert "覆盖" in body or "正本" in body
    assert "禁止改写" in body

    body = _skill_text("triage")
    assert "tp" in body and "fp" in body
    assert "need_more_context" in body
    assert "禁止" in body

    body = _skill_text("api_hunt")
    assert "submit_result" in body
    assert "HTTP" in body or "http" in body

    body = _skill_text("profile")
    assert "画像" in body
