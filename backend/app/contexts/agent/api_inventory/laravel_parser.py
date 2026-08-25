"""Laravel Route::get/post/... 字面量。"""
from __future__ import annotations

import re
from pathlib import Path

from .models import EndpointRecord, make_endpoint, read_text, rel_posix, walk_files

_ROUTE = re.compile(
    r"""Route::(?P<m>get|post|put|patch|delete|any|match)\(\s*(?P<q>['"])(?P<p>[^'"]+)(?P=q)""",
    re.IGNORECASE,
)


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def parse_laravel_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".php",)):
        text = read_text(path)
        if "Route::" not in text:
            continue
        rel = rel_posix(root, path)
        for m in _ROUTE.finditer(text):
            method = m.group("m").upper()
            if method in {"ANY", "MATCH"}:
                methods = ("GET", "POST")
            else:
                methods = (method,)
            for http in methods:
                out.append(make_endpoint(
                    method=http,
                    path=m.group("p"),
                    handler_file=rel,
                    line_start=_line_of(text, m.start()),
                    parser="laravel",
                    acquisition="router",
                ))
    return out
