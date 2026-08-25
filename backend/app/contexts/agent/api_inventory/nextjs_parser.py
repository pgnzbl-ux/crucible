"""Next.js pages/api 文件入口 + app/api/**/route.ts 导出方法。"""
from __future__ import annotations

import re
from pathlib import Path

from .models import EndpointRecord, make_endpoint, read_text, rel_posix, walk_files

_EXPORT_FN = re.compile(
    r"export\s+(?:async\s+)?function\s+(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*\(",
)
_EXPORT_CONST = re.compile(
    r"export\s+const\s+(GET|POST|PUT|PATCH|DELETE|OPTIONS|HEAD)\s*=",
)


def _dynamic_seg(name: str) -> str:
    if name.startswith("[") and name.endswith("]"):
        inner = name[1:-1]
        if inner.startswith("..."):
            inner = inner[3:]
        return "{" + inner.strip() + "}"
    return name


def _pages_api_path(rel: str) -> str | None:
    low = rel.replace("\\", "/")
    marker = "pages/api/"
    idx = low.find(marker)
    if idx < 0:
        return None
    rest = low[idx + len(marker):]
    rest = re.sub(r"\.(tsx?|jsx?|mjs|cjs)$", "", rest)
    if rest.endswith("/index"):
        rest = rest[: -len("/index")]
    parts = [_dynamic_seg(p) for p in rest.split("/") if p]
    return "/api" + (("/" + "/".join(parts)) if parts else "")


def _app_route_path(rel: str) -> str | None:
    low = rel.replace("\\", "/")
    if not low.endswith("/route.ts") and not low.endswith("/route.js"):
        return None
    marker = "app/"
    idx = low.find(marker)
    if idx < 0:
        return None
    rest = low[idx + len(marker):]
    rest = re.sub(r"/route\.(tsx?|js)$", "", rest)
    parts = [_dynamic_seg(p) for p in rest.split("/") if p]
    return "/" + "/".join(parts) if parts else "/"


def parse_nextjs_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".js", ".ts", ".jsx", ".tsx")):
        rel = rel_posix(root, path)
        pages = _pages_api_path(rel)
        if pages:
            out.append(make_endpoint(
                method="GET",
                path=pages,
                handler_file=rel,
                parser="nextjs",
                acquisition="script_file",
            ))
            out.append(make_endpoint(
                method="POST",
                path=pages,
                handler_file=rel,
                parser="nextjs",
                acquisition="script_file",
            ))
            continue
        app_path = _app_route_path(rel)
        if not app_path:
            continue
        text = read_text(path)
        methods = {m.group(1).upper() for m in _EXPORT_FN.finditer(text)}
        methods.update(m.group(1).upper() for m in _EXPORT_CONST.finditer(text))
        if not methods:
            continue
        for method in sorted(methods):
            out.append(make_endpoint(
                method=method,
                path=app_path,
                handler_file=rel,
                handler_symbol=method,
                parser="nextjs",
                acquisition="export_handler",
            ))
    return out
