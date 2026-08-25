"""仓库内 OpenAPI / Swagger 文件。"""
from __future__ import annotations

import json
from pathlib import Path

import yaml

from .models import EndpointRecord, make_endpoint, read_text, rel_posix, walk_files

_HTTP = ("get", "post", "put", "patch", "delete", "options", "head", "trace")
_NAMES = {
    "openapi.json", "openapi.yaml", "openapi.yml",
    "swagger.json", "swagger.yaml", "swagger.yml",
}


def _load(path: Path, text: str) -> dict | None:
    try:
        if path.suffix.lower() == ".json":
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
    except (json.JSONDecodeError, yaml.YAMLError, OSError):
        return None
    return data if isinstance(data, dict) else None


def parse_openapi_repo(repo_root: Path) -> list[EndpointRecord]:
    root = Path(repo_root)
    out: list[EndpointRecord] = []
    for path in walk_files(root, (".json", ".yaml", ".yml")):
        if path.name.lower() not in _NAMES and "openapi" not in path.name.lower() and "swagger" not in path.name.lower():
            continue
        data = _load(path, read_text(path))
        if not data or not isinstance(data.get("paths"), dict):
            continue
        rel = rel_posix(root, path)
        for raw_path, ops in data["paths"].items():
            if not isinstance(ops, dict):
                continue
            for method, spec in ops.items():
                if str(method).lower() not in _HTTP:
                    continue
                symbol = None
                if isinstance(spec, dict):
                    symbol = spec.get("operationId")
                out.append(make_endpoint(
                    method=str(method).upper(),
                    path=str(raw_path),
                    handler_file=rel,
                    handler_symbol=str(symbol) if symbol else None,
                    parser="openapi",
                    acquisition="openapi",
                ))
    return out
