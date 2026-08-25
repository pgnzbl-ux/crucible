from app.contexts.finding.classification import infer_cwe, vulnerability_title


def test_known_cwe_has_human_title():
    assert vulnerability_title(cwe="CWE-89", rule_id="custom.rule", message="", engine="semgrep") == "SQL 注入"


def test_missing_cwe_uses_engine_and_rule_semantics():
    assert vulnerability_title(cwe=None, rule_id="generic-api-key", message="secret found", engine="gitleaks") == "敏感信息泄露"
    assert vulnerability_title(cwe=None, rule_id="GHSA-abcd", message="affected package", engine="osv") == "存在漏洞依赖"
    assert vulnerability_title(
        cwe=None, rule_id="GHSA-abcd", message="affected package", engine="osv",
        dependency_name="jinja2",
    ) == "jinja2 依赖漏洞"
    assert vulnerability_title(
        cwe="CWE-79", rule_id="GHSA-abcd", message="", engine="osv",
        dependency_name="jinja2",
    ) == "jinja2 · 跨站脚本（XSS）"
    assert vulnerability_title(cwe=None, rule_id="python.lang.security.audit.subprocess-shell-true", message="", engine="semgrep") == "命令注入"


def test_unmapped_cwe_still_uses_rule_semantics():
    assert vulnerability_title(cwe="CWE-999", rule_id="django.security.open-redirect", message="", engine="semgrep") == "开放重定向"


def test_unknown_rule_is_humanized_instead_of_generic_placeholder():
    assert vulnerability_title(cwe=None, rule_id="custom.jwt-validation-missing", message="", engine="semgrep") == "Jwt Validation Missing"


def test_missing_cwe_is_inferred_from_semantic_rule_without_overwriting_source_cwe():
    assert infer_cwe(cwe=None, rule_id="python.lang.security.audit.subprocess-shell-true", message="", engine="semgrep") == "CWE-78"
    assert infer_cwe(cwe=None, rule_id="django.security.open-redirect", message="", engine="semgrep") == "CWE-601"
    assert infer_cwe(cwe="CWE-999", rule_id="python.sqli", message="", engine="semgrep") == "CWE-999"
