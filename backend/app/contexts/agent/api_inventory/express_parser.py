"""Express / Koa / Fastify 同形的 app.get('/x') 字面量。"""
from __future__ import annotations

import re
from pathlib import Path

from .models import HTTP_METHODS, EndpointRecord, make_endpoint, read_text, rel_posix, walk_files

_CALL = re.compile(
    r"""(?:^|[^A-Za-z0-9_])(?:app|router|r|server|this|fastify)\.(?P<m>get|post|put|patch|delete|options|head)\(\s*(?P<q>['"`])(?P<p>[^'"`]+?)(?P=q)""",
    re.IGNORECASE | re.MULTILINE,
)
_ROUTE_CHAIN = re.compile(
    r"""(?:app|router|r)\.route\(\s*(?P<q>['"`])(?P<p>[^'"`]+?)(?P=q)\s*\)\s*\.(?P<m>get|post|put|patch|delete|options|head)\(""",
    re.IGNORECASE,
)


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def parse_express_file(rel_path: str, source: str) -> list[EndpointRecord]:
    out: list[EndpointRecord] = []
    for rx in (_CALL, _ROUTE_CHAIN):
        for m in rx.finditer(source):
            method = m.group("m").upper()
            if method.lower() not in HTTP_METHODS:
                continue
            out.append(make_endpoint(
                method=method,
                path=m.group("p"),
                handler_file=rel_path,
                line_start=_line_of(source, m.start()),
                parser="express",
                acquisition="router",
            ))
    return out


def parse_express_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".js", ".ts", ".mjs", ".cjs")):
        out.extend(parse_express_file(rel_posix(root, path), read_text(path)))
    return out
