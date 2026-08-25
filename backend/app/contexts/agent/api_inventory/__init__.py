"""api_inventory 包。"""
from __future__ import annotations

from .fastapi_parser import build_fastapi_bom, parse_fastapi_file
from .models import group_by_resource_key, prioritize_pve
from .registry import (
    build_inventory_bom,
    parser_keys_for_profile,
    select_parsers,
    unsupported_for_profile,
)

__all__ = [
    "build_fastapi_bom",
    "build_inventory_bom",
    "group_by_resource_key",
    "parse_fastapi_file",
    "parser_keys_for_profile",
    "prioritize_pve",
    "select_parsers",
    "unsupported_for_profile",
]
