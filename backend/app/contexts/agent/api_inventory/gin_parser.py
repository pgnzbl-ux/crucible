"""Gin / Echo / Chi 同形的 r.GET("/x") 或 r.Get("/x") 字面量。"""
from __future__ import annotations

import re
from pathlib import Path

from .models import EndpointRecord, make_endpoint, read_text, rel_posix, walk_files

_CALL = re.compile(
    r"""\.(?P<m>GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS|Get|Post|Put|Patch|Delete|Head|Options)\(\s*(?P<q>["'`])(?P<p>[^"'`]+)(?P=q)""",
)
_HINTS = (".GET(", ".POST(", ".PUT(", ".PATCH(", ".DELETE(", ".Get(", ".Post(")


def parse_gin_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".go",)):
        text = read_text(path)
        if not any(h in text for h in _HINTS):
            continue
        rel = rel_posix(root, path)
        for m in _CALL.finditer(text):
            out.append(make_endpoint(
                method=m.group("m").upper(),
                path=m.group("p"),
                handler_file=rel,
                line_start=text.count("\n", 0, m.start()) + 1,
                parser="gin",
                acquisition="router",
            ))
    return out
