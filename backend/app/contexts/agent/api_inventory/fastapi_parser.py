"""确定性 API 清单 — FastAPI AST 解析。"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from .models import (
    HTTP_METHODS,
    EndpointRecord,
    make_endpoint,
    read_text,
    rel_posix,
    records_to_bom,
    walk_files,
)


def _const_str(node: ast.AST | None) -> str | None:
    if node is None:
        return None
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _decorator_http(dec: ast.AST) -> tuple[str, str] | None:
    """解析 @app.get('/x') / @router.post('/y') → (METHOD, path)。"""
    if not isinstance(dec, ast.Call):
        return None
    call = dec
    func = dec.func
    attr = _call_name(func)
    if not attr or attr.lower() not in HTTP_METHODS:
        if attr == "api_route":
            path = _const_str(call.args[0]) if call.args else None
            methods: list[str] = []
            for kw in call.keywords:
                if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                    for elt in kw.value.elts:
                        s = _const_str(elt)
                        if s:
                            methods.append(s.upper())
            if path and methods:
                return methods[0], path
        return None
    if not call.args:
        return None
    path = _const_str(call.args[0])
    if path is None:
        return None
    return attr.upper(), path


def _auth_hints_from_decorators(decs: list[ast.AST]) -> list[str]:
    hints: list[str] = []
    for dec in decs:
        text = ast.dump(dec)
        low = text.lower()
        if "depends" in low or "security" in low or "oauth2" in low or "httpbearer" in low:
            hints.append("dependency_auth")
        if "require" in low and ("role" in low or "admin" in low or "auth" in low):
            hints.append("role_guard")
    return sorted(set(hints))


def parse_fastapi_file(rel_path: str, source: str) -> list[EndpointRecord]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[EndpointRecord] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        extra = [
            arg.arg for arg in list(node.args.args) + list(node.args.kwonlyargs)
            if arg.arg not in ("self", "cls")
        ]
        auth = _auth_hints_from_decorators(node.decorator_list)
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                name = _call_name(child.func)
                if name and name.lower() == "depends":
                    auth.append("depends")
        for dec in node.decorator_list:
            parsed = _decorator_http(dec)
            if not parsed:
                continue
            method, path = parsed
            out.append(make_endpoint(
                method=method,
                path=path,
                handler_file=rel_path,
                handler_symbol=node.name,
                line_start=getattr(node, "lineno", None),
                extra_params=extra,
                auth_observed=auth,
                parser="fastapi",
                acquisition="router",
            ))
    return out


def parse_fastapi_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".py",)):
        rel = rel_posix(root, path)
        out.extend(parse_fastapi_file(rel, read_text(path)))
    return out


def build_fastapi_bom(repo_root: str | Path) -> dict[str, Any]:
    """单测/旧调用兼容：只跑 FastAPI parser。"""
    eps = parse_fastapi_repo(Path(repo_root))
    return records_to_bom(eps, parsers=["fastapi"], acquisition_kinds=["router"])
