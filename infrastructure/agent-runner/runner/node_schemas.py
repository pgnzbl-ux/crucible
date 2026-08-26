"""AI 节点 submit_result 工具的 input schema —— 全平台单一真相。

同时被两处消费，必须只此一份，禁止再各自维护副本：
- 容器内 `runner.run_one`：构造 submit_result MCP 工具（AI 实际看到的形状提示）。
- 后端 `app.contexts.agent.ai_runner`：仅测试 / 契约引用（运行时不构造工具）。

本模块零第三方依赖（纯 dict），后端 import 不会拖入 claude_agent_sdk，
容器 / 后端 / 单测都能安全导入。真正拒形状仍是后端 `validate_output`。

MCP 工具 schema 约束（2026-08-19 教训）：Anthropic 工具接口只保证
`type/properties/required` 子集；顶层 `allOf`/`if`/`then`/`const` 会让
第三方网关（如 360AI）静默丢弃整个工具定义，模型看不到 submit_result，
节点以 runner.no_submit 失败。条件形状（audit gate_verdict 分支等）只在
后端 `validate_output` 的 Python 逻辑 + SKILL.md 文案里表达，schema 层
最多用 `enum`/`anyOf`（嵌在 properties 内，已验证兼容）。
"""
from __future__ import annotations

NODE_INPUT_SCHEMAS: dict[str, dict] = {
    "canary": {
        "type": "object",
        "properties": {
            "marker": {
                "type": "string",
                "minLength": 1,
                "description": "通过 Read 工具读取到的兼容性标记原文",
            },
            "probe_completed": {
                "type": "boolean",
                "description": "是否通过 Bash 执行了平台探针",
            },
            "credential_visible": {
                "type": "boolean",
                "description": "探针是否发现模型主凭据；不得输出凭据值",
            },
            "summary": {"type": "string", "description": "兼容性测试简述"},
        },
        "required": ["marker", "probe_completed", "credential_visible", "summary"],
    },
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
        "description": "条件形状：pass 需 kill_chain/payloads/runtime_dependent/core_claim；fail 需 kill_chain/defense_layers（后端 validate_output 强校验，SKILL.md 有表）",
    },
    "reproduce": {
        "type": "object",
        "properties": {
            "reproduced": {"type": "boolean", "description": "是否真实复现"},
            "evidence": {
                "type": "array",
                "description": "证据列表",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "minLength": 1},
                        "detail": {"type": "string", "minLength": 1},
                    },
                    "required": ["type", "detail"],
                },
            },
            "attempts": {"type": "array", "description": "每次 HTTP 尝试的结构化记录"},
            "screenshots": {"type": "array", "description": "工作区内真实截图路径"},
            "verdict": {
                "type": "string",
                "enum": ["confirmed", "partial", "code_reachable", "code_smell", "false_positive", "not_reproduced"],
                "description": "6 档判定",
            },
            "cvss": {"type": "object", "description": "仅 confirmed/partial 的 CVSS"},
            "vulnerable_file": {"type": "string", "description": "漏洞文件定位"},
            "poc": {
                "type": "object",
                "description": "仅 confirmed/partial 的完整 PoC",
                "properties": {
                    "language": {"type": "string", "enum": ["python", "bash", "other"]},
                    "filename": {"type": "string"},
                    "code": {"type": "string"},
                    "usage": {"type": "string"},
                    "language_reason": {"type": "string"},
                },
            },
        },
        "required": ["verdict", "reproduced", "attempts"],
    },
    "report": {
        "type": "object",
        "properties": {
            "report_data": {"type": "object", "description": "document_kind + 8 节 Markdown"},
            "final_verdict": {
                "type": "string",
                "enum": ["confirmed", "partial", "code_reachable", "code_smell", "false_positive", "not_reproduced", "needs_review"],
                "description": "6 档判定；audit uncertain 无 reproduce 时为 needs_review（验证记录）",
            },
            "cvss": {"type": "object", "description": "仅漏洞报告的 CVSS"},
            "vulnerable_file": {"type": "string"},
        },
        "required": ["report_data", "final_verdict"],
    },
    "triage": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["tp", "fp", "need_more_context"],
                "description": "二审判决",
            },
            "confidence": {"type": "number", "description": "0–1"},
            "why": {
                "type": "array",
                "items": {"type": "string"},
                "description": "简短理由",
            },
            "evidence": {
                "type": "array",
                "description": "[{file, lines}]",
                "items": {
                    "type": "object",
                    "properties": {
                        "file": {"type": "string"},
                        "lines": {"type": "string"},
                    },
                },
            },
            "need": {
                "type": "array",
                "items": {"type": "string"},
                "description": "仍看不到而必须确认的符号（尽量用工具自补后仍缺再列）",
            },
            "attacker_controlled": {
                "type": "boolean",
                "description": "可疑真洞必须为 true：存在攻击者可控来源",
            },
            "reaches_sink": {
                "type": "boolean",
                "description": "可疑真洞必须为 true：能指到危险点",
            },
            "sanitizer": {
                "type": "string",
                "enum": ["none", "bypassable", "effective"],
                "description": "可疑真洞只允许 none 或 bypassable",
            },
        },
        "required": ["verdict", "confidence", "why"],
    },
    "api_hunt": {
        "type": "object",
        "properties": {
            "suspects": {
                "type": "array",
                "description": "鉴权/逻辑候选列表；允许安全判断未知，最终由 triage 判定",
                "items": {
                    "type": "object",
                    "properties": {
                        "cwe": {"type": "string", "description": "CWE-639 / CWE-863 等"},
                        "endpoint_id": {"type": "string"},
                        "file_path": {"type": "string", "description": "相对仓库根的 handler 文件"},
                        "function_symbol": {"type": "string"},
                        "line_start": {"type": "integer"},
                        "why": {"type": "array", "items": {"type": "string"}},
                        "summary": {
                            "type": "string",
                            "description": "1～3 句候选风险简述",
                        },
                        "reasoning": {
                            "type": "string",
                            "description": "当前证据与仍未知事实的推理",
                        },
                        "evidence": {
                            "type": "array",
                            "description": "证据条目：字符串或 {file, lines}",
                            "items": {},
                        },
                        "attacker_controlled": {"type": ["boolean", "null"]},
                        "reaches_sink": {"type": ["boolean", "null"]},
                        "sanitizer": {
                            "type": ["string", "null"],
                            "enum": ["none", "bypassable", "effective", "unknown", None],
                        },
                        "confidence": {
                            "description": "0–1 浮点、HIGH/MEDIUM/LOW；证据不足可为 null",
                        },
                        "evidence_kind": {"type": "string"},
                        "owasp_api": {"type": "string"},
                        "resource_key": {"type": "string"},
                        "method": {"type": "string"},
                        "path_template": {"type": "string"},
                    },
                    "required": [
                        "file_path",
                        "endpoint_id",
                        "why",
                        "summary",
                        "reasoning",
                        "evidence",
                        "attacker_controlled",
                        "reaches_sink",
                        "sanitizer",
                        "confidence",
                    ],
                },
            },
            "reviewed_count": {"type": "integer", "description": "本批审过的端点数"},
            "budget_exhausted": {"type": "boolean"},
        },
        "required": ["suspects", "reviewed_count"],
    },
}
