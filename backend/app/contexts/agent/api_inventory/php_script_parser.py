"""传统 PHP：URL 与 webroot 下 .php 文件同构。"""
from __future__ import annotations

from pathlib import Path

from .models import SKIP_DIR_NAMES, EndpointRecord, make_endpoint, read_text, rel_posix, walk_files
from .php_request_surface import extract_php_id_params

_WEBROOTS = ("public", "www", "web", "htdocs")
_FRAMEWORK_DIRS = frozenset({
    "app", "src", "vendor", "tests", "Tests", "storage", "bootstrap",
    "config", "database", "resources", "routes", "node_modules",
    "cgi-bin", "bin", "lib", "vendor-bin",
})


def _webroot(repo_root: Path) -> Path | None:
    for name in _WEBROOTS:
        cand = repo_root / name
        if cand.is_dir():
            return cand
    return None


def _url_for(webroot: Path, path: Path) -> str:
    rel = rel_posix(webroot, path)
    if rel.endswith("/index.php"):
        rel = rel[: -len("index.php")].rstrip("/")
        return "/" + rel if rel else "/"
    if rel == "index.php":
        return "/"
    if rel.endswith(".php"):
        rel = rel[: -len(".php")]
    return "/" + rel


def _candidate_files(root: Path) -> list[Path]:
    webroot = _webroot(root)
    if webroot is not None:
        return [p for p in walk_files(webroot, (".php",)) if not _FRAMEWORK_DIRS.intersection(p.parts)]
    files: list[Path] = []
    for path in root.glob("*.php"):
        if path.is_file():
            files.append(path)
    for sub in root.iterdir():
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        if sub.name in _FRAMEWORK_DIRS or sub.name in SKIP_DIR_NAMES:
            continue
        for path in walk_files(sub, (".php",)):
            files.append(path)
    return files


def parse_php_script_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    webroot = _webroot(root) or root
    out: list[EndpointRecord] = []
    for path in _candidate_files(root):
        try:
            rel_file = rel_posix(root, path)
        except ValueError:
            continue
        url = _url_for(webroot, path)
        extra = extract_php_id_params(read_text(path), enabled_frameworks=set())
        for method in ("GET", "POST"):
            out.append(make_endpoint(
                method=method,
                path=url,
                handler_file=rel_file,
                extra_params=extra,
                parser="php_script",
                acquisition="script_file",
            ))
    return out
