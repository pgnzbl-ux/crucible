"""Django urls.py：path() / re_path() 字面量。"""
from __future__ import annotations

import ast
import re
from pathlib import Path

from .models import EndpointRecord, make_endpoint, normalize_path, read_text, rel_posix, walk_files


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


def _django_path_to_template(raw: str) -> str:
    return re.sub(r"<(?:[^:>]+:)?([A-Za-z_][\w]*)>", r"{\1}", raw or "")


def parse_django_file(rel_path: str, source: str) -> list[EndpointRecord]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    out: list[EndpointRecord] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node.func) not in {"path", "re_path", "url"}:
            continue
        if not node.args:
            continue
        second = node.args[1] if len(node.args) > 1 else None
        if isinstance(second, ast.Call) and _call_name(second.func) == "include":
            continue
        raw = _const_str(node.args[0])
        if raw is None:
            continue
        path = normalize_path(_django_path_to_template(raw))
        symbol = None
        if len(node.args) > 1:
            symbol = _call_name(node.args[1])
        out.append(make_endpoint(
            method="GET",
            path=path,
            handler_file=rel_path,
            handler_symbol=symbol,
            line_start=getattr(node, "lineno", None),
            parser="django",
            acquisition="router",
        ))
    return out


def parse_django_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".py",)):
        if path.name != "urls.py":
            continue
        out.extend(parse_django_file(rel_posix(root, path), read_text(path)))
    return out
