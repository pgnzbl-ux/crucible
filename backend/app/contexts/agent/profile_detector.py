"""节点 1 项目画像规则引擎。

把 plugin run-project-env/references/project-detection.md(7 语言规则表)
+ web-detection.md(web 门禁关键字表)翻成 Python 确定性检测。
画像后按 commit SHA 写入 source_artifacts.profile_json,后续同 SHA 任务复用,省 AI。
"""
from __future__ import annotations

import re
from pathlib import Path

# 语言检测规则(触发文件 → 语言)(对齐 project-detection.md)
LANGUAGE_RULES: list[tuple[str, list[str]]] = [
    ("nodejs", ["package.json"]),
    ("python", ["requirements.txt", "pyproject.toml"]),
    ("java", ["pom.xml", "build.gradle"]),
    ("go", ["go.mod"]),
    ("php", ["composer.json", "index.php"]),
    ("rust", ["Cargo.toml"]),
    ("static", ["index.html"]),  # 仅静态 HTML(最低优先级)
]

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
    "php": {"laravel": "laravel", "symfony": "symfony"},
    "rust": {"actix-web": "actix-web", "axum": "axum", "rocket": "rocket"},
}

# web 框架(web 门禁 is_web=True 的信号)
WEB_FRAMEWORKS = {
    "express", "nestjs", "koa", "fastify", "next", "nuxt",
    "fastapi", "flask", "django", "streamlit", "tornado",
    "spring-boot", "quarkus",
    "gin", "echo",
    "laravel", "symfony",
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


def _detect_language(root: Path) -> str | None:
    for lang, files in LANGUAGE_RULES:
        if any((root / f).exists() for f in files):
            return lang
    if _has_php_entry(root):
        return "php"
    if _has_java_web(root):
        return "java"
    return None


def _detect_framework(root: Path, language: str | None) -> str | None:
    if not language:
        return None
    kws = FRAMEWORK_KEYWORDS.get(language, {})
    if not kws:
        return None
    dep_files = DEPENDENCY_FILES.get(language, [])
    blob = "\n".join(_read_file(root / f) for f in dep_files if (root / f).exists())
    for kw, fw in kws.items():
        if kw in blob:
            return fw
    return None


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
    found: list[str] = []
    for lang, files in LANGUAGE_RULES:
        if lang == "static":
            continue
        if any((root / f).exists() for f in files):
            found.append(lang)
    return found


def profile_needs_ai(source_path: str, hints: dict) -> bool:
    """三档：强 Web / 强非 Web 走规则；其余（含有语言无框架）必须问 AI。"""
    root = Path(source_path)
    language = hints.get("language")
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
    """对 clone 后的源码做画像,返回节点 1 output schema。

    source_path 指向仓库根（host_workdir/{repo_dirname}）。
    """
    root = Path(source_path)
    language = _detect_language(root)
    framework = _detect_framework(root, language)
    port = _detect_port(root)
    is_web = _is_web(root, language, framework)
    has_dockerfile = (root / "Dockerfile").exists() or any(root.glob("Dockerfile*"))
    has_compose = any(root.glob("docker-compose*.yml")) or any(root.glob("docker-compose*.yaml"))

    return {
        "is_web": is_web,
        "language": language,
        "framework": framework,
        "port": port,
        "has_dockerfile": has_dockerfile,
        "has_compose": has_compose,
        "detected_services": [],
    }
