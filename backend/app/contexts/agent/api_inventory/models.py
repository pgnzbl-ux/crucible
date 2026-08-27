"""API 清单共享记录与仓库遍历。"""
from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ACQUISITION_ROUTER = "router"
ACQUISITION_SCRIPT = "script_file"
ACQUISITION_OPENAPI = "openapi"
ACQUISITION_EXPORT = "export_handler"

HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "trace"})
ID_PARAM_RE = re.compile(
    r"\{(?P<name>[^}:]+)(?::[^}]*)?\}|:(?P<colon>[A-Za-z_][\w]*)|<(?:[^:>]+:)?(?P<django>[A-Za-z_][\w]*)>",
)
OBJECT_ID_NAMES = frozenset({
    "id", "uuid", "pk",
    "user_id", "userid", "uid",
    "order_id", "orderid",
    "account_id", "org_id", "tenant_id", "project_id", "file_id", "item_id",
})
ADMIN_HINTS = ("/admin", "/internal", "/debug", "/manage")
SKIP_DIR_NAMES = frozenset({
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".tox",
    "dist", "build", ".eggs", ".mypy_cache", ".pytest_cache",
    "vendor", "target", "coverage", ".next",
})

from app.contexts.agent.stacks.registry import (  # noqa: E402
    INVENTORY_LANGUAGES as SUPPORTED_INVENTORY_LANGUAGES,
    LANG_ALIASES,
)


@dataclass
class EndpointRecord:
    method: str
    path_template: str
    handler_file: str
    handler_symbol: str | None
    line_start: int | None
    params: list[str] = field(default_factory=list)
    id_params: list[str] = field(default_factory=list)
    has_object_id: bool = False
    auth_observed: list[str] = field(default_factory=list)
    is_pve: bool = False
    resource_key: str = ""
    endpoint_id: str = ""
    parser: str = ""
    acquisition: str = ACQUISITION_ROUTER
    route_file: str = ""


def normalize_path(path: str) -> str:
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    p = re.sub(r"/+", "/", p.rstrip("/") or "/")

    def _brace(m: re.Match[str]) -> str:
        name = (m.group("name") or m.group("colon") or m.group("django") or "").split(":")[0].strip()
        return "{" + name + "}" if name else m.group(0)

    return ID_PARAM_RE.sub(_brace, p)


def path_params(path: str) -> list[str]:
    names: list[str] = []
    for m in ID_PARAM_RE.finditer(path or ""):
        name = (m.group("name") or m.group("colon") or m.group("django") or "").strip()
        if name:
            names.append(name.split(":")[0])
    return names


def is_object_id(name: str) -> bool:
    n = (name or "").lower()
    if n in OBJECT_ID_NAMES:
        return True
    return n.endswith("_id") or n.endswith("id") and len(n) > 2


def looks_admin(path: str) -> bool:
    low = (path or "").lower()
    return any(h in low for h in ADMIN_HINTS)


def resource_key_for(path_template: str, id_params: list[str]) -> str:
    primary = id_params[0] if id_params else ""
    raw = f"{normalize_path(path_template)}|{primary}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def make_endpoint(
    *,
    method: str,
    path: str,
    handler_file: str,
    handler_symbol: str | None = None,
    line_start: int | None = None,
    extra_params: Iterable[str] = (),
    auth_observed: Iterable[str] = (),
    parser: str,
    acquisition: str,
    route_file: str | None = None,
) -> EndpointRecord:
    path = normalize_path(path)
    params = list(path_params(path))
    for name in extra_params:
        if name not in params and is_object_id(name):
            params.append(name)
    id_params = [p for p in params if is_object_id(p)]
    auth = sorted({str(x) for x in auth_observed if x})
    has_oid = bool(id_params)
    is_pve = has_oid or looks_admin(path)
    return EndpointRecord(
        method=str(method or "GET").upper(),
        path_template=path,
        handler_file=handler_file,
        handler_symbol=handler_symbol,
        line_start=line_start,
        params=params,
        id_params=id_params,
        has_object_id=has_oid,
        auth_observed=auth,
        is_pve=is_pve,
        resource_key=resource_key_for(path, id_params),
        endpoint_id=f"{str(method or 'GET').upper()} {path}",
        parser=parser,
        acquisition=acquisition,
        route_file=route_file or "",
    )


def walk_files(repo_root: Path, suffixes: tuple[str, ...]) -> Iterable[Path]:
    root = Path(repo_root)
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in suffixes:
            continue
        if SKIP_DIR_NAMES.intersection(path.parts):
            continue
        yield path


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def rel_posix(root: Path, path: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def profile_language_ids(profile: Any) -> list[str]:
    if profile is None:
        return []
    ids: list[str] = []
    for fact in getattr(profile, "languages", None) or []:
        if isinstance(fact, dict):
            lid = fact.get("id")
        else:
            lid = getattr(fact, "id", None)
        if lid:
            ids.append(str(lid).lower())
    primary = getattr(profile, "primary_language", None) or getattr(profile, "language", None)
    if primary:
        ids.append(str(primary).lower())
    out: list[str] = []
    seen: set[str] = set()
    for lid in ids:
        if lid and lid not in seen:
            seen.add(lid)
            out.append(lid)
    return out


def canonical_language_ids(profile: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for lid in profile_language_ids(profile):
        canon = LANG_ALIASES.get(lid, lid)
        if canon and canon not in seen:
            seen.add(canon)
            out.append(canon)
    return out


def profile_frameworks(profile: Any) -> list[str]:
    if profile is None:
        return []
    names: list[str] = []
    for item in getattr(profile, "frameworks", None) or []:
        names.append(str(item).lower())
    single = getattr(profile, "framework", None)
    if single:
        names.append(str(single).lower())
    return list(dict.fromkeys(names))


def dedupe_endpoints(endpoints: list[EndpointRecord]) -> list[EndpointRecord]:
    seen: set[str] = set()
    uniq: list[EndpointRecord] = []
    for ep in endpoints:
        key = f"{ep.method}|{ep.path_template}|{ep.handler_file}|{ep.handler_symbol}"
        if key in seen:
            continue
        seen.add(key)
        uniq.append(ep)
    return uniq


def records_to_bom(
    endpoints: list[EndpointRecord],
    *,
    parsers: list[str],
    acquisition_kinds: list[str],
) -> dict[str, Any]:
    uniq = dedupe_endpoints(endpoints)
    records = [asdict(ep) for ep in uniq]
    pve = [r for r in records if r.get("is_pve")]
    parser = ",".join(parsers) if parsers else "none"
    return {
        "parser": parser,
        "parsers": parsers,
        "acquisition_kinds": acquisition_kinds,
        "endpoint_count": len(records),
        "pve_count": len(pve),
        "endpoints": records,
    }


def prioritize_pve(endpoints: list[dict[str, Any]], *, top_k: int) -> list[dict[str, Any]]:
    """规则优先：含 object-id / admin / 写操作。

    若没有任何 is_pve：对 script_file **或** router 端点按写操作/admin 降级 Top-K，
    避免传统 PHP 与无路径 {id} 的框架路由永久空跑。
    """
    def score(ep: dict[str, Any]) -> tuple:
        method = str(ep.get("method") or "GET").upper()
        write = 0 if method in ("POST", "PUT", "PATCH", "DELETE") else 1
        oid = 0 if ep.get("has_object_id") else 1
        admin = 0 if looks_admin(str(ep.get("path_template") or "")) else 1
        auth_gap = 0 if not (ep.get("auth_observed") or []) else 1
        return (oid, write, admin, auth_gap, str(ep.get("endpoint_id") or ""))

    cands = [e for e in endpoints if e.get("is_pve")]
    if not cands:
        acq_ok = {ACQUISITION_SCRIPT, ACQUISITION_ROUTER}
        cands = [
            e for e in endpoints
            if str(e.get("acquisition") or "") in acq_ok
        ]
    cands.sort(key=score)
    return cands[: max(0, top_k)]


def group_by_resource_key(endpoints: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    buckets: dict[str, list[dict[str, Any]]] = {}
    for ep in endpoints:
        key = str(ep.get("resource_key") or ep.get("endpoint_id") or "")
        buckets.setdefault(key, []).append(ep)
    return sorted(buckets.values(), key=lambda xs: (-len(xs), xs[0].get("endpoint_id") or ""))
