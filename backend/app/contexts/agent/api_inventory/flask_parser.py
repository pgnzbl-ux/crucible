"""Flask @app.route / @bp.route 字面量路径。"""
from __future__ import annotations

import ast
from pathlib import Path

from .models import HTTP_METHODS, EndpointRecord, make_endpoint, read_text, rel_posix, walk_files


def _const_str(node: ast.AST | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _route_spec(dec: ast.AST) -> list[tuple[str, str]]:
    if not isinstance(dec, ast.Call) or not dec.args:
        return []
    name = _call_name(dec.func)
    path = _const_str(dec.args[0])
    if path is None:
        return []
    if name == "route":
        methods = ["GET"]
        for kw in dec.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                found = [_const_str(elt) for elt in kw.value.elts]
                methods = [m.upper() for m in found if m]
        return [(m, path) for m in methods] or [("GET", path)]
    if name and name.lower() in HTTP_METHODS:
        return [(name.upper(), path)]
    return []


def parse_flask_file(rel_path: str, source: str) -> list[EndpointRecord]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[EndpointRecord] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            for method, path in _route_spec(dec):
                out.append(make_endpoint(
                    method=method,
                    path=path,
                    handler_file=rel_path,
                    handler_symbol=node.name,
                    line_start=getattr(node, "lineno", None),
                    parser="flask",
                    acquisition="router",
                ))
    return out


def parse_flask_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".py",)):
        out.extend(parse_flask_file(rel_posix(root, path), read_text(path)))
    return out
