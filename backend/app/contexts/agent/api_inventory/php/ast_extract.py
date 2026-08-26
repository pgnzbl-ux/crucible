"""PHP 请求传参面：tree-sitter AST + 按框架开关的访问器。

只收集字符串字面量参数名；动态下标/变量名参数名忽略。
降噪交给 models.is_object_id（OBJECT_ID_NAMES / *_id）。

enabled_frameworks:
  - None → 全部框架（兼容旧调用）
  - empty set → 仅原生（超全局 / filter_input）
  - 非空 set → 原生 + 所列框架
"""
from __future__ import annotations

from app.contexts.agent.api_inventory.models import is_object_id

_SUPERGLOBALS = frozenset({"_GET", "_POST", "_REQUEST", "_COOKIE"})

_ALL_FRAMEWORKS = frozenset({
    "laravel", "symfony", "thinkphp", "codeigniter", "yii",
    "cakephp", "phalcon", "laminas", "slim", "wordpress", "fuel",
})

# 函数调用：框架 → 函数名集合（filter_input 为原生，不在此表）
_FUNCTION_BY_FW: dict[str, frozenset[str]] = {
    "laravel": frozenset({"request"}),
    "thinkphp": frozenset({"input"}),
    "wordpress": frozenset({"get_query_var"}),
}

# 实例方法名（不解析接收者类型）
_METHOD_BY_FW: dict[str, frozenset[str]] = {
    "laravel": frozenset({
        "input", "get", "query", "post", "parameter", "param", "cookie",
    }),
    "symfony": frozenset({"get", "parameter"}),
    "thinkphp": frozenset({"param", "get", "post"}),
    "codeigniter": frozenset({
        "get", "post", "getGet", "getPost", "getVar", "getGetPost",
        "get_post", "cookie",
    }),
    "yii": frozenset({"get", "post", "getParam", "getQuery"}),
    "cakephp": frozenset({"get", "getData", "getQuery", "fromGet", "fromPost"}),
    "phalcon": frozenset({"get", "getQuery", "getPost", "getParam"}),
    "laminas": frozenset({"get", "fromGet", "fromPost", "getQuery"}),
    "slim": frozenset({"get", "getParam", "getQuery"}),
    "fuel": frozenset({"get", "post", "param"}),
}

# 无具名字符串参的数组 API：不抽
_METHODS_NO_NAMED_ARG = frozenset({
    "getQueryParams",
    "getParsedBody",
})

# Class::method 静态调用
_SCOPED_BY_FW: dict[str, frozenset[tuple[str, str]]] = {
    "laravel": frozenset({("Input", "get"), ("Input", "post")}),
    "fuel": frozenset({("Input", "get"), ("Input", "post")}),
    "codeigniter": frozenset({
        ("Request", "param"), ("Request", "get"), ("Request", "post"),
    }),
}


def _resolve_enabled(enabled_frameworks: set[str] | None) -> set[str] | None:
    """None = 全开；空 set = 仅原生；否则返回规范后的框架集合。"""
    if enabled_frameworks is None:
        return None
    return {str(x).lower() for x in enabled_frameworks if x}


def _fw_active(enabled: set[str] | None, fw: str) -> bool:
    if enabled is None:
        return True
    return fw in enabled


def _active_functions(enabled: set[str] | None) -> frozenset[str]:
    names: set[str] = set()
    for fw, fns in _FUNCTION_BY_FW.items():
        if _fw_active(enabled, fw):
            names.update(fns)
    return frozenset(names)


def _active_methods(enabled: set[str] | None) -> frozenset[str]:
    names: set[str] = set()
    for fw, mns in _METHOD_BY_FW.items():
        if _fw_active(enabled, fw):
            names.update(mns)
    return frozenset(names)


def _active_scoped(enabled: set[str] | None) -> frozenset[tuple[str, str]]:
    pairs: set[tuple[str, str]] = set()
    for fw, sc in _SCOPED_BY_FW.items():
        if _fw_active(enabled, fw):
            pairs.update(sc)
    return frozenset(pairs)


def _node_text(raw: bytes, node) -> str:
    return raw[node.start_byte:node.end_byte].decode("utf-8", "replace")


def _string_literal_content(raw: bytes, node) -> str | None:
    """从 string / encapsed_string 节点取出字面量内容；非字面量返回 None。"""
    if node is None:
        return None
    if node.type == "string":
        for child in node.children:
            if child.type == "string_content":
                return _node_text(raw, child)
        return ""
    if node.type == "encapsed_string":
        if any(c.type not in {"\"", "string_content", "escape_sequence"} for c in node.children):
            return None
        parts = [
            _node_text(raw, child)
            for child in node.children
            if child.type == "string_content"
        ]
        return "".join(parts)
    if node.type == "argument":
        for child in node.children:
            if child.type in {"string", "encapsed_string"}:
                return _string_literal_content(raw, child)
        return None
    return None


def _normalize_param_name(raw_name: str) -> str | None:
    """ThinkPHP input('get.user_id') → user_id。"""
    name = (raw_name or "").strip()
    if not name:
        return None
    if "." in name:
        head, _, rest = name.partition(".")
        if head.lower() in {"get", "post", "param", "request", "put", "delete"} and rest:
            name = rest.rsplit(".", 1)[-1] if "." in rest else rest
    name = name.strip()
    return name or None


def _first_string_arg(raw: bytes, args_node) -> str | None:
    if args_node is None or args_node.type != "arguments":
        return None
    for child in args_node.children:
        if child.type != "argument":
            continue
        lit = _string_literal_content(raw, child)
        if lit is not None:
            return lit
    return None


def _argument_at(raw: bytes, args_node, index: int) -> str | None:
    if args_node is None or args_node.type != "arguments":
        return None
    seen = 0
    for child in args_node.children:
        if child.type != "argument":
            continue
        if seen == index:
            return _string_literal_content(raw, child)
        seen += 1
    return None


def _name_text(raw: bytes, node) -> str | None:
    if node is None:
        return None
    if node.type == "name":
        return _node_text(raw, node)
    named = node.child_by_field_name("name")
    if named is not None:
        return _name_text(raw, named)
    return None


def _arguments_node(node):
    args = node.child_by_field_name("arguments")
    if args is not None:
        return args
    for child in node.children:
        if child.type == "arguments":
            return child
    return None


def extract_php_id_params(
    source: str,
    *,
    enabled_frameworks: set[str] | None = None,
) -> list[str]:
    """从 PHP 源码抽取可能的 object-id 请求参数名（去重、保序）。"""
    if not source or not source.strip():
        return []
    try:
        import tree_sitter_php
        from tree_sitter import Language, Parser
    except ImportError:
        return []

    enabled = _resolve_enabled(enabled_frameworks)
    fnames = _active_functions(enabled)
    methods = _active_methods(enabled)
    scoped = _active_scoped(enabled)

    parser = Parser(Language(tree_sitter_php.language_php()))
    raw = source.encode("utf-8")
    tree = parser.parse(raw)
    found: list[str] = []
    seen: set[str] = set()

    def add_raw(raw_name: str | None) -> None:
        if raw_name is None:
            return
        name = _normalize_param_name(raw_name)
        if not name or not is_object_id(name):
            return
        key = name.lower()
        if key in seen:
            return
        seen.add(key)
        found.append(name)

    def walk(node) -> None:
        t = node.type

        if t == "subscript_expression":
            # 原生：$_GET['id'] 等（始终启用）
            target = node.child_by_field_name("object") or (
                node.children[0] if node.children else None
            )
            index = None
            for child in node.children:
                if child.type in {"string", "encapsed_string"}:
                    index = child
                    break
            if target is not None and target.type == "variable_name":
                var = None
                for child in target.children:
                    if child.type == "name":
                        var = _node_text(raw, child)
                        break
                if var in _SUPERGLOBALS:
                    add_raw(_string_literal_content(raw, index) if index else None)

        elif t == "function_call_expression":
            fname_node = node.child_by_field_name("function") or (
                node.children[0] if node.children else None
            )
            fname = _name_text(raw, fname_node) if fname_node else None
            args = _arguments_node(node)
            # 原生 filter_input
            if fname == "filter_input":
                add_raw(_argument_at(raw, args, 1))
            elif fname in fnames:
                add_raw(_first_string_arg(raw, args))

        elif t == "member_call_expression":
            method = None
            name_node = node.child_by_field_name("name")
            if name_node is not None:
                method = (
                    _node_text(raw, name_node)
                    if name_node.type == "name"
                    else _name_text(raw, name_node)
                )
            if method is None:
                for child in node.children:
                    if child.type == "name":
                        method = _node_text(raw, child)
            args = _arguments_node(node)
            if method in _METHODS_NO_NAMED_ARG:
                pass
            elif method in methods:
                add_raw(_first_string_arg(raw, args))

        elif t == "scoped_call_expression":
            names = [c for c in node.children if c.type == "name"]
            class_name = _node_text(raw, names[0]) if len(names) >= 2 else None
            method = _node_text(raw, names[1]) if len(names) >= 2 else None
            args = _arguments_node(node)
            if class_name and method and (class_name, method) in scoped:
                add_raw(_first_string_arg(raw, args))

        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return found


__all__ = ["extract_php_id_params", "_ALL_FRAMEWORKS"]
