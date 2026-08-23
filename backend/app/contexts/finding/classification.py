"""漏洞线索的用户可读分类；不改变扫描器原始 CWE 与规则身份。"""
from __future__ import annotations

import re


CWE_TITLES: dict[str, str] = {
    "CWE-20": "输入校验不足",
    "CWE-22": "路径穿越",
    "CWE-78": "命令注入",
    "CWE-79": "跨站脚本（XSS）",
    "CWE-89": "SQL 注入",
    "CWE-90": "LDAP 注入",
    "CWE-91": "XML 注入",
    "CWE-94": "代码注入",
    "CWE-98": "文件包含",
    "CWE-113": "HTTP 响应拆分",
    "CWE-119": "内存越界访问",
    "CWE-125": "越界读取",
    "CWE-190": "整数溢出",
    "CWE-200": "敏感信息暴露",
    "CWE-209": "错误信息泄露",
    "CWE-259": "硬编码密码",
    "CWE-276": "默认权限过宽",
    "CWE-287": "身份认证缺陷",
    "CWE-295": "证书校验不当",
    "CWE-306": "关键功能缺少认证",
    "CWE-311": "敏感数据未加密",
    "CWE-319": "明文传输敏感信息",
    "CWE-326": "加密强度不足",
    "CWE-327": "弱加密算法",
    "CWE-330": "随机数不可预测",
    "CWE-345": "数据完整性校验不足",
    "CWE-352": "跨站请求伪造（CSRF）",
    "CWE-400": "资源消耗过度",
    "CWE-416": "释放后使用",
    "CWE-434": "危险文件上传",
    "CWE-502": "不安全反序列化",
    "CWE-601": "开放重定向",
    "CWE-611": "XML 外部实体（XXE）",
    "CWE-614": "Cookie 缺少安全属性",
    "CWE-732": "权限配置不当",
    "CWE-770": "资源分配缺少限制",
    "CWE-776": "XML 实体扩展攻击",
    "CWE-787": "越界写入",
    "CWE-798": "硬编码凭据",
    "CWE-918": "服务端请求伪造（SSRF）",
}


_SEMANTIC_CLASSIFICATIONS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("sql-injection", "sql_injection", "sqli"), "SQL 注入", "CWE-89"),
    (("command-injection", "command_injection", "shell-true", "subprocess-shell"), "命令注入", "CWE-78"),
    (("path-traversal", "path_traversal", "directory-traversal"), "路径穿越", "CWE-22"),
    (("cross-site-scripting", "cross_site_scripting", "xss"), "跨站脚本（XSS）", "CWE-79"),
    (("server-side-request-forgery", "server_side_request_forgery", "ssrf"), "服务端请求伪造（SSRF）", "CWE-918"),
    (("open-redirect", "open_redirect"), "开放重定向", "CWE-601"),
    (("deserialization", "pickle", "unserialize"), "不安全反序列化", "CWE-502"),
    (("xxe", "external-entity", "external_entity"), "XML 外部实体（XXE）", "CWE-611"),
    (("hardcoded-password", "hardcoded-secret", "hardcoded-credential"), "硬编码凭据", "CWE-798"),
    (("weak-hash", "insecure-hash", " md5", " sha1"), "弱加密算法", "CWE-327"),
    (("insecure-random", "weak-random", "predictable-random"), "随机数不可预测", "CWE-330"),
    (("missing-auth", "auth-bypass", "authentication-bypass"), "身份认证缺陷", "CWE-287"),
    (("unsafe-file-upload", "unrestricted-upload"), "危险文件上传", "CWE-434"),
    (("prototype-pollution", "prototype_pollution"), "原型污染", "CWE-1321"),
    (("regex-dos", "redos"), "正则表达式拒绝服务", "CWE-1333"),
    (("debug-enabled", "debug-mode"), "生产环境调试模式开启", "CWE-489"),
)


def _humanize_rule(rule_id: str) -> str:
    tail = (rule_id or "").strip().split(".")[-1]
    words = re.sub(r"[_-]+", " ", tail).strip()
    return words.title() if words and words.lower() not in {"unknown", "rule"} else "未命名安全风险"


def _semantic_match(rule_id: str, message: str) -> tuple[str, str] | None:
    haystack = f" {rule_id} {message} ".lower()
    for needles, title, inferred_cwe in _SEMANTIC_CLASSIFICATIONS:
        if any(needle in haystack for needle in needles):
            return title, inferred_cwe
    return None


def infer_cwe(*, cwe: str | None, rule_id: str, message: str, engine: str) -> str | None:
    """补齐扫描器缺失的 CWE；已有原始值永远优先，不在这里覆盖。"""
    normalized_cwe = (cwe or "").strip().upper()
    if normalized_cwe:
        return normalized_cwe
    semantic = _semantic_match(rule_id, message)
    if semantic:
        return semantic[1]
    if engine == "gitleaks":
        return "CWE-798"
    return None


def vulnerability_title(*, cwe: str | None, rule_id: str, message: str, engine: str) -> str:
    normalized_cwe = (cwe or "").upper()
    if normalized_cwe in CWE_TITLES:
        return CWE_TITLES[normalized_cwe]

    semantic = _semantic_match(rule_id, message)
    if semantic:
        return semantic[0]

    if engine == "gitleaks":
        return "敏感信息泄露"
    if engine == "osv":
        return "存在漏洞依赖"
    return _humanize_rule(rule_id)
