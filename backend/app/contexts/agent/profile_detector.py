"""节点 1 项目画像规则引擎。

把 plugin run-project-env/references/project-detection.md(7 语言规则表)
+ web-detection.md(web 门禁关键字表)翻成 Python 确定性检测。
画像后按 commit SHA 写入 source_artifacts.profile_json,后续同 SHA 任务复用,省 AI。

discovery-spec §6.0：语言是**多事实列表**(禁止 first-match 短路)；
semgrep_configs / osv_manifests / package_managers 由纯函数派生，禁止 AI 写。
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# 语言检测规则(触发文件 → 语言)(对齐 project-detection.md)；顺序 = 权重平手时的 tie-break
LANGUAGE_RULES: list[tuple[str, list[str]]] = [
    ("nodejs", ["package.json"]),
    ("python", ["requirements.txt", "pyproject.toml"]),
    ("java", ["pom.xml", "build.gradle"]),
    ("go", ["go.mod"]),
    ("php", ["composer.json", "index.php"]),
    ("rust", ["Cargo.toml"]),
    ("static", ["index.html"]),  # 仅静态 HTML(最低优先级；根目录命中才计)
]

# 触发文件扫描时剪枝的目录(node_modules 里的 package.json 不是项目语言证据)
TRIGGER_EXCLUDED_DIRS = {
    "node_modules", "vendor", ".venv", "venv", ".git", "dist", "build",
    "target", ".tox", "fixtures", "__pycache__", "site-packages",
}

# 参与子目录扫描的触发文件(index.html 只认根目录；index.php 只认根/public)
_TRIGGER_FILENAMES = {
    "package.json", "requirements.txt", "pyproject.toml", "pom.xml",
    "build.gradle", "go.mod", "composer.json", "Cargo.toml",
}

KNOWN_LANGUAGE_IDS = {"nodejs", "python", "java", "go", "php", "rust", "static"}

# semgrep --config 派生表：权威在 stacks.registry.SEMGREP_DIRS_BY_PROFILE
from app.contexts.agent.stacks.registry import SEMGREP_DIRS_BY_PROFILE

SEMGREP_CONFIG_BY_LANGUAGE: dict[str, list[str]] = {
    k: list(v) for k, v in SEMGREP_DIRS_BY_PROFILE.items()
}


def _assert_semgrep_config_dirs_match_rules_tree() -> None:
    """启动期自检：派生表目标目录 ⊆ 规则库白名单。"""
    from app.core.semgrep_rules import ALLOWED_SEMGREP_LANG_DIRS, SemgrepLangDirError

    for lang_id, dirs in SEMGREP_CONFIG_BY_LANGUAGE.items():
        bad = [d for d in dirs if d not in ALLOWED_SEMGREP_LANG_DIRS]
        if bad:
            raise SemgrepLangDirError(
                f"SEMGREP_CONFIG_BY_LANGUAGE[{lang_id!r}]={dirs} 含非法目录 {bad}；"
                f"须与 backend/semgrep_rules/<dir> 文件夹名一致: "
                f"{sorted(ALLOWED_SEMGREP_LANG_DIRS)}"
            )


_assert_semgrep_config_dirs_match_rules_tree()

# osv-scanner 加速用：锁文件/依赖清单(相对路径)
OSV_MANIFEST_NAMES = (
    "package.json", "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "requirements.txt", "poetry.lock", "Pipfile.lock", "pyproject.toml",
    "pom.xml", "build.gradle",
    "go.mod", "go.sum",
    "composer.json", "composer.lock",
    "Cargo.lock", "Cargo.toml",
)
_PACKAGE_MANAGER_BY_MANIFEST = {
    "package-lock.json": "npm", "yarn.lock": "npm", "pnpm-lock.yaml": "npm", "package.json": "npm",
    "requirements.txt": "pip", "poetry.lock": "pip", "Pipfile.lock": "pip", "pyproject.toml": "pip",
    "pom.xml": "maven", "build.gradle": "gradle",
    "go.mod": "go", "go.sum": "go",
    "composer.json": "composer", "composer.lock": "composer",
    "Cargo.lock": "cargo", "Cargo.toml": "cargo",
}

# 框架关键字(语言 → {关键字 → 框架})
FRAMEWORK_KEYWORDS: dict[str, dict[str, str]] = {
    "nodejs": {
        "next": "next", "nuxt": "nuxt", "express": "express", "@nestjs/core": "nestjs",
        "koa": "koa", "fastify": "fastify",
    },
    "python": {
        "fastapi": "fastapi", "flask": "flask", "django": "django", "streamlit": "streamlit",
        "tornado": "tornado",
    },
    "java": {"spring-boot-starter": "spring-boot", "springframework": "spring-boot", "quarkus": "quarkus"},
    "go": {"gin": "gin", "echo": "echo"},
    "php": {
        "laravel": "laravel",
        "symfony": "symfony",
        "thinkphp": "thinkphp",
        "topthink": "thinkphp",
        "codeigniter": "codeigniter",
        "yiisoft/yii2": "yii",
        "cakephp": "cakephp",
        "phalcon": "phalcon",
        "laminas": "laminas",
        "zendframework": "laminas",
        "slim/slim": "slim",
        "wordpress": "wordpress",
    },
    "rust": {"actix-web": "actix-web", "axum": "axum", "rocket": "rocket"},
}

# web 框架(web 门禁 is_web=True 的信号)
WEB_FRAMEWORKS = {
    "express", "nestjs", "koa", "fastify", "next", "nuxt",
    "fastapi", "flask", "django", "streamlit", "tornado",
    "spring-boot", "quarkus",
    "gin", "echo",
    "laravel", "symfony", "thinkphp", "codeigniter", "yii", "cakephp",
    "phalcon", "laminas", "slim", "wordpress",
    "actix-web", "axum", "rocket",
}

# web 门禁额外信号(web-detection.md)
WEB_SIGNALS = [
    r"server\.port", r"PORT\s*=", r"listen\s*\(\s*\d", r"app\.listen",
    r"swagger", r"openapi", r"controller",
]
NON_WEB_SIGNALS = [r"CLI\s+tool", r"command.line", r"desktop\s+app", r"batch\s+process"]

DEPENDENCY_FILES = {
    "nodejs": ["package.json"],
    "python": ["requirements.txt", "pyproject.toml"],
    "java": ["pom.xml", "build.gradle"],
    "go": ["go.mod"],
    "php": ["composer.json"],
    "rust": ["Cargo.toml"],
}


SPA_MARKERS = ('"vite"', "'vite'", '"react"', "'react'", '"vue"', "'vue'", '"svelte"', "'svelte'", '"@angular/core"')

_EVIDENCE_CAP = 20  # 单语言证据文件上限(防大仓库撑爆落库字段)
_DEP_BLOB_CAP = 500_000  # 框架检测依赖文件合计字符上限


def _read_file(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="ignore").lower()
    except OSError:
        return ""


def _readme_text(root: Path) -> str:
    for rname in ["README.md", "readme.md", "README.rst", "README"]:
        if (root / rname).exists():
            return _read_file(root / rname)
    return ""


def _readme_matches(readme: str, patterns: list[str]) -> bool:
    return any(re.search(signal, readme, re.IGNORECASE) for signal in patterns)


def _has_php_entry(root: Path) -> bool:
    return (root / "index.php").exists() or (root / "public" / "index.php").exists()


def _has_java_web(root: Path) -> bool:
    return (root / "WEB-INF" / "web.xml").exists() or (
        root / "src" / "main" / "webapp" / "WEB-INF" / "web.xml"
    ).exists()


def _java_web_evidence(root: Path) -> str | None:
    if (root / "src" / "main" / "webapp" / "WEB-INF" / "web.xml").exists():
        return "src/main/webapp/WEB-INF/web.xml"
    if (root / "WEB-INF" / "web.xml").exists():
        return "WEB-INF/web.xml"
    return None


def _has_spa_marker(root: Path) -> bool:
    pkg = root / "package.json"
    if not pkg.exists():
        return False
    blob = _read_file(pkg)
    return any(marker in blob for marker in SPA_MARKERS)


def _has_strong_web(root: Path, language: str | None, framework: str | None) -> bool:
    if framework and framework in WEB_FRAMEWORKS:
        return True
    if language == "static":
        return True
    if _has_php_entry(root):
        return True
    if _has_java_web(root):
        return True
    if language == "nodejs" and _has_spa_marker(root):
        return True
    return False


def _has_strong_non_web(root: Path, language: str | None, framework: str | None) -> bool:
    if _has_strong_web(root, language, framework):
        return False
    readme = _readme_text(root)
    return _readme_matches(readme, NON_WEB_SIGNALS) and not _readme_matches(readme, WEB_SIGNALS)


def _walk_trigger_files(root: Path, filenames: set[str], cap: int = 200) -> dict[str, list[str]]:
    """收集触发文件相对路径；剪枝依赖/构建目录。"""
    hits: dict[str, list[str]] = {name: [] for name in filenames}
    found = 0
    for dirpath, dirnames, fnames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in TRIGGER_EXCLUDED_DIRS]
        for fn in fnames:
            if fn not in hits:
                continue
            rel_dir = os.path.relpath(dirpath, root)
            rel = fn if rel_dir == "." else f"{rel_dir.replace(os.sep, '/')}/{fn}"
            hits[fn].append(rel)
            found += 1
            if found >= cap:
                return hits
    return hits


def detect_languages(root: Path) -> list[dict]:
    """扫全仓触发文件返回多语言事实；禁止 first-match 短路(package.json 不得盖住后端语言)。"""
    hits = _walk_trigger_files(root, set(_TRIGGER_FILENAMES))
    facts: list[dict] = []
    for lang, files in LANGUAGE_RULES:
        if lang == "static":
            continue
        evidence: list[str] = []
        for f in files:
            if f == "index.php":
                continue  # PHP 页面文件遍地都是，只认根/public 入口
            evidence.extend(hits.get(f, []))
        if lang == "php" and _has_php_entry(root):
            entry = "public/index.php" if (root / "public" / "index.php").exists() else "index.php"
            if entry not in evidence:
                evidence.append(entry)
        if lang == "java":
            web_xml = _java_web_evidence(root)
            if web_xml and web_xml not in evidence:
                evidence.append(web_xml)
        if evidence:
            facts.append({
                "id": lang,
                "evidence_files": sorted(set(evidence))[:_EVIDENCE_CAP],
                "source": "rules",
                "confidence": 1.0,
            })
    if not facts and (root / "index.html").exists():
        facts.append({"id": "static", "evidence_files": ["index.html"], "source": "rules", "confidence": 1.0})
    return facts


def append_ai_language(languages: list[dict], ai_language: str | None) -> list[dict]:
    """AI 语言只能追加 source=ai 的低置信事实：不得覆盖 rules 证据，也不进 semgrep_configs。"""
    if not ai_language or ai_language not in KNOWN_LANGUAGE_IDS or ai_language == "static":
        return languages
    if any(f.get("id") == ai_language for f in languages):
        return languages
    return [*languages, {"id": ai_language, "evidence_files": [], "source": "ai", "confidence": 0.6}]


def _language_weight(fact: dict) -> tuple[int, int]:
    """证据权重：根目录文件计 2、子目录计 1；平手按 LANGUAGE_RULES 顺序。"""
    order = next((i for i, (lang, _) in enumerate(LANGUAGE_RULES) if lang == fact.get("id")), 99)
    weight = sum(2 if "/" not in p else 1 for p in (fact.get("evidence_files") or ["x"]))
    return (-weight, order)


def derive_primary_language(languages: list[dict]) -> str | None:
    """由 languages 按证据权重派生主语言；rules 事实优先，AI 追加项不夺主。"""
    rules = [f for f in languages if f.get("source") == "rules"]
    pool = rules or list(languages)
    if not pool:
        return None
    return min(pool, key=_language_weight)["id"]


def derive_semgrep_configs(languages: list[dict]) -> list[str]:
    """纯函数派生 semgrep --config 列表；只认 source!=ai 的事实(防 AI 谎报选错规则包)。

    产出的每一项都是规则库语言目录名（php/python/…），不是画像语言 id（nodejs）。
    """
    from app.core.semgrep_rules import require_allowed_lang_dirs
    from app.contexts.agent.stacks.registry import semgrep_dirs_for_languages

    facts = [f for f in languages if f.get("source") != "ai"]
    return require_allowed_lang_dirs(semgrep_dirs_for_languages(facts))


def derive_osv_manifests(root: Path) -> list[str]:
    hits = _walk_trigger_files(root, set(OSV_MANIFEST_NAMES), cap=100)
    merged = sorted(p for paths in hits.values() for p in set(paths))
    return merged[:100]


def derive_package_managers(manifests: list[str]) -> list[str]:
    result: list[str] = []
    for rel in manifests:
        name = rel.rsplit("/", 1)[-1]
        pm = _PACKAGE_MANAGER_BY_MANIFEST.get(name)
        if pm and pm not in result:
            result.append(pm)
    return result


def _detect_language(root: Path) -> str | None:
    """兼容旧调用：主语言 = 多事实派生结果。"""
    return derive_primary_language(detect_languages(root))


def _detect_framework(root: Path, language: str | None) -> str | None:
    frameworks = detect_frameworks(root, [language] if language else [])
    return frameworks[0] if frameworks else None


def detect_frameworks(root: Path, language_ids: list[str]) -> list[str]:
    """按语言顺序扫依赖文件关键字，返回命中的框架列表(主语言优先)。

    依赖文件与语言检测一样走全仓 walk（剪枝 vendor/node_modules），不只认根目录。
    """
    found: list[str] = []
    for language in language_ids:
        kws = FRAMEWORK_KEYWORDS.get(language, {})
        if not kws:
            continue
        dep_names = DEPENDENCY_FILES.get(language, [])
        if not dep_names:
            continue
        hits = _walk_trigger_files(root, set(dep_names), cap=50)
        parts: list[str] = []
        budget = _DEP_BLOB_CAP
        for name in dep_names:
            for rel in hits.get(name, []):
                if budget <= 0:
                    break
                chunk = _read_file(root / rel)
                if not chunk:
                    continue
                take = chunk[:budget]
                parts.append(take)
                budget -= len(take)
            if budget <= 0:
                break
        blob = "\n".join(parts)
        for kw, fw in kws.items():
            if kw in blob and fw not in found:
                found.append(fw)
    return found


def _detect_port(root: Path) -> int | None:
    candidates = [
        root / ".env", root / "application.yml", root / "application.yaml",
        root / "config" / "default.json", root / ".env.example",
    ]
    for p in candidates:
        if not p.exists():
            continue
        text = _read_file(p)
        m = re.search(r"(?:^|\n)\s*(?:port|PORT)\s*[=:]\s*(\d{2,5})", text)
        if m:
            return int(m.group(1))
    return None


def _is_web(root: Path, language: str | None, framework: str | None) -> bool:
    if _has_strong_web(root, language, framework):
        return True
    readme = _readme_text(root)
    return _readme_matches(readme, WEB_SIGNALS)


def _root_languages(root: Path) -> list[str]:
    return [f["id"] for f in detect_languages(root) if f["id"] != "static"]


def profile_needs_ai(source_path: str, hints: dict) -> bool:
    """遗留：强 Web / 强非 Web 是否可跳过 AI。

    ProfileNode 在 SDK 开启且无缓存时已一律轻度 AI，不再调用本函数；
    保留供单测与启发式对照。
    """
    root = Path(source_path)
    language = hints.get("primary_language") or hints.get("language")
    framework = hints.get("framework")
    if not language:
        return True
    if len(_root_languages(root)) > 1:
        return True
    if _has_strong_web(root, language, framework):
        return False
    if _has_strong_non_web(root, language, framework):
        return False
    return True


def detect_profile(source_path: str) -> dict:
    """对 clone 后的源码做画像,返回节点 1 output schema(含派生字段,见 discovery-spec §6.0)。

    source_path 指向仓库根（host_workdir/{repo_dirname}）。
    """
    root = Path(source_path)
    languages = detect_languages(root)
    primary = derive_primary_language(languages)
    lang_ids = [f["id"] for f in languages if f["id"] != "static"]
    frameworks = detect_frameworks(root, lang_ids)
    framework = frameworks[0] if frameworks else None
    port = _detect_port(root)
    is_web = _is_web(root, primary, framework)
    has_dockerfile = (root / "Dockerfile").exists() or any(root.glob("Dockerfile*"))
    has_compose = any(root.glob("docker-compose*.yml")) or any(root.glob("docker-compose*.yaml"))
    osv_manifests = derive_osv_manifests(root)

    return {
        "is_web": is_web,
        "languages": languages,
        "primary_language": primary,
        "language": primary,  # 兼容 env_ready 旧 Input / 落库
        "frameworks": frameworks,
        "framework": framework,  # = frameworks[0]，兼容旧字段
        "package_managers": derive_package_managers(osv_manifests),
        "semgrep_configs": derive_semgrep_configs(languages),
        "osv_manifests": osv_manifests,
        "port": port,
        "has_dockerfile": has_dockerfile,
        "has_compose": has_compose,
        "detected_services": [],
        "start_command": None,
        "non_web_reason": None if is_web else "rule-engine: 无 web 入口证据",
        "profile_source": "rules",
    }
