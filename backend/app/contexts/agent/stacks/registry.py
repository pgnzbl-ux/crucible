"""语言/框架单一注册表 — 画像 / Semgrep / 函数索引 / 清单选型只读此处。

禁止在 profile_detector、semgrep_rules、context_extractor、api_inventory
各自维护第四套语言 id 表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 画像 languages[].id 权威集合
PROFILE_LANGUAGE_IDS = frozenset({
    "nodejs", "python", "java", "go", "php", "rust", "static",
})

# 画像语言 → Semgrep 本地规则目录名（禁止把 nodejs 直接当 --config）
SEMGREP_DIRS_BY_PROFILE: dict[str, tuple[str, ...]] = {
    "python": ("python",),
    "java": ("java",),
    "nodejs": ("javascript", "typescript"),
    "go": ("go",),
    "php": ("php",),
}

# 画像语言 → tree-sitter 函数索引语言
INDEX_LANGS_BY_PROFILE: dict[str, tuple[str, ...]] = {
    "python": ("python",),
    "java": ("java",),
    "nodejs": ("javascript", "typescript"),
    "go": ("go",),
    "php": ("php",),
}

ALL_INDEX_LANGS = frozenset({
    "python", "java", "javascript", "typescript", "go", "php",
})

# 清单支持的语言（无适配 parser 的进 unsupported）
INVENTORY_LANGUAGES = frozenset({"python", "nodejs", "php", "java", "go"})

LANG_ALIASES = {
    "py": "python",
    "js": "nodejs",
    "javascript": "nodejs",
    "typescript": "nodejs",
    "ts": "nodejs",
    "golang": "go",
}

# 画像 framework id → 清单 parser key（与 PARSER_SPECS.key 对齐）
FRAMEWORK_ALIASES: dict[str, str] = {
    "spring-boot": "spring",
    "springframework": "spring",
    "next": "nextjs",
    "nuxt": "nextjs",
    "ci": "codeigniter",
    "codeigniter4": "codeigniter",
    "yii2": "yii",
    "zend": "laminas",
    "zendframework": "laminas",
}

# 语言 → 该语言下全部清单 parser keys（frameworks 空时的回退）
INVENTORY_PARSERS_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "python": ("fastapi", "flask", "django"),
    "nodejs": ("express", "nextjs", "nestjs"),
    "php": ("php_script", "laravel"),
    "java": ("spring",),
    "go": ("gin",),
}

# 语言的 script_file parser（frameworks 空或未适配时的座位）
INVENTORY_SCRIPT_BY_LANGUAGE: dict[str, str] = {
    "php": "php_script",
}

# framework pack id → parser key（有 routes 的框架）
INVENTORY_FRAMEWORK_PARSERS: dict[str, str] = {
    "fastapi": "fastapi",
    "flask": "flask",
    "django": "django",
    "express": "express",
    "nextjs": "nextjs",
    "nestjs": "nestjs",
    "laravel": "laravel",
    "spring": "spring",
    "gin": "gin",
    # surface-only packs（尚无独立 routes parser；选型时不启专用 key，靠 script+surface）
    "symfony": "",
    "thinkphp": "",
    "codeigniter": "",
    "yii": "",
    "cakephp": "",
    "phalcon": "",
    "laminas": "",
    "fuel": "",
    "wordpress": "",
    "slim": "",
}


@dataclass(frozen=True)
class InventoryPlan:
    """清单一次运行启用的 parser keys + 传参面框架 id。"""

    parser_keys: tuple[str, ...]
    surface_frameworks: tuple[str, ...]
    stack_ids: tuple[str, ...]
    degraded: bool = False
    note: str = ""


def canonicalize_language(lid: str) -> str:
    raw = (lid or "").strip().lower()
    return LANG_ALIASES.get(raw, raw)


def canonicalize_framework(name: str) -> str:
    raw = (name or "").strip().lower()
    return FRAMEWORK_ALIASES.get(raw, raw)


def semgrep_dirs_for_languages(language_ids: list[dict] | list[Any]) -> list[str]:
    """由画像 languages 派生 semgrep_configs（目录名列表，去重保序）。"""
    out: list[str] = []
    seen: set[str] = set()
    for item in language_ids or []:
        if isinstance(item, dict):
            lid = item.get("id")
        else:
            lid = getattr(item, "id", None)
        if not lid:
            continue
        canon = canonicalize_language(str(lid))
        for cfg in SEMGREP_DIRS_BY_PROFILE.get(canon, ()):
            if cfg not in seen:
                seen.add(cfg)
                out.append(cfg)
    return out


def index_langs_for_profile_ids(profile_language_ids: list[str] | None) -> list[str]:
    """None = 全语言；空列表 = 空索引（有画像但无语言事实时 fail-soft）。"""
    if profile_language_ids is None:
        return sorted(ALL_INDEX_LANGS)
    wanted: set[str] = set()
    for lid in profile_language_ids:
        wanted.update(INDEX_LANGS_BY_PROFILE.get(canonicalize_language(str(lid)), ()))
    return sorted(wanted & ALL_INDEX_LANGS)


def normalize_frameworks(raw: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in raw or []:
        fw = canonicalize_framework(str(item))
        if fw and fw not in seen:
            seen.add(fw)
            out.append(fw)
    return out


def plan_inventory(
    *,
    languages: list[str],
    frameworks: list[str] | None = None,
) -> InventoryPlan:
    """根据画像语言 + 框架决定启用哪些清单 parser 与 surface。"""
    langs = [canonicalize_language(x) for x in languages if x]
    langs = list(dict.fromkeys(langs))
    fws = normalize_frameworks(frameworks)
    keys: list[str] = ["openapi"]
    surfaces: list[str] = []
    stacks: list[str] = list(langs)
    degraded = False
    notes: list[str] = []

    for lang in langs:
        if lang not in INVENTORY_LANGUAGES:
            continue
        lang_parsers = INVENTORY_PARSERS_BY_LANGUAGE.get(lang, ())
        script_key = INVENTORY_SCRIPT_BY_LANGUAGE.get(lang)
        lang_fws = [f for f in fws if _framework_belongs_to_lang(f, lang)]

        if lang_fws:
            hit_route = False
            for fw in lang_fws:
                stacks.append(f"{lang}:{fw}")
                route_key = INVENTORY_FRAMEWORK_PARSERS.get(fw, "")
                if route_key:
                    keys.append(route_key)
                    hit_route = True
                # surface-only 或有 routes 的框架都可启用 surface id
                if fw in INVENTORY_FRAMEWORK_PARSERS or fw in {
                    "symfony", "thinkphp", "codeigniter", "yii", "cakephp",
                    "phalcon", "laminas", "fuel", "wordpress", "slim",
                }:
                    surfaces.append(fw)
            if not hit_route and script_key:
                keys.append(script_key)
                degraded = True
                notes.append(f"{lang} frameworks={lang_fws} 无 routes 适配，降级 {script_key}")
            elif script_key and lang == "php" and not hit_route:
                pass
        else:
            # 无框架：有 script 只用 script；否则回退该语言全部 parser
            if script_key:
                keys.append(script_key)
            else:
                keys.extend(lang_parsers)

    # 去重保序
    keys = list(dict.fromkeys(keys))
    surfaces = list(dict.fromkeys(surfaces))
    stacks = list(dict.fromkeys(stacks))
    return InventoryPlan(
        parser_keys=tuple(keys),
        surface_frameworks=tuple(surfaces),
        stack_ids=tuple(stacks),
        degraded=degraded,
        note="; ".join(notes),
    )


def _framework_belongs_to_lang(fw: str, lang: str) -> bool:
    by_lang = {
        "python": {"fastapi", "flask", "django", "streamlit", "tornado", "starlette"},
        "nodejs": {"express", "nextjs", "nestjs", "koa", "fastify", "next", "nuxt"},
        "php": {
            "laravel", "symfony", "thinkphp", "codeigniter", "yii", "cakephp",
            "phalcon", "laminas", "fuel", "wordpress", "slim",
        },
        "java": {"spring", "spring-boot", "quarkus"},
        "go": {"gin", "echo", "chi", "fiber"},
        "rust": {"actix-web", "axum", "rocket"},
    }
    return fw in by_lang.get(lang, set()) or canonicalize_framework(fw) in by_lang.get(lang, set())
