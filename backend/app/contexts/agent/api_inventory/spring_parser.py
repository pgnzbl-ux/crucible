"""Spring @GetMapping / @RequestMapping 字面量（含类前缀）。"""
from __future__ import annotations

import re
from pathlib import Path

from .models import EndpointRecord, make_endpoint, normalize_path, read_text, rel_posix, walk_files

_REQ_OPEN = re.compile(r"@RequestMapping\s*\(([^)]*)\)")
_METHOD_MAP = re.compile(
    r"@(?P<ann>GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)\s*\(\s*(?:(?:value|path)\s*=\s*)?(?:(?P<q>['\"])(?P<p>[^'\"]+)(?P=q))?",
)
_REQ_METHOD = re.compile(r"RequestMethod\.(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)")
_PATH_IN_BODY = re.compile(
    r"(?:(?:value|path)\s*=\s*)?['\"]([^'\"]+)['\"]",
)
_ANN_METHOD = {
    "GetMapping": "GET",
    "PostMapping": "POST",
    "PutMapping": "PUT",
    "DeleteMapping": "DELETE",
    "PatchMapping": "PATCH",
}


def _join(prefix: str, path: str) -> str:
    return normalize_path((prefix.rstrip("/") + "/" + path.lstrip("/")) if prefix else path)


def _class_prefix(source: str) -> tuple[str, int | None]:
    for m in _REQ_OPEN.finditer(source):
        after = source[m.end(): m.end() + 220]
        if re.search(r"\b(class|interface)\b", after):
            hit = _PATH_IN_BODY.search(m.group(1) or "")
            return (hit.group(1) if hit else ""), m.start()
    return "", None


def parse_spring_file(rel_path: str, source: str) -> list[EndpointRecord]:
    prefix, class_start = _class_prefix(source)
    out: list[EndpointRecord] = []
    for m in _METHOD_MAP.finditer(source):
        if class_start is not None and m.start() == class_start:
            continue
        path = m.group("p") or ""
        ann = m.group("ann")
        window = source[m.start(): m.start() + 240]
        if ann == "RequestMapping":
            found = _REQ_METHOD.findall(window)
            methods = [x.upper() for x in found] or ["GET"]
        else:
            methods = [_ANN_METHOD[ann]]
        combined = _join(prefix, path) if path or prefix else "/"
        line = source.count("\n", 0, m.start()) + 1
        for method in methods:
            out.append(make_endpoint(
                method=method,
                path=combined,
                handler_file=rel_path,
                line_start=line,
                parser="spring",
                acquisition="router",
            ))
    return out


def parse_spring_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".java",)):
        text = read_text(path)
        if "Mapping" not in text and "RequestMapping" not in text:
            continue
        out.extend(parse_spring_file(rel_posix(root, path), text))
    return out
