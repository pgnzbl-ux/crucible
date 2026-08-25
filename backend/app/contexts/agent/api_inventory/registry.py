"""按画像语言选择确定性 parser；禁止 AI 列端点。"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .django_parser import parse_django_repo
from .express_parser import parse_express_repo
from .fastapi_parser import parse_fastapi_repo
from .flask_parser import parse_flask_repo
from .gin_parser import parse_gin_repo
from .laravel_parser import parse_laravel_repo
from .models import (
    SUPPORTED_INVENTORY_LANGUAGES,
    EndpointRecord,
    canonical_language_ids,
    records_to_bom,
)
from .nestjs_parser import parse_nestjs_repo
from .nextjs_parser import parse_nextjs_repo
from .openapi_parser import parse_openapi_repo
from .php_script_parser import parse_php_script_repo
from .spring_parser import parse_spring_repo

ParseFn = Callable[[Path], list[EndpointRecord]]


@dataclass(frozen=True)
class ParserSpec:
    key: str
    language: str | None
    parse: ParseFn
    always: bool = False


PARSER_SPECS: tuple[ParserSpec, ...] = (
    ParserSpec("openapi", None, parse_openapi_repo, always=True),
    ParserSpec("fastapi", "python", parse_fastapi_repo),
    ParserSpec("flask", "python", parse_flask_repo),
    ParserSpec("django", "python", parse_django_repo),
    ParserSpec("express", "nodejs", parse_express_repo),
    ParserSpec("nextjs", "nodejs", parse_nextjs_repo),
    ParserSpec("nestjs", "nodejs", parse_nestjs_repo),
    ParserSpec("php_script", "php", parse_php_script_repo),
    ParserSpec("laravel", "php", parse_laravel_repo),
    ParserSpec("spring", "java", parse_spring_repo),
    ParserSpec("gin", "go", parse_gin_repo),
)


def select_parsers(profile: Any) -> list[ParserSpec]:
    langs = set(canonical_language_ids(profile))
    selected: list[ParserSpec] = []
    for spec in PARSER_SPECS:
        if spec.always:
            selected.append(spec)
            continue
        if spec.language and spec.language in langs:
            selected.append(spec)
    if langs:
        return selected
    return [s for s in selected if s.always]


def parser_keys_for_profile(profile: Any) -> list[str]:
    return [s.key for s in select_parsers(profile)]


def unsupported_for_profile(profile: Any) -> list[str]:
    langs = canonical_language_ids(profile)
    if not langs:
        return ["unknown"]
    return sorted({lid for lid in langs if lid not in SUPPORTED_INVENTORY_LANGUAGES})


def phase_message_for_profile(profile: Any) -> str:
    langs = canonical_language_ids(profile)
    keys = parser_keys_for_profile(profile)
    lang_label = "、".join(langs) if langs else "未知"
    parser_label = "/".join(keys) if keys else "openapi"
    return f"按画像 {lang_label} 解析 {parser_label}"


def build_inventory_bom(repo_root: str | Path, profile: Any = None) -> dict[str, Any]:
    root = Path(repo_root)
    specs = select_parsers(profile)
    endpoints: list[EndpointRecord] = []
    ran: list[str] = []
    kinds: list[str] = []
    for spec in specs:
        ran.append(spec.key)
        chunk = spec.parse(root)
        endpoints.extend(chunk)
        for ep in chunk:
            if ep.acquisition and ep.acquisition not in kinds:
                kinds.append(ep.acquisition)
    return records_to_bom(endpoints, parsers=ran, acquisition_kinds=kinds)
