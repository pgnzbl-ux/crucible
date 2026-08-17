"""报告渲染测试:8 节 Markdown 字符串 → 带标题的导出 md。"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


SAMPLE_REPORT_DATA = {
    "product_intro": "X 是一个基于 Flask 的博客系统。",
    "vulnerability": "CWE-89 SQL 注入。文件 `app/login.py:42`。入口 POST /login。",
    "impact": "- 受影响：all\n- 不受影响：—",
    "details": "### 4.1 代码审计\n\n`app/login.py` f-string 拼接 SQL。",
    "reproduction": "HTTP `0.0.0.0:5000`。\n\n![step1](img/step1_login.png)",
    "poc_commands": "```bash\ncurl -X POST http://localhost:5000/login\n```",
    "fix_suggestions": "P0: 参数化查询。",
    "reporting_decision": "建议报送。实际危害高。",
}


def test_render_all_8_sections():
    from app.contexts.report.renderer import render_report_md

    md = render_report_md(SAMPLE_REPORT_DATA)
    for i in range(1, 9):
        assert f"## {i}." in md
    assert "Flask" in md
    assert "CWE-89" in md
    assert "img/step1_login.png" in md


def test_render_does_not_look_up_nested_objects():
    from app.contexts.report.renderer import render_report_md

    md = render_report_md(SAMPLE_REPORT_DATA)
    assert "## 2. 漏洞描述" in md
    assert "CWE-89 SQL 注入" in md


def test_render_handles_missing_optional_fields():
    from app.contexts.report.renderer import render_report_md

    md = render_report_md({"product_intro": "x"})
    assert "## 1." in md and "## 8." in md
    assert "—" in md
    assert "# 漏洞验证报告" in md


def test_render_verification_record_uses_record_sections():
    from app.contexts.report.renderer import render_report_md

    md = render_report_md({
        "document_kind": "verification_record",
        "product_intro": "产品Z",
        "claimed_issue": "声称 SQL 注入",
        "whitebox_analysis": "入口到 sink 可达",
        "test_record": "curl 返回 CLI missing",
        "blocker": "宿主机没有 Claude Code 二进制",
        "observed_facts": "HTTP 未打到可观察危害",
        "remaining_conditions": "需要可用 CLI",
        "reporting_decision": "不报送",
        "poc_commands": "curl should not appear as PoC heading",
    })
    assert "# 漏洞验证记录" in md
    assert "## 2. 声称问题" in md
    assert "## 4. 测试记录" in md
    assert "CLI missing" in md
    assert "## 6. POC" not in md
    assert "poc_commands" not in md
