"""报告渲染测试:report_data JSON → markdown 8 节。"""
import sys
import os
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


SAMPLE_REPORT_DATA = {
    "product_intro": "X 是一个基于 Flask 的博客系统。",
    "vulnerability": {
        "type": "CWE-89: SQL 注入",
        "cvss": {
            "vector": "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H",
            "base_score": 9.8,
            "severity": "Critical",
        },
        "vulnerable_file": "app/login.py",
        "vulnerable_lines": "42-45",
        "preconditions": "无",
        "entry_point": "POST /login",
        "core_harm": "攻击者可绕过登录并 dump 全库",
        "environment_constraint": "默认配置即受影响",
        "trigger_default": "是",
    },
    "impact": {
        "affected_versions": "all",
        "unaffected_versions": "—",
        "trigger_condition_defaults": "默认即满足",
    },
    "details": {
        "audit_analysis": [
            {"file": "app/login.py", "lines": "42-45", "content": "cursor.execute(f\"SELECT * FROM users WHERE name='{name}'\")", "flaw_explanation": "f-string 拼接 SQL"},
        ],
        "poc_construction": {
            "endpoint_choice_reason": "/login 是入口",
            "bypass_methods": ["无 WAF"],
            "payload_design": "' OR 1=1--",
            "exploitation_chain": "输入 → SQL → dump",
        },
    },
    "reproduction": {
        "transport_shape": {"protocol": "HTTP", "listener": "0.0.0.0:5000", "tls_termination": "无", "x_forwarded_proto": "—", "channel_check": "无"},
        "target_product": "X 1.0",
        "frontend_url": "http://localhost:5000/",
        "steps": [{"step": 1, "action": "发送 payload", "observation": "返回 200 且登录成功", "screenshot": "img/step1_login.png"}],
        "result_verification": [{"item": "登录态", "result": "获得 admin session"}],
        "attack_chain_diagram": "攻击者 → /login → SQLi → dump",
    },
    "poc_commands": ["curl -X POST http://localhost:5000/login -d \"username=admin' OR 1=1--\""],
    "fix_suggestions": [{"priority": "P0", "suggestion": "用参数化查询", "code_example": "cursor.execute(\"SELECT * FROM users WHERE name=%s\", (name,))"}],
    "reporting_decision": {"recommendation": "📤 建议报送", "actual_harm": "高", "fix_priority": "P0", "reason": "默认配置可利用", "risk_description": "可导致数据泄露"},
}


def test_render_all_8_sections():
    """渲染的 markdown 必须含 ## 1 到 ## 8 全部 8 节。"""
    from app.contexts.report.renderer import render_report_md

    md = render_report_md(SAMPLE_REPORT_DATA)
    for i in range(1, 9):
        assert f"## {i}." in md or f"## {i} " in md, f"缺第 {i} 节"
    # 关键内容应出现
    assert "CWE-89" in md
    assert "app/login.py" in md
    assert "9.8" in md
    assert "POST /login" in md


def test_render_contains_transport_shape():
    """§5.1 必须含 transport-shape 关键字(md_to_docx preflight 要求)。"""
    from app.contexts.report.renderer import render_report_md

    md = render_report_md(SAMPLE_REPORT_DATA)
    # 5.1 环境准备 区块要有协议/端口等
    assert "HTTP" in md
    assert "0.0.0.0:5000" in md


def test_render_steps_inline_screenshots():
    """§5.2 复现步骤的截图必须 inline 嵌入 ![](img/...)。"""
    from app.contexts.report.renderer import render_report_md

    md = render_report_md(SAMPLE_REPORT_DATA)
    assert "![step1_login](img/step1_login.png)" in md or "img/step1_login.png" in md


def test_render_handles_missing_optional_fields():
    """可选字段缺失时渲染不崩(给占位)。"""
    from app.contexts.report.renderer import render_report_md

    minimal = {"product_intro": "x", "vulnerability": {"type": "CWE-79"}}
    md = render_report_md(minimal)
    assert "## 1." in md
    assert "## 8." in md


def test_render_cvss_vector_and_score():
    """CVSS 向量和分数应渲染。"""
    from app.contexts.report.renderer import render_report_md

    md = render_report_md(SAMPLE_REPORT_DATA)
    assert "AV:N/AC:L/PR:N/UI:N/C:H/I:H/A:H" in md
    assert "9.8" in md
    assert "Critical" in md
