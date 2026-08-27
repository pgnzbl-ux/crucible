"""NestJS @Controller + @Get/@Post 字面量。"""
from __future__ import annotations

import re
from pathlib import Path

from .models import EndpointRecord, make_endpoint, normalize_path, read_text, rel_posix, walk_files

_CTRL = re.compile(
    r"@Controller\(\s*(?:(?P<q>['\"])(?P<p>[^'\"]*)(?P=q))?\s*\)",
)
_METH = re.compile(
    r"@(Get|Post|Put|Patch|Delete|Options|Head|All)\(\s*(?:(?P<q>['\"])(?P<p>[^'\"]*)(?P=q))?\s*\)",
)


def _join(prefix: str, path: str) -> str:
    return normalize_path((prefix.rstrip("/") + "/" + path.lstrip("/")) if prefix else (path or "/"))


def parse_nestjs_file(rel_path: str, source: str) -> list[EndpointRecord]:
    if "@Controller" not in source:
        return []
    ctrl = _CTRL.search(source)
    prefix = (ctrl.group("p") if ctrl and ctrl.group("p") else "") or ""
    if prefix and not prefix.startswith("/"):
        prefix = "/" + prefix
    out: list[EndpointRecord] = []
    for m in _METH.finditer(source):
        raw_method = m.group(1)
        path = m.group("p") or ""
        methods = ("GET", "POST") if raw_method == "All" else (raw_method.upper(),)
        combined = _join(prefix, path)
        line = source.count("\n", 0, m.start()) + 1
        for method in methods:
            out.append(make_endpoint(
                method=method,
                path=combined,
                handler_file=rel_path,
                handler_symbol=raw_method,
                line_start=line,
                parser="nestjs",
                acquisition="router",
            ))
    return out


def parse_nestjs_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".js", ".ts")):
        text = read_text(path)
        if "@Controller" not in text:
            continue
        out.extend(parse_nestjs_file(rel_posix(root, path), text))
    return out
