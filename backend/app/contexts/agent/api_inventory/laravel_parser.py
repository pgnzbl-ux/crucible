"""Laravel Route::get/post/... → 端点 + 控制器解析 + Laravel 传参面 enrich。"""
from __future__ import annotations

import re
from pathlib import Path

from .models import (
    SKIP_DIR_NAMES,
    EndpointRecord,
    make_endpoint,
    read_text,
    rel_posix,
    walk_files,
)
from .php import extract_php_id_params

_ROUTE_HEAD = re.compile(
    r"""Route::(?P<m>get|post|put|patch|delete|any|match)\(\s*(?P<q>['"])(?P<p>[^'"]+)(?P=q)\s*,""",
    re.IGNORECASE,
)
_CTRL_ARRAY = re.compile(
    r"""\[\s*(?P<cls>[A-Za-z_][\w\\]*)\s*::\s*class\s*,\s*['"](?P<meth>[A-Za-z_][\w]*)['"]\s*\]""",
)
_CTRL_AT = re.compile(
    r"""['"](?P<cls>[A-Za-z_][\w\\]*)@(?P<meth>[A-Za-z_][\w]*)['"]""",
)
_CLOSURE = re.compile(r"""(?:static\s+)?function\s*\(|fn\s*\(""")


def _line_of(text: str, idx: int) -> int:
    return text.count("\n", 0, idx) + 1


def _class_basename(name: str) -> str:
    return name.replace("\\", "/").rsplit("/", 1)[-1]


def _find_controller(root: Path, class_name: str) -> str | None:
    """在 app/Http/Controllers、app/ 下按 {Class}.php 查找；再全仓 walk（跳过 vendor）。"""
    base = _class_basename(class_name)
    if not base or not base[0].isalpha():
        return None
    filename = f"{base}.php"
    preferred = [
        root / "app" / "Http" / "Controllers",
        root / "app" / "Controllers",
        root / "app",
    ]
    for base_dir in preferred:
        if not base_dir.is_dir():
            continue
        for path in base_dir.rglob(filename):
            if not path.is_file():
                continue
            if SKIP_DIR_NAMES.intersection(path.parts):
                continue
            try:
                return rel_posix(root, path)
            except ValueError:
                continue
    for path in walk_files(root, (".php",)):
        if path.name != filename:
            continue
        try:
            return rel_posix(root, path)
        except ValueError:
            continue
    return None


def _parse_handler(arg_slice: str) -> tuple[str | None, str | None, bool]:
    """返回 (class_name, method, is_closure)。"""
    m = _CTRL_ARRAY.search(arg_slice)
    if m:
        return m.group("cls"), m.group("meth"), False
    m = _CTRL_AT.search(arg_slice)
    if m:
        return m.group("cls"), m.group("meth"), False
    if _CLOSURE.search(arg_slice):
        return None, None, True
    return None, None, False


def _arg_slice_after_path(text: str, comma_end: int) -> str:
    """取 path 后逗号起至匹配右括号的片段（粗切，足够匹配控制器字面量）。"""
    depth = 1
    i = comma_end
    start = comma_end
    while i < len(text):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                return text[start:i]
        i += 1
    return text[start:start + 400]


def parse_laravel_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".php",)):
        text = read_text(path)
        if "Route::" not in text:
            continue
        route_rel = rel_posix(root, path)
        for m in _ROUTE_HEAD.finditer(text):
            method = m.group("m").upper()
            if method in {"ANY", "MATCH"}:
                methods = ("GET", "POST")
            else:
                methods = (method,)
            arg_slice = _arg_slice_after_path(text, m.end())
            cls, meth, is_closure = _parse_handler(arg_slice)
            handler_file = route_rel
            handler_symbol: str | None = None
            if cls and meth and not is_closure:
                resolved = _find_controller(root, cls)
                if resolved:
                    handler_file = resolved
                handler_symbol = meth
            elif is_closure:
                handler_symbol = None

            extra: list[str] = []
            if handler_file.endswith(".php"):
                extra = extract_php_id_params(
                    read_text(root / handler_file),
                    enabled_frameworks={"laravel"},
                )

            line = _line_of(text, m.start())
            for http in methods:
                out.append(make_endpoint(
                    method=http,
                    path=m.group("p"),
                    handler_file=handler_file,
                    handler_symbol=handler_symbol,
                    line_start=line,
                    extra_params=extra,
                    parser="laravel",
                    acquisition="router",
                    route_file=route_rel,
                ))
    return out
