"""语言/框架栈注册（单一真相）。"""
from __future__ import annotations

from .registry import (
    ALL_INDEX_LANGS,
    FRAMEWORK_ALIASES,
    INVENTORY_LANGUAGES,
    LANG_ALIASES,
    PROFILE_LANGUAGE_IDS,
    InventoryPlan,
    canonicalize_framework,
    canonicalize_language,
    index_langs_for_profile_ids,
    normalize_frameworks,
    plan_inventory,
    semgrep_dirs_for_languages,
)

__all__ = [
    "ALL_INDEX_LANGS",
    "FRAMEWORK_ALIASES",
    "INVENTORY_LANGUAGES",
    "LANG_ALIASES",
    "PROFILE_LANGUAGE_IDS",
    "InventoryPlan",
    "canonicalize_framework",
    "canonicalize_language",
    "index_langs_for_profile_ids",
    "normalize_frameworks",
    "plan_inventory",
    "semgrep_dirs_for_languages",
]
